#!/usr/bin/env python3
"""
Experimental podcast script generator.

Reads recent threads from the aggregator database and produces a structured
JSON script suitable for text-to-speech rendering, in the style of a neutral
radio news broadcast.

Run:
    uv run python scripts/podcast_generate.py
    uv run python scripts/podcast_generate.py --hours 36 --output my_script.json
    uv run python scripts/podcast_generate.py --dry-run   # list candidates, no LLM calls

Requires OPENAI_API_KEY and DATABASE_URL in the environment (or .env file at the repo root).
"""

from __future__ import annotations

# ── Config ────────────────────────────────────────────────────────────────────
# Model: gpt-5.6-terra via litellm's OpenAI routing. litellm resolves "gpt-*"
# names to OpenAI automatically; use "openai/gpt-5.6-terra" if routing fails.
MODEL = "gpt-5.6-terra"

CANDIDATE_WINDOW_HOURS = 36       # threads updated within this window are candidates
KNOWN_FACTS_RECENT_DAYS = 7       # deltas newer than this are "recent"; older ones are history
MAX_TURNS_PER_THREAD = 10         # tool-use loop cap per segment (generous; powerful model)
MAX_CANDIDATE_THREADS = 40        # max threads sent to the selection phase
ARTICLES_PER_THREAD_DEFAULT = 6   # default limit for get_thread_articles tool
WORDS_PER_MINUTE = 120            # news reading pace for duration estimate
LLM_MAX_TOKENS = 4096             # per-call output cap
# gpt-5.6-terra does not accept a temperature parameter (only default=1 supported)
OUTPUT_PATH = "podcast_script.json"
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Bootstrap: load .env before importing anything that reads DATABASE_URL.
# aggregator_common.db runs Settings() at import time, so env must be ready.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "aggregator-common" / "src"))

from aggregator_common.env import load_env
load_env()

import os
from contextlib import contextmanager
from typing import Generator

import litellm  # noqa: E402 — must come after load_env()

# Explicitly wire the API key — litellm may not see env vars set by load_env()
# because python-dotenv loads into os.environ after litellm's own startup scan.
# Also accepts OPENAPI_API_KEY (common typo) so the run doesn't fail silently.
_openai_api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAPI_API_KEY", "")
if not _openai_api_key:
    sys.exit("OPENAI_API_KEY is not set. Add it to .env or export it before running.")
litellm.openai_key = _openai_api_key
os.environ["OPENAI_API_KEY"] = _openai_api_key  # ensure sublibraries also see it
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from aggregator_common.models import Article, InterestProfile, Thread, ThreadMembership

# Build the DB session factory directly so this standalone script does not
# trigger aggregator_common.db's module-level Settings() construction, which
# requires DATABASE_URL to already be in the environment at import time.
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not _DATABASE_URL:
    sys.exit(
        "DATABASE_URL is not set.\n"
        "Create a .env file at the repo root with DATABASE_URL=postgresql://... "
        "or export it before running."
    )
# Ensure psycopg3 driver (matches aggregator_common.db convention)
for _prefix in ("postgresql://", "postgres://"):
    if _DATABASE_URL.startswith(_prefix):
        _DATABASE_URL = "postgresql+psycopg://" + _DATABASE_URL[len(_prefix):]
        break

_engine = create_engine(_DATABASE_URL, pool_pre_ping=True)
_SessionFactory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Telemetry ─────────────────────────────────────────────────────────────────

@dataclass
class ThreadTelemetry:
    thread_id: int
    turns: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ScriptTelemetry:
    threads_selected: int = 0
    threads_written: int = 0
    selection_turns: int = 0
    selection_tokens: int = 0
    wrapper_tokens: int = 0
    elapsed_seconds: float = 0.0
    per_thread: list[ThreadTelemetry] = field(default_factory=list)

    @property
    def total_turns(self) -> int:
        return self.selection_turns + sum(t.turns for t in self.per_thread)

    @property
    def total_tool_calls(self) -> int:
        return sum(t.tool_calls for t in self.per_thread)

    @property
    def total_prompt_tokens(self) -> int:
        return self.selection_tokens + self.wrapper_tokens + sum(t.prompt_tokens for t in self.per_thread)

    @property
    def total_completion_tokens(self) -> int:
        return sum(t.completion_tokens for t in self.per_thread)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _isoformat(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


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
            Thread.surfaced == True,
            Thread.dismissed == False,
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


def _db_get_thread_details(session: Session, thread_id: int) -> dict:
    thread = session.get(Thread, thread_id)
    if thread is None:
        return {"error": f"Thread {thread_id} not found"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=KNOWN_FACTS_RECENT_DAYS)

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

    # Count non-suppressed members without loading all of them
    member_count = session.scalar(
        select(func.count())
        .select_from(ThreadMembership)
        .where(ThreadMembership.thread_id == thread_id, ThreadMembership.suppressed == False)
    ) or 0

    days_old = (datetime.now(timezone.utc) - thread.first_seen.replace(tzinfo=timezone.utc)
                if thread.first_seen.tzinfo is None else
                datetime.now(timezone.utc) - thread.first_seen).days

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
        # Full accumulated knowledge for historical context
        "known_facts": thread.known_facts or [],
        # Timestamped recent additions — what changed in the last N days
        "recent_deltas": recent_deltas,
    }


def _db_get_thread_articles(session: Session, thread_id: int, limit: int) -> list[dict]:
    rows = session.execute(
        select(ThreadMembership, Article)
        .join(Article, ThreadMembership.article_id == Article.id)
        .where(
            ThreadMembership.thread_id == thread_id,
            ThreadMembership.suppressed == False,
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


# ── LLM plumbing ──────────────────────────────────────────────────────────────

def _to_assistant_dict(msg: Any) -> dict:
    d: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return d


def _call_llm(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: Any = "auto",
    response_format: dict | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": LLM_MAX_TOKENS,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    if response_format:
        kwargs["response_format"] = response_format
    return litellm.completion(**kwargs)


def _extract_json(content: str) -> str:
    """Strip markdown fences from LLM JSON responses."""
    raw = content.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        if raw.rstrip().endswith("```"):
            raw = raw[: raw.rfind("```")]
    return raw.strip()


# ── Tool schemas (kept for reference; segment phase uses pre-fetch, not tool calls) ──

_SEGMENT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_thread_details",
            "description": (
                "Retrieve the full context for a thread: rolling narrative summary, "
                "all accumulated known facts (historical context), and recent deltas "
                f"(timestamped developments from the last {KNOWN_FACTS_RECENT_DAYS} days). "
                "Always call this first to understand the full arc of the story."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "integer", "description": "The thread ID to retrieve."},
                },
                "required": ["thread_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_thread_articles",
            "description": (
                "Get the articles belonging to a thread, ordered by importance score descending. "
                "Each entry includes title, source name, publication date, LLM summary, "
                "importance reason, topic tags, and a flag indicating whether a full text "
                "excerpt is available for quoting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "integer", "description": "The thread ID."},
                    "limit": {
                        "type": "integer",
                        "description": f"Max articles to return (default {ARTICLES_PER_THREAD_DEFAULT}, max 10).",
                        "default": ARTICLES_PER_THREAD_DEFAULT,
                    },
                },
                "required": ["thread_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_article_excerpt",
            "description": (
                "Get the full extracted text excerpt of a specific article. "
                "Use this when you want to quote directly — only call it for articles "
                "where has_excerpt=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "article_id": {"type": "integer", "description": "The article ID."},
                },
                "required": ["article_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "emit_segment",
            "description": (
                "Submit the completed podcast segment for this story. "
                "Call this once — and only once — when you have gathered sufficient "
                "context and written the segment text. Calling this ends the loop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "headline": {
                        "type": "string",
                        "description": "Broadcast-style one-sentence headline for this story.",
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "The full spoken segment (150-220 words). Written to be read aloud: "
                            "short sentences, active voice, source-attributed."
                        ),
                    },
                    "topic_category": {
                        "type": "string",
                        "description": (
                            "Broad topic bucket, e.g. 'AI & Technology', 'World Politics', "
                            "'Business & Finance', 'Motorsport', 'Gaming', 'Science', 'Other'."
                        ),
                    },
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Names of the sources cited in this segment.",
                    },
                    "is_developing": {
                        "type": "boolean",
                        "description": (
                            "True if this is an ongoing story with meaningful prior coverage "
                            "(thread older than a few days and has substantial known_facts history)."
                        ),
                    },
                    "tts_hints": {
                        "type": "object",
                        "description": "Hints for the TTS rendering layer.",
                        "properties": {
                            "pause_before": {
                                "type": "string",
                                "enum": ["none", "short", "medium", "long"],
                            },
                            "pace": {
                                "type": "string",
                                "enum": ["slow", "normal", "brisk"],
                            },
                        },
                    },
                },
                "required": ["headline", "text", "topic_category", "sources", "is_developing"],
            },
        },
    },
]


def _dispatch_segment_tool(session: Session, tool_name: str, tool_args: dict) -> Any:
    if tool_name == "get_thread_details":
        return _db_get_thread_details(session, tool_args["thread_id"])
    if tool_name == "get_thread_articles":
        limit = min(tool_args.get("limit", ARTICLES_PER_THREAD_DEFAULT), 10)
        return _db_get_thread_articles(session, tool_args["thread_id"], limit)
    if tool_name == "get_article_excerpt":
        return _db_get_article_excerpt(session, tool_args["article_id"])
    if tool_name == "emit_segment":
        return tool_args  # handled by caller
    raise ValueError(f"Unknown tool: {tool_name!r}")


# ── Phase 1: Story selection ──────────────────────────────────────────────────

_SELECTION_SYSTEM = """\
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


def _run_selection_phase(
    candidates: list[dict],
    interest_profile: str,
    telemetry: ScriptTelemetry,
) -> tuple[str, list[dict]]:
    """Phase 1: Ask the LLM to select and order stories. Returns (episode_theme, selected_list)."""

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
        {"role": "system", "content": _SELECTION_SYSTEM},
        {"role": "user", "content": seed + "\n\nReturn your selection as JSON."},
    ]

    response = _call_llm(messages, response_format={"type": "json_object"})
    telemetry.selection_turns = 1
    usage = getattr(response, "usage", None)
    if usage:
        telemetry.selection_tokens = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)

    content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(_extract_json(content))
        episode_theme = parsed.get("episode_theme", "")
        selected = parsed.get("selected", [])
        return episode_theme, selected
    except json.JSONDecodeError as exc:
        print(f"[warn] Selection JSON parse failed ({exc}); falling back to top-7.", file=sys.stderr)
        return "", [{"thread_id": c["thread_id"], "rationale": ""} for c in candidates[:7]]


# ── Phase 2: Segment generation ───────────────────────────────────────────────
# gpt-5.6-terra is a reasoning model: function tools are not supported in
# /v1/chat/completions when reasoning_effort is set. We pre-fetch all context
# in Python and pass it in one rich prompt, letting the model reason over the
# complete picture — which is actually the better fit for a reasoning model.

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
    """Build the rich text context passed to the LLM for segment generation."""
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
                f"## Recent developments (last {KNOWN_FACTS_RECENT_DAYS} days, timestamped)\n\n"
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


def _run_segment_phase(
    session: Session,
    thread_id: int,
    rationale: str,
    telemetry: ScriptTelemetry,
) -> dict | None:
    """Phase 2: Pre-fetch all context, single LLM call per thread. Returns segment payload or None."""

    t = ThreadTelemetry(thread_id=thread_id)
    telemetry.per_thread.append(t)

    # Pre-fetch all context in Python
    thread = _db_get_thread_details(session, thread_id)
    if "error" in thread:
        print(f"[warn] Thread {thread_id}: {thread['error']}", file=sys.stderr)
        return None

    articles = _db_get_thread_articles(session, thread_id, ARTICLES_PER_THREAD_DEFAULT)

    # Grab excerpts for the top articles that have them (up to 3 for quotability)
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

    response = _call_llm(messages, response_format={"type": "json_object"})
    t.turns = 1
    t.tool_calls = 0
    usage = getattr(response, "usage", None)
    if usage:
        t.prompt_tokens = usage.prompt_tokens or 0
        t.completion_tokens = usage.completion_tokens or 0

    content = response.choices[0].message.content or ""
    try:
        return json.loads(_extract_json(content))
    except json.JSONDecodeError as exc:
        print(f"[warn] Thread {thread_id}: segment JSON parse failed ({exc}).", file=sys.stderr)
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


def _generate_wrapper(
    date_str: str,
    episode_theme: str,
    segments: list[dict],
    telemetry: ScriptTelemetry,
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

    response = _call_llm(messages, response_format={"type": "json_object"})
    usage = getattr(response, "usage", None)
    if usage:
        telemetry.wrapper_tokens = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)

    content = response.choices[0].message.content or ""
    try:
        wrapper = json.loads(_extract_json(content))
        # Ensure transition array is the right length
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
    episode_theme: str,
    segments: list[dict],
    wrapper: dict,
    telemetry: ScriptTelemetry,
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
    duration_seconds = int(total_words / WORDS_PER_MINUTE * 60)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": date_str,
        "episode_theme": episode_theme,
        "model": MODEL,
        "duration_estimate_seconds": duration_seconds,
        "segments": output_segments,
        "telemetry": {
            "total_turns": telemetry.total_turns,
            "total_tool_calls": telemetry.total_tool_calls,
            "total_prompt_tokens": telemetry.total_prompt_tokens,
            "total_completion_tokens": telemetry.total_completion_tokens,
            "threads_selected": telemetry.threads_selected,
            "threads_written": telemetry.threads_written,
            "elapsed_seconds": round(telemetry.elapsed_seconds, 1),
            "per_thread": [
                {
                    "thread_id": t.thread_id,
                    "turns": t.turns,
                    "tool_calls": t.tool_calls,
                    "prompt_tokens": t.prompt_tokens,
                    "completion_tokens": t.completion_tokens,
                }
                for t in telemetry.per_thread
            ],
        },
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a podcast script from recent aggregator threads."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=CANDIDATE_WINDOW_HOURS,
        help=f"Candidate window in hours (default: {CANDIDATE_WINDOW_HOURS})",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_PATH,
        help=f"Output JSON file (default: {OUTPUT_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidate threads and exit without calling the LLM.",
    )
    args = parser.parse_args()

    start = time.monotonic()
    date_str = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y")
    telemetry = ScriptTelemetry()

    print(f"[podcast] model={MODEL}  candidate_window={args.hours}h", flush=True)
    print(f"[podcast] Fetching candidates...", flush=True)

    with get_session() as session:
        interest_profile = _get_interest_profile(session)
        candidates = _get_candidate_threads(session, args.hours, MAX_CANDIDATE_THREADS)

    print(f"[podcast] {len(candidates)} candidate thread(s).", flush=True)

    if args.dry_run:
        print(f"\n{'grade':>5}  {'id':>7}  {'updated':>10}  {'age':>3}  title")
        print("-" * 80)
        for c in candidates:
            age_flag = "NEW" if c["first_seen"] == c["last_updated"] else f"{c['first_seen']}"
            print(
                f"{c['top_grade'] or '—':>5}  {c['thread_id']:>7}  "
                f"{c['last_updated']:>10}  {age_flag:>10}  {c['title'][:60]}"
            )
        return

    if not candidates:
        print("[podcast] No surfaced threads found in the candidate window. Nothing to generate.", file=sys.stderr)
        sys.exit(1)

    # ── Phase 1 ──
    print(f"[podcast] Phase 1: selecting stories from {len(candidates)} candidates...", flush=True)
    episode_theme, selected = _run_selection_phase(candidates, interest_profile, telemetry)
    telemetry.threads_selected = len(selected)
    print(
        f"[podcast] Selected {len(selected)} story/stories. "
        f"Theme: {episode_theme or '(none)'}",
        flush=True,
    )

    # ── Phase 2 ──
    segments: list[dict] = []
    with get_session() as session:
        for item in selected:
            thread_id = item.get("thread_id")
            rationale = item.get("rationale", "")
            if not thread_id:
                continue
            title_hint = next(
                (c["title"][:60] for c in candidates if c["thread_id"] == thread_id), str(thread_id)
            )
            print(f"[podcast] Writing segment: {title_hint}...", flush=True)
            payload = _run_segment_phase(session, int(thread_id), rationale, telemetry)
            if payload:
                payload["thread_id"] = thread_id
                segments.append(payload)
                telemetry.threads_written += 1
                t = telemetry.per_thread[-1]
                print(
                    f"[podcast]   ✓ turns={t.turns} tool_calls={t.tool_calls} "
                    f"tokens={t.prompt_tokens + t.completion_tokens}",
                    flush=True,
                )
            else:
                print(f"[warn] Thread {thread_id} produced no segment.", file=sys.stderr)

    print(f"[podcast] {telemetry.threads_written} segment(s) written.", flush=True)

    if not segments:
        print("[podcast] No segments generated — aborting.", file=sys.stderr)
        sys.exit(1)

    # ── Phase 3 ──
    print("[podcast] Phase 3: generating intro, transitions, outro...", flush=True)
    wrapper = _generate_wrapper(date_str, episode_theme, segments, telemetry)

    # ── Assembly ──
    telemetry.elapsed_seconds = time.monotonic() - start
    script = _assemble_script(date_str, episode_theme, segments, wrapper, telemetry)

    output_path = Path(args.output)
    output_path.write_text(json.dumps(script, indent=2, ensure_ascii=False))

    mins, secs = divmod(script["duration_estimate_seconds"], 60)
    print(
        f"\n[podcast] Done in {telemetry.elapsed_seconds:.1f}s.\n"
        f"  Stories:    {telemetry.threads_written}\n"
        f"  Duration:   ~{mins}m{secs:02d}s\n"
        f"  Turns:      {telemetry.total_turns} "
        f"(selection={telemetry.selection_turns} segments={telemetry.total_turns - telemetry.selection_turns})\n"
        f"  Tool calls: {telemetry.total_tool_calls}\n"
        f"  Tokens:     {telemetry.total_prompt_tokens + telemetry.total_completion_tokens:,} "
        f"(in={telemetry.total_prompt_tokens:,} out={telemetry.total_completion_tokens:,})\n"
        f"  Output:     {output_path.resolve()}",
        flush=True,
    )


if __name__ == "__main__":
    main()
