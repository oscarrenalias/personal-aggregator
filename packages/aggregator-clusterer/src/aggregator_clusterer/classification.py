from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import litellm
from sqlalchemy import select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, ClassificationLabel, Thread
from aggregator_clusterer.candidates import CandidateMatch
from aggregator_clusterer.config import ClustererSettings

logger = logging.getLogger(__name__)


def is_section_title_blocked(article: Article, settings: "ClustererSettings") -> bool:
    """Return True if the article title is a generic section/index heading.

    Checks exact match against the configured blocklist, then a heuristic for
    very short titles whose every word appears in a blocklist phrase.  Articles
    that match should not seed or join a thread.
    """
    raw = article.clean_title or article.feed_title or ""
    normalized = raw.lower().strip()
    if not normalized:
        return False

    blocked_phrases = {entry.lower().strip() for entry in settings.clusterer_section_title_blocklist}

    if normalized in blocked_phrases:
        logger.debug(
            "Section-title guard fired (exact match) article_id=%s title=%r",
            article.id,
            raw,
        )
        return True

    # Heuristic: short titles (≤ 3 words) where every word appears in a blocked phrase.
    words = normalized.split()
    if 1 <= len(words) <= 3:
        blocklist_words: set[str] = set()
        for phrase in blocked_phrases:
            blocklist_words.update(phrase.split())
        if all(w in blocklist_words for w in words):
            logger.debug(
                "Section-title guard fired (heuristic) article_id=%s title=%r",
                article.id,
                raw,
            )
            return True

    return False


_LABEL_VALUES = ", ".join(f'"{lbl.value}"' for lbl in ClassificationLabel)

_SYSTEM_PROMPT = """\
You are a news thread classifier. Given an article and the most relevant existing thread, decide how to classify the article.

Respond with a JSON object only — no markdown, no commentary. Required fields:
- "label": one of [{label_values}]
- "thread_id": the integer thread id if the article belongs to an existing thread, or null if it starts a new one
- "confidence": a float between 0.0 and 1.0
- "new_facts": a list of strings (may be empty) — concrete new facts the article adds to the thread
- "reason": a brief explanation of the classification

Label semantics:
- new_thread: article starts a distinct new story with no existing thread
- same_thread_new_fact: article adds concrete new information to the thread (thread_id required)
- same_thread_new_angle: article covers the same story from a new perspective (thread_id required)
- same_thread_duplicate: article is substantially the same as existing thread content (thread_id required)
- same_thread_background_only: article provides only background/context without new developments (thread_id required)
- correction_or_clarification: article corrects or clarifies prior reporting in the thread (thread_id required)
- related_new_thread: article is related but distinct enough to warrant a new thread
- irrelevant_or_low_value: article is off-topic or adds no value

Set thread_id to null when label is new_thread or related_new_thread.
""".format(label_values=_LABEL_VALUES)


def _build_user_message(
    article: Article,
    top_candidate: Optional[CandidateMatch],
    session: Session,
) -> str:
    title = article.clean_title or article.feed_title or "(no title)"
    summary = article.summary or "(no summary)"

    topics = article.topics
    if isinstance(topics, list):
        topics_str = ", ".join(str(t) for t in topics) if topics else "(none)"
    elif isinstance(topics, dict):
        topics_str = ", ".join(topics.keys()) if topics else "(none)"
    else:
        topics_str = "(none)"

    entities = article.entities
    if isinstance(entities, list):
        entities_str = ", ".join(str(e) for e in entities) if entities else "(none)"
    elif isinstance(entities, dict):
        entities_str = ", ".join(entities.keys()) if entities else "(none)"
    else:
        entities_str = "(none)"

    lines = [
        "## Article",
        f"Title: {title}",
        f"Summary: {summary}",
        f"Topics: {topics_str}",
        f"Entities: {entities_str}",
    ]

    if top_candidate is not None:
        thread = session.execute(
            select(Thread).where(Thread.id == top_candidate.thread_id)
        ).scalar_one_or_none()

        if thread is not None:
            lines.append("")
            lines.append("## Best candidate thread")
            lines.append(f"thread_id: {thread.id}")
            lines.append(f"Title: {thread.representative_title}")
            lines.append(f"Summary: {thread.rolling_summary or '(none)'}")
            known_facts = thread.known_facts or []
            if known_facts:
                facts_str = "; ".join(str(f) for f in known_facts)
                lines.append(f"Known facts: {facts_str}")
            else:
                lines.append("Known facts: (none)")
            lines.append(f"Candidate score: {top_candidate.composite_score:.3f}")
        else:
            lines.append("")
            lines.append("## Best candidate thread")
            lines.append("(thread not found — classify as new_thread)")
    else:
        lines.append("")
        lines.append("## Best candidate thread")
        lines.append("(no candidates — classify as new_thread)")

    return "\n".join(lines)


def _error_result() -> "ClassificationResult":
    return ClassificationResult(
        label=ClassificationLabel.new_thread,
        thread_id=None,
        confidence=0.0,
        new_facts=[],
        reason="classification_error",
    )


@dataclass
class ClassificationResult:
    label: ClassificationLabel
    thread_id: Optional[int]
    confidence: float
    new_facts: list[str] = field(default_factory=list)
    reason: str = ""


def classify_article(
    article: Article,
    candidates: list[CandidateMatch],
    session: Session,
    settings: ClustererSettings,
) -> ClassificationResult:
    top_candidate = candidates[0] if candidates else None
    user_message = _build_user_message(article, top_candidate, session)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    try:
        response = litellm.completion(
            model=settings.clusterer_llm_model,
            messages=messages,
            max_tokens=settings.clusterer_llm_max_output_tokens,
            temperature=settings.clusterer_llm_temperature,
            timeout=settings.clusterer_llm_timeout_seconds,
        )
    except Exception as exc:
        logger.error("LLM call failed during article classification (article_id=%s): %s", article.id, exc)
        return _error_result()

    # Log token usage
    usage = getattr(response, "usage", None)
    if usage is not None:
        logger.debug(
            "classification token usage article_id=%s model=%s prompt_tokens=%s completion_tokens=%s",
            article.id,
            getattr(response, "model", settings.clusterer_llm_model),
            getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
        )

    content = response.choices[0].message.content
    if not content:
        logger.error("Empty LLM response for article_id=%s", article.id)
        return _error_result()

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("JSON parse error classifying article_id=%s: %s — raw: %r", article.id, exc, content[:200])
        return _error_result()

    try:
        label = ClassificationLabel(data["label"])
    except (KeyError, ValueError) as exc:
        logger.error("Invalid label in classification response for article_id=%s: %s", article.id, exc)
        return _error_result()

    # thread_id must be None for labels that don't attach to an existing thread
    raw_thread_id = data.get("thread_id")
    if label in (ClassificationLabel.new_thread, ClassificationLabel.related_new_thread):
        thread_id: Optional[int] = None
    else:
        thread_id = int(raw_thread_id) if raw_thread_id is not None else None

    try:
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    raw_facts = data.get("new_facts", [])
    new_facts = [str(f) for f in raw_facts] if isinstance(raw_facts, list) else []

    reason = str(data.get("reason", ""))

    return ClassificationResult(
        label=label,
        thread_id=thread_id,
        confidence=confidence,
        new_facts=new_facts,
        reason=reason,
    )
