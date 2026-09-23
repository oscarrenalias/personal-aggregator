"""Podcast script generation — ported from scripts/podcast_generate.py.

Phase 1 (this module): story selection and continuity context assembly.
Phase 2 (segment writing) and Phase 3 (wrapper) will be added in subsequent beads.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import litellm
from sqlalchemy import select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, InterestProfile, Thread, ThreadMembership
from aggregator_common.queries import get_recent_podcast_episodes
from aggregator_podcast.config import PodcastSettings

log = logging.getLogger(__name__)

_MAX_CANDIDATE_THREADS = 40
_SELECTION_SYSTEM_BASE = """\
You are the editor of a daily spoken news podcast. Your listener is an informed adult who cares \
about technology, AI, software, global politics, motorsport, and gaming — but this is not a niche \
show. It is a balanced, well-rounded daily briefing that reflects the actual shape of the news day.

Your task: given the candidate story threads below, select and order the stories for today's episode. \
Target 7 to 12 minutes of spoken content (roughly 5-9 stories at about 180 words each).

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
    system_prompt = _SELECTION_SYSTEM_BASE + continuity_block

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
