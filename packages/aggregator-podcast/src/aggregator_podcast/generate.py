"""Podcast script generation — ported from scripts/podcast_generate.py.

Phase 1: story selection and continuity context assembly.
Phase 2: per-segment LLM script writing for each selected thread.
Phase 3: intro, transitions, and outro LLM call.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import litellm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, InterestProfile, PodcastEpisode, Thread, ThreadMembership
from aggregator_common.queries import get_recent_podcast_episodes
from aggregator_podcast.config import PodcastSettings

log = logging.getLogger(__name__)

_MAX_CANDIDATE_THREADS = 40
_ARTICLES_PER_THREAD = 6
_KNOWN_FACTS_RECENT_DAYS = 7
_WORDS_PER_MINUTE = 120
_SELECTION_SYSTEM_BASE = """\
You are the editor of a daily spoken news podcast. Your listener is an informed adult who cares \
about technology, AI, software, global politics, motorsport, and gaming — but this is not a niche \
show. It is a balanced, well-rounded daily briefing that reflects the actual shape of the news day.

Your task: given the candidate story threads below, select and order the stories for today's episode. \
Target {max_stories} stories at about 180 words each.

Priorities:
- Genuine newsworthiness comes first — pick stories that matter, regardless of the interest profile.
- Balance across topics — do not let any one subject dominate unless the day's news genuinely warrants it.
- Prefer stories with meaningful developments in the last 24-48 hours.
- Prefer threads that have prior coverage (older threads with history make richer segments with context).
- Order stories for a natural broadcast flow: lead with the most impactful story, group related \
  topics, avoid jarring topic jumps.

Return a JSON object in exactly this format (no other text):
{
  "episode_theme": "<one sentence summarising today's dominant theme, or empty string if none>",
  "selected": [
    {"thread_id": <int>, "rationale": "<one sentence explaining why this story and its position>"},
    ...
  ]
}\
"""

_CONTINUITY_INSTRUCTION = """\

## Continuity guidance

The recent episodes below show which stories were already covered. When selecting today's stories:
- Skip threads already covered unless there are significant new developments since that episode.
- When re-covering a thread, explicitly reference the prior coverage in your rationale \
  (e.g. "significant update since yesterday's coverage of this thread").\
"""


# ── DB helpers ────────────────────────────────────────────────────────────────

def _fmt_date(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt is not None else "unknown"


def _get_interest_profile(session: Session) -> str:
    row = session.scalar(select(InterestProfile))
    return row.profile_text.strip() if row and row.profile_text else ""


def _get_candidate_threads(session: Session, window_hours: int, limit: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    threads = session.scalars(
        select(Thread)
        .where(
            Thread.status == "active",
            Thread.surfaced == True,  # noqa: E712
            Thread.dismissed == False,  # noqa: E712
            Thread.last_updated >= cutoff,
        )
        .order_by(Thread.top_grade.desc().nulls_last(), Thread.last_updated.desc())
        .limit(limit)
    ).all()

    return [
        {
            "thread_id": t.id,
            "title": t.representative_title,
            "summary": (t.rolling_summary or "")[:400],
            "top_grade": t.top_grade,
            "tier": t.tier,
            "first_seen": _fmt_date(t.first_seen),
            "last_updated": _fmt_date(t.last_updated),
            "source_diversity": round(t.source_diversity or 0, 2),
        }
        for t in threads
    ]


# ── LLM helpers ───────────────────────────────────────────────────────────────

def _extract_json(content: str) -> str:
    """Strip markdown fences from LLM JSON responses."""
    raw = content.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.rstrip().endswith("```"):
            raw = raw[: raw.rfind("```")]
    return raw.strip()


def _call_selection_llm(
    messages: list[dict],
    settings: PodcastSettings,
) -> Any:
    return litellm.completion(
        model=settings.podcast_llm_model,
        messages=messages,
        max_tokens=settings.podcast_llm_max_tokens,
        response_format={"type": "json_object"},
    )


# ── Continuity context ────────────────────────────────────────────────────────

def _build_continuity_block(session: Session, continuity_count: int) -> str:
    """Return a formatted 'Recent episodes' block for the selection prompt, or empty string."""
    if continuity_count <= 0:
        return ""

    recent = get_recent_podcast_episodes(session, continuity_count)
    if not recent:
        return ""

    lines: list[str] = [_CONTINUITY_INSTRUCTION, "\n## Recent episodes\n"]
    for ep in recent:
        script = ep.script_json or {}
        ep_date = ep.date.isoformat() if ep.date else "unknown"
        theme = ep.episode_theme or script.get("episode_theme", "")
        story_segments = [
            s for s in (script.get("segments") or []) if s.get("type") == "story"
        ]
        stories = [
            {"thread_id": s.get("thread_id"), "headline": s.get("headline", ""), "topic_category": s.get("topic_category", "")}
            for s in story_segments
        ]
        lines.append(
            f"Date: {ep_date}  Theme: {theme or '(none)'}\n"
            + "\n".join(
                f"  - thread_id={s['thread_id']} [{s['topic_category']}] {s['headline']}"
                for s in stories
                if s["thread_id"] is not None
            )
        )

    return "\n".join(lines)


# ── Phase 1: Story selection ──────────────────────────────────────────────────

def run_selection_phase(
    session: Session,
    settings: PodcastSettings,
) -> tuple[str, list[dict], list[dict]]:
    """Query candidate threads and call the selection LLM.

    Returns (episode_theme, selected_list, candidates) where:
      - episode_theme is a one-sentence description of the episode's dominant theme.
      - selected_list is the LLM-ordered list of dicts with keys thread_id and rationale.
      - candidates is the full candidate list (needed by Phase 2 for title lookups).
    """
    interest_profile = _get_interest_profile(session)
    candidates = _get_candidate_threads(
        session,
        window_hours=settings.podcast_candidate_window_hours,
        limit=_MAX_CANDIDATE_THREADS,
    )

    if not candidates:
        log.warning("No surfaced threads found in the candidate window.")
        return "", [], []

    continuity_block = _build_continuity_block(session, settings.podcast_continuity_count)
    system_prompt = _SELECTION_SYSTEM_BASE.format(max_stories=settings.podcast_max_stories) + continuity_block

    parts: list[str] = []
    if interest_profile:
        parts.append(f"## Listener interest profile\n\n{interest_profile}")

    lines = [f"## Candidate threads ({len(candidates)} total, sorted by grade desc)\n"]
    for c in candidates:
        age_note = (
            f" [developing since {c['first_seen']}]"
            if c["first_seen"] != c["last_updated"]
            else " [new today]"
        )
        lines.append(
            f"thread_id={c['thread_id']} | grade={c['top_grade']} | tier={c['tier'] or '—'} "
            f"| last_updated={c['last_updated']}{age_note}\n"
            f"Title: {c['title']}\n"
            f"Summary: {c['summary'] or '(no summary yet)'}\n"
        )
    parts.append("\n---\n".join(lines))

    seed = "\n\n".join(parts)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": seed + "\n\nReturn your selection as JSON."},
    ]

    log.info("Phase 1: calling selection LLM with %d candidates.", len(candidates))
    response = _call_selection_llm(messages, settings)
    content = response.choices[0].message.content or ""

    try:
        parsed = json.loads(_extract_json(content))
        episode_theme: str = parsed.get("episode_theme", "")
        selected: list[dict] = parsed.get("selected", [])
        if len(selected) > settings.podcast_max_stories:
            log.info(
                "Phase 1 returned %d stories; truncating to podcast_max_stories=%d.",
                len(selected),
                settings.podcast_max_stories,
            )
            selected = selected[: settings.podcast_max_stories]
        log.info(
            "Phase 1 complete: %d threads selected, theme=%r",
            len(selected),
            episode_theme or "(none)",
        )
        return episode_theme, selected, candidates
    except json.JSONDecodeError as exc:
        log.warning("Selection JSON parse failed (%s); falling back to top-7.", exc)
        fallback = [{"thread_id": c["thread_id"], "rationale": ""} for c in candidates[:7]]
        return "", fallback, candidates


# ── Phase 2: DB helpers for segment context ───────────────────────────────────

def _isoformat(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _db_get_thread_details(session: Session, thread_id: int) -> dict:
    thread = session.get(Thread, thread_id)
    if thread is None:
        return {"error": f"Thread {thread_id} not found"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=_KNOWN_FACTS_RECENT_DAYS)

    all_deltas: list[dict] = thread.deltas or []
    recent_deltas: list[dict] = []
    for d in all_deltas:
        ts_str = d.get("timestamp")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts >= cutoff:
                    recent_deltas.append(d)
            except ValueError:
                pass

    member_count = session.scalar(
        select(func.count())
        .select_from(ThreadMembership)
        .where(ThreadMembership.thread_id == thread_id, ThreadMembership.suppressed == False)  # noqa: E712
    ) or 0

    first_seen = thread.first_seen
    if first_seen is not None and first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)
    days_old = (datetime.now(timezone.utc) - first_seen).days if first_seen is not None else 0

    return {
        "thread_id": thread.id,
        "title": thread.representative_title,
        "rolling_summary": thread.rolling_summary or "",
        "first_seen": _isoformat(thread.first_seen),
        "last_updated": _isoformat(thread.last_updated),
        "days_old": days_old,
        "tier": thread.tier,
        "top_grade": thread.top_grade,
        "source_diversity": thread.source_diversity,
        "member_count": member_count,
        "known_facts": thread.known_facts or [],
        "recent_deltas": recent_deltas,
    }


def _db_get_thread_articles(session: Session, thread_id: int, limit: int) -> list[dict]:
    rows = session.execute(
        select(ThreadMembership, Article)
        .join(Article, ThreadMembership.article_id == Article.id)
        .where(
            ThreadMembership.thread_id == thread_id,
            ThreadMembership.suppressed == False,  # noqa: E712
        )
        .order_by(Article.importance_score.desc().nulls_last())
        .limit(limit)
    ).all()

    return [
        {
            "article_id": a.id,
            "title": a.clean_title or a.feed_title or "",
            "source": a.feed_title or "",
            "published_at": _isoformat(a.published_at or a.feed_published_at),
            "importance_score": a.importance_score,
            "importance_reason": a.importance_reason or "",
            "summary": a.summary or "",
            "topics": a.topics or [],
            "has_excerpt": bool(a.excerpt),
            "new_facts": m.new_facts or [],
        }
        for m, a in rows
    ]


def _db_get_article_excerpt(session: Session, article_id: int) -> dict:
    a = session.get(Article, article_id)
    if a is None:
        return {"error": f"Article {article_id} not found"}
    return {
        "article_id": a.id,
        "title": a.clean_title or a.feed_title or "",
        "source": a.feed_title or "",
        "author": a.author or "",
        "published_at": _isoformat(a.published_at or a.feed_published_at),
        "excerpt": a.excerpt or "",
    }


# ── Phase 2: Segment generation ───────────────────────────────────────────────

_SEGMENT_SYSTEM = """\
You are a radio journalist writing one segment of a daily news podcast. Write in a neutral, \
authoritative broadcast style — factual, precise, well-attributed. No opinion, no editorialising.

You will receive the full context for the story: a rolling narrative summary, all known facts \
accumulated over the thread's lifetime, recent developments with timestamps, and the full text \
of the most important articles including any available excerpts.

Craft each segment like this:
1. A strong hook that orients the listener immediately. Avoid generic meta-openers like \
   "Today we look at...", "In this segment...", or "Welcome back." — jump straight to the \
   substance of the story.
2. Source attribution woven naturally: "according to the Financial Times", \
   "Techmeme links to a Bloomberg report", "Politico reports that", "as Bloomberg notes".
3. Concrete specifics — numbers, names, dates, percentages — from the source material. \
   Avoid vague generalities.
4. Historical arc when the thread has meaningful prior history: "this is the latest chapter \
   in a story that began in [date], when..." — draw from the known_facts list. If the thread \
   is new today, skip the historical framing.
5. A direct quote only when you have the actual text in an excerpt. Introduce it fully: \
   "[Name], [title], told [outlet]: '...'"  Never paraphrase as if it were a direct quote.
6. 150-220 words of spoken text. Short sentences. Active voice. No bullet points.

Return a JSON object in exactly this format:
{
  "headline": "<broadcast-style one-sentence headline>",
  "text": "<the full spoken segment, 150-220 words>",
  "topic_category": "<e.g. AI & Technology, World Politics, Business & Finance, Motorsport, Gaming, Science, Other>",
  "sources": ["<source name>", ...],
  "is_developing": <true if thread has substantial prior history, false if new today>,
  "tts_hints": {
    "pause_before": "<none|short|medium|long>",
    "pace": "<slow|normal|brisk>"
  }
}\
"""


def _build_segment_context(
    thread: dict,
    articles: list[dict],
    excerpts: list[dict],
    rationale: str,
) -> str:
    parts: list[str] = []

    parts.append(
        f"## Thread: {thread['title']}\n"
        f"Thread ID: {thread['thread_id']} | "
        f"Days old: {thread['days_old']} | "
        f"Grade: {thread['top_grade']} | "
        f"Members: {thread['member_count']}\n"
        f"First seen: {thread['first_seen']} | Last updated: {thread['last_updated']}\n\n"
        f"**Editorial context:** {rationale}"
    )

    if thread.get("rolling_summary"):
        parts.append(f"## Rolling narrative summary\n\n{thread['rolling_summary']}")

    known_facts: list = thread.get("known_facts") or []
    if known_facts:
        facts_text = "\n".join(f"- {f}" for f in known_facts)
        parts.append(f"## All known facts (accumulated history — oldest to newest)\n\n{facts_text}")

    recent_deltas: list = thread.get("recent_deltas") or []
    if recent_deltas:
        delta_lines = []
        for d in recent_deltas:
            ts = d.get("timestamp", "")[:10]
            dtype = d.get("type", d.get("label", ""))
            if dtype == "merge":
                delta_lines.append(f"[{ts}] Thread merge (absorbed thread {d.get('absorbed_id')})")
            else:
                for fact in d.get("new_facts") or []:
                    delta_lines.append(f"[{ts}] {fact}")
        if delta_lines:
            parts.append(
                f"## Recent developments (last {_KNOWN_FACTS_RECENT_DAYS} days, timestamped)\n\n"
                + "\n".join(delta_lines)
            )

    if articles:
        article_lines = []
        for a in articles:
            pub = (a.get("published_at") or "")[:10]
            score = a.get("importance_score", "?")
            article_lines.append(
                f"### [{score}] {a['title']} — {a['source']} ({pub})\n"
                f"Summary: {a.get('summary') or '(no summary)'}\n"
                f"Importance: {a.get('importance_reason') or '(none)'}\n"
                f"Topics: {', '.join(str(t) for t in (a.get('topics') or []))}\n"
                f"Article ID: {a['article_id']} | Has excerpt: {a.get('has_excerpt', False)}"
            )
        parts.append("## Source articles (by importance score, highest first)\n\n" + "\n\n".join(article_lines))

    if excerpts:
        exc_lines = []
        for e in excerpts:
            exc_lines.append(
                f"### Excerpt: {e['title']} — {e['source']} ({(e.get('published_at') or '')[:10]})\n"
                f"Author: {e.get('author') or 'unknown'}\n\n"
                f"{e.get('excerpt') or '(empty)'}"
            )
        parts.append("## Full article excerpts (available for direct quotation)\n\n" + "\n\n---\n\n".join(exc_lines))

    return "\n\n".join(parts)


def run_segment_phase(
    session: Session,
    thread_id: int,
    rationale: str,
    settings: PodcastSettings,
) -> dict | None:
    """Phase 2: Pre-fetch all context, single LLM call per thread. Returns segment payload or None."""
    thread = _db_get_thread_details(session, thread_id)
    if "error" in thread:
        log.warning("Thread %d: %s", thread_id, thread["error"])
        return None

    articles = _db_get_thread_articles(session, thread_id, _ARTICLES_PER_THREAD)

    excerpts: list[dict] = []
    for a in articles[:4]:
        if a.get("has_excerpt") and len(excerpts) < 3:
            exc = _db_get_article_excerpt(session, a["article_id"])
            if exc.get("excerpt"):
                excerpts.append(exc)

    context = _build_segment_context(thread, articles, excerpts, rationale)

    messages: list[dict] = [
        {"role": "system", "content": _SEGMENT_SYSTEM},
        {"role": "user", "content": context + "\n\nWrite the podcast segment and return it as JSON."},
    ]

    log.info("Phase 2: writing segment for thread %d.", thread_id)
    response = litellm.completion(
        model=settings.podcast_llm_model,
        messages=messages,
        max_tokens=settings.podcast_llm_max_tokens,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""
    try:
        return json.loads(_extract_json(content))
    except json.JSONDecodeError as exc:
        log.warning("Thread %d: segment JSON parse failed (%s).", thread_id, exc)
        return None


# ── Phase 3: Wrapper (intro, transitions, outro) ──────────────────────────────

_WRAPPER_SYSTEM = """\
You are a radio producer finishing a news podcast script. You have the ordered list of story \
segments. Write:

1. intro — 2-3 sentences: today's date spoken naturally, a brief tease of the top story or \
   the episode's overall theme. Do not list every story. Conversational but professional.
2. transitions — ALWAYS one per consecutive segment pair, never empty. These are the spoken \
   bridges that separate stories and signal a new topic is starting. Every listener needs to hear \
   a clear cue when one story ends and the next begins — even when adjacent stories share a category. \
   Vary the phrasing naturally and creatively; avoid repeating the same phrase more than once. \
   Draw from the full palette of broadcast connectors: "In other news...", "Moving on now...", \
   "Meanwhile...", "On a lighter note...", "Switching gears...", "Closer to home...", \
   "On the technology front...", "Now for something a bit different...", "Turning to...", \
   "Back in the world of...", "Also making headlines today...", "And in a story that caught our eye..." \
   — or invent something that fits the mood of the two adjacent stories. A transition from a \
   conflict story into a tech story should feel different from one between two tech stories. \
   Keep each transition to one sentence, 8-15 words.
3. outro — 1-2 sentences. Clean sign-off. No "have a great day" fluff.

Return a JSON object in exactly this format (no other text):
{
  "intro": "<intro text>",
  "transitions": ["<between seg 0 and 1>", "<between seg 1 and 2>", ...],
  "outro": "<outro text>"
}
The transitions array must have exactly (N-1) entries where N is the number of segments.\
"""


def run_wrapper_phase(
    date_str: str,
    episode_theme: str,
    segments: list[dict],
    settings: PodcastSettings,
) -> dict:
    """Phase 3: Generate intro, per-transition sentences, and outro."""
    seg_lines = [
        f"Segment {i + 1}: [{s.get('topic_category', '?')}] {s.get('headline', '')}"
        for i, s in enumerate(segments)
    ]

    user_content = (
        f"Date: {date_str}\n"
        f"Episode theme: {episode_theme or '(no dominant theme)'}\n"
        f"Number of segments: {len(segments)}\n\n"
        "Ordered segments:\n" + "\n".join(seg_lines)
        + "\n\nReturn JSON."
    )

    messages = [
        {"role": "system", "content": _WRAPPER_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    log.info("Phase 3: generating intro/transitions/outro for %d segments.", len(segments))
    response = litellm.completion(
        model=settings.podcast_llm_model,
        messages=messages,
        max_tokens=settings.podcast_llm_max_tokens,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or ""
    try:
        wrapper = json.loads(_extract_json(content))
        expected = max(0, len(segments) - 1)
        transitions = wrapper.get("transitions", [])
        if len(transitions) < expected:
            transitions += [""] * (expected - len(transitions))
        wrapper["transitions"] = transitions[:expected]
        return wrapper
    except json.JSONDecodeError:
        return {
            "intro": f"Here is your news briefing for {date_str}.",
            "transitions": [""] * max(0, len(segments) - 1),
            "outro": "That's all for today.",
        }


# ── Assembly ──────────────────────────────────────────────────────────────────

def _assemble_script(
    date_str: str,
    iso_date: str,
    episode_theme: str,
    segments: list[dict],
    wrapper: dict,
    model: str,
) -> dict:
    output_segments: list[dict] = []

    output_segments.append({
        "type": "intro",
        "text": wrapper.get("intro", ""),
        "tts_hints": {"pause_before": "none", "pace": "normal"},
    })

    transitions = wrapper.get("transitions", [])

    for i, seg in enumerate(segments):
        if i > 0:
            transition_text = transitions[i - 1] if i - 1 < len(transitions) else ""
            if transition_text:
                output_segments.append({
                    "type": "transition",
                    "text": transition_text,
                    "tts_hints": {"pause_before": "medium", "pace": "normal"},
                })

        output_segments.append({
            "type": "story",
            "thread_id": seg.get("thread_id"),
            "headline": seg.get("headline", ""),
            "topic_category": seg.get("topic_category", ""),
            "text": seg.get("text", ""),
            "tts_hints": seg.get("tts_hints") or {"pause_before": "short", "pace": "normal"},
            "sources": seg.get("sources", []),
            "is_developing": seg.get("is_developing", False),
        })

    output_segments.append({
        "type": "outro",
        "text": wrapper.get("outro", ""),
        "tts_hints": {"pause_before": "long", "pace": "slow"},
    })

    total_words = sum(
        len(s.get("text", "").split())
        for s in output_segments
        if s["type"] in ("intro", "story", "transition", "outro")
    )
    duration_seconds = int(total_words / _WORDS_PER_MINUTE * 60)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date_str,
        "iso_date": iso_date,
        "episode_theme": episode_theme,
        "model": model,
        "duration_estimate_seconds": duration_seconds,
        "segments": output_segments,
    }


# ── Top-level orchestrator ────────────────────────────────────────────────────

def generate_podcast(
    episode: PodcastEpisode,
    settings: PodcastSettings,
    session: Session,
) -> tuple[dict, str]:
    """Orchestrate Phases 1–3 and return (script_json, audio_path).

    audio_path is a placeholder string; actual audio synthesis is handled by the TTS phase.
    """
    date_str = episode.date.strftime("%A, %B %-d, %Y") if hasattr(episode.date, "strftime") else str(episode.date)
    iso_date = episode.date.isoformat() if hasattr(episode.date, "isoformat") else str(episode.date)

    # Phase 1: story selection
    episode_theme, selected, candidates = run_selection_phase(session, settings)
    if not selected:
        raise ValueError("Phase 1 produced no selected threads — cannot generate episode.")

    log.info(
        "Phase 1 complete: %d thread(s) selected, theme=%r",
        len(selected),
        episode_theme or "(none)",
    )

    # Phase 2: per-segment script writing
    segments: list[dict] = []
    for item in selected:
        thread_id = item.get("thread_id")
        rationale = item.get("rationale", "")
        if not thread_id:
            continue
        payload = run_segment_phase(session, int(thread_id), rationale, settings)
        if payload:
            payload["thread_id"] = thread_id
            segments.append(payload)
        else:
            log.warning("Thread %s produced no segment; skipping.", thread_id)

    if not segments:
        raise ValueError("Phase 2 produced no segments — cannot generate episode.")

    log.info("Phase 2 complete: %d segment(s) written.", len(segments))

    # Phase 3: intro, transitions, outro
    wrapper = run_wrapper_phase(date_str, episode_theme, segments, settings)
    log.info("Phase 3 complete.")

    # Assembly
    script_json = _assemble_script(date_str, iso_date, episode_theme, segments, wrapper, settings.podcast_llm_model)

    # audio_path is a placeholder; TTS synthesis populates it later
    audio_path = ""

    return script_json, audio_path
