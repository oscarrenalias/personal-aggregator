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
You are a news thread classifier. Given an article and a numbered list of candidate threads, decide how to classify the article.

Respond with a JSON object only — no markdown, no commentary. Required fields:
- "label": one of [{label_values}]
- "thread_id": the integer thread_id of the best-matching candidate thread (must be one of the listed thread_ids), or null if the article starts a new thread
- "confidence": a float between 0.0 and 1.0
- "new_facts": a list of strings (may be empty) — concrete new facts the article adds to the thread
- "reason": a brief explanation of the classification
- "thread_title": a concise, complete-phrase headline (≤80 characters, never ending mid-word), neutral and objective.
  new_thread or related_new_thread: synthesize a title from the article — do not copy the headline verbatim.
  same_thread_new_fact or correction_or_clarification: refresh the title to reflect the latest development.
  All other labels: set to null.
  Avoid clickbait, sensationalist framing, and exclamation marks.

Label semantics:
- new_thread: article starts a distinct new story with no existing thread
- same_thread_new_fact: article adds concrete new information to the thread (thread_id required)
- same_thread_new_angle: article covers the same story from a new perspective (thread_id required)
- same_thread_duplicate: article is substantially the same as existing thread content (thread_id required)
- same_thread_background_only: article provides only background/context without new developments (thread_id required)
- correction_or_clarification: article corrects or clarifies prior reporting in the thread (thread_id required)
- related_new_thread: article is related but distinct enough to warrant a new thread
- irrelevant_or_low_value: article is off-topic or adds no value

When the article fits a candidate thread, set thread_id to that thread's integer id (from the listed candidates).
Set thread_id to null when label is new_thread or related_new_thread.
Only use a thread_id from the presented candidate list — do not invent thread ids.
""".format(label_values=_LABEL_VALUES)

_MAX_SUMMARY_CHARS = 400
_MAX_FACTS_CHARS = 300
_TITLE_LIMIT = 80


def _truncate_title(text: str, limit: int = _TITLE_LIMIT) -> str:
    """Truncate to the last whole word within limit, appending '…'. Hard-cuts if no space fits."""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space] + "…"
    return truncated + "…"


def _build_user_message(
    article: Article,
    candidates: list[CandidateMatch],
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

    if candidates:
        candidate_thread_ids = [c.thread_id for c in candidates]
        thread_rows = session.execute(
            select(Thread).where(Thread.id.in_(candidate_thread_ids))
        ).scalars().all()
        threads_by_id: dict[int, Thread] = {t.id: t for t in thread_rows}

        lines.append("")
        lines.append("## Candidate threads")
        lines.append("Return the thread_id of the best match, or null if none fits.")

        for i, candidate in enumerate(candidates, start=1):
            thread = threads_by_id.get(candidate.thread_id)
            if thread is None:
                continue
            rolling_summary = thread.rolling_summary or "(none)"
            if len(rolling_summary) > _MAX_SUMMARY_CHARS:
                rolling_summary = rolling_summary[:_MAX_SUMMARY_CHARS] + "…"
            known_facts = thread.known_facts or []
            if known_facts:
                facts_str = "; ".join(str(f) for f in known_facts)
                if len(facts_str) > _MAX_FACTS_CHARS:
                    facts_str = facts_str[:_MAX_FACTS_CHARS] + "…"
            else:
                facts_str = "(none)"
            lines.append("")
            lines.append(f"### {i}. thread_id={thread.id}: {thread.representative_title}")
            lines.append(f"Summary: {rolling_summary}")
            lines.append(f"Known facts: {facts_str}")
    else:
        lines.append("")
        lines.append("## Candidate threads")
        lines.append("(no candidates — classify as new_thread)")

    return "\n".join(lines)


@dataclass
class ClassificationResult:
    label: ClassificationLabel
    thread_id: Optional[int]
    confidence: float
    new_facts: list[str] = field(default_factory=list)
    reason: str = ""
    thread_title: Optional[str] = None
    is_error: bool = False


def _error_result() -> "ClassificationResult":
    return ClassificationResult(
        label=ClassificationLabel.new_thread,
        thread_id=None,
        confidence=0.0,
        new_facts=[],
        reason="classification_error",
        is_error=True,
    )


def classify_article(
    article: Article,
    candidates: list[CandidateMatch],
    session: Session,
    settings: ClustererSettings,
) -> ClassificationResult:
    max_n = settings.clusterer_max_classifier_candidates
    top_candidates = candidates[:max_n]
    presented_ids: set[int] = {c.thread_id for c in top_candidates}
    user_message = _build_user_message(article, top_candidates, session)

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
            metadata={"service": "clusterer", "operation": "classify", "ref_id": str(article.id)},
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

    # thread_id must be None for labels that don't attach to an existing thread,
    # and must be one of the presented candidate ids to guard against hallucinated ids.
    raw_thread_id = data.get("thread_id")
    if label in (ClassificationLabel.new_thread, ClassificationLabel.related_new_thread):
        thread_id: Optional[int] = None
    else:
        if raw_thread_id is not None:
            tid = int(raw_thread_id)
            if tid in presented_ids:
                thread_id = tid
            else:
                logger.warning(
                    "LLM returned thread_id=%s not in presented candidates %s for article_id=%s; treating as new",
                    tid,
                    presented_ids,
                    article.id,
                )
                thread_id = None
        else:
            thread_id = None

    try:
        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    raw_facts = data.get("new_facts", [])
    new_facts = [str(f) for f in raw_facts] if isinstance(raw_facts, list) else []

    reason = str(data.get("reason", ""))

    raw_title = data.get("thread_title")
    thread_title: Optional[str] = _truncate_title(str(raw_title)) if raw_title else None

    return ClassificationResult(
        label=label,
        thread_id=thread_id,
        confidence=confidence,
        new_facts=new_facts,
        reason=reason,
        thread_title=thread_title,
    )
