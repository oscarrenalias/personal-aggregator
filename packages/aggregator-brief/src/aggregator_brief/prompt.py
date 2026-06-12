from __future__ import annotations

from datetime import datetime

from sqlalchemy import nullslast, select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, InterestProfile

from .config import BriefSettings


_SYSTEM_TEMPLATE = """\
You are the user's personal news analyst. Your job is to produce a structured daily brief \
covering the most important and relevant developments from the past {period_hours} hours.

## User interest profile

{interest_profile}

## Your task

Produce a daily brief with a compelling headline, a short intro paragraph, and up to \
{max_topics} topic sections. For each topic provide:
- **What happened** — a clear, factual narrative of the development.
- **Why it matters to this user** — prioritised against the interest profile above. \
De-emphasise topics the user has marked as low-priority.
- **Historical context** (optional) — if this story follows earlier events, say so. \
Use `search_articles` to look up earlier coverage when you need background.
- **References** — the articles to read. Reference articles by their id from the seed data \
for internal links; you may also include external source urls the model is confident about.

## Tools

- `search_articles(query, since?, until?, categories?, limit?)` — full-text search over the \
full article history. Use this to surface earlier coverage for continuity context.
- `get_article(article_id)` — retrieve fuller text and metadata for a single article by id.
- `submit_brief(headline, intro, topics[...])` — **call this last** to submit the finished \
brief. The loop ends when you call it; do not call any tool after `submit_brief`.

Be selective: surface what matters to this user, not everything. Cap yourself at {max_topics} topics.\
"""


def build_system_prompt(settings: BriefSettings, interest_profile: str) -> str:
    profile_text = (
        interest_profile.strip()
        if interest_profile and interest_profile.strip()
        else "(no interest profile configured — use general newsworthiness)"
    )
    return _SYSTEM_TEMPLATE.format(
        period_hours=settings.brief_period_hours,
        interest_profile=profile_text,
        max_topics=settings.brief_max_topics,
    )


def build_seed_messages(
    session: Session,
    settings: BriefSettings,
    period_start: datetime,
    period_end: datetime,
) -> list[dict]:
    profile_row = session.scalar(select(InterestProfile))
    interest_profile_text = (
        profile_row.profile_text.strip()
        if profile_row and profile_row.profile_text
        else ""
    )

    articles = list(
        session.scalars(
            select(Article)
            .where(
                Article.status == "ready",
                Article.retrieved_at >= period_start,
                Article.retrieved_at < period_end,
            )
            .order_by(nullslast(Article.importance_score.desc()))
            .limit(settings.brief_max_candidate_articles)
        )
    )

    prior_briefs = list(
        session.scalars(
            select(Brief)
            .where(Brief.status == "ready")
            .order_by(Brief.period_end.desc())
            .limit(settings.brief_continuity_count)
        )
    )

    parts: list[str] = []

    if interest_profile_text:
        parts.append("## Interest profile (for reference)\n\n" + interest_profile_text)

    period_label = (
        f"{period_start.strftime('%Y-%m-%d %H:%M')} to "
        f"{period_end.strftime('%Y-%m-%d %H:%M')} UTC"
    )
    parts.append(
        f"## Candidate articles — {len(articles)} article(s) covering {period_label}"
        " (ordered by importance score, highest first)\n\n"
        "Use these as the primary source material for the brief. Reference articles by `id`.\n"
    )

    for article in articles:
        title = article.clean_title or article.feed_title or "(no title)"
        source = article.feed_url or "unknown source"
        pub_date = article.published_at or article.retrieved_at
        pub_str = pub_date.strftime("%Y-%m-%d") if pub_date else "unknown"
        score = article.importance_score if article.importance_score is not None else "?"
        summary = article.summary or article.excerpt or ""
        topics: list[str] = [str(t) for t in article.topics] if isinstance(article.topics, list) else []

        lines = [f"id={article.id} | score={score} | {pub_str} | {source}"]
        lines.append(f"Title: {title}")
        if topics:
            lines.append(f"Topics: {', '.join(topics)}")
        if summary:
            lines.append(f"Summary: {summary}")
        parts.append("\n".join(lines))

    if prior_briefs:
        parts.append(
            f"## Recent briefs — continuity context (last {len(prior_briefs)})\n\n"
            "Reference these when a story looks like a follow-up to earlier events.\n"
        )
        for brief in prior_briefs:
            period_str = brief.period_end.strftime("%Y-%m-%d") if brief.period_end else "unknown"
            brief_lines = [f"Brief ({period_str}): {brief.headline or '(no headline)'}"]
            if brief.intro:
                brief_lines.append(brief.intro)
            parts.append("\n".join(brief_lines))

    parts.append(
        "## Next steps\n\n"
        "Review the candidate articles above. Use `search_articles` for background on any "
        "story that appears to follow earlier events. When ready, call `submit_brief` with "
        "your structured result."
    )

    return [{"role": "user", "content": "\n\n---\n\n".join(parts)}]
