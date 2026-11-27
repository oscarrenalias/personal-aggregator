"""promptfoo Python prompt functions for the summarize_rank.rank eval.

rank_v2 and rank_v3 delegate to the production build_messages function so they
never need to copy prompt text — any update to the service prompt is picked up
automatically.
"""

from __future__ import annotations

from aggregator_summarize_rank.config import SummarizeRankSettings
from aggregator_summarize_rank.prompt import build_messages


def _make_settings() -> SummarizeRankSettings:
    # Construct settings with a placeholder DATABASE_URL so we get the production
    # defaults (including llm_max_input_chars=6000) without needing a live DB.
    import os
    orig = os.environ.get("DATABASE_URL")
    os.environ.setdefault("DATABASE_URL", "postgresql://placeholder@localhost/aggregator")
    try:
        return SummarizeRankSettings()
    finally:
        if orig is None:
            os.environ.pop("DATABASE_URL", None)


class _Category:
    def __init__(self, name: str, description: str | None = None):
        self.name = name
        self.description = description


def _build_article_data(vars_: dict) -> dict:
    return {
        "source_name": vars_.get("source_name") or "Unknown Source",
        "clean_title": vars_.get("clean_title"),
        "feed_title": vars_.get("feed_title"),
        "title": vars_.get("title"),
        "clean_text": vars_.get("content_text"),
        "excerpt": vars_.get("excerpt"),
        "feed_summary": vars_.get("feed_summary"),
    }


def _build_categories(raw: list | None) -> list[_Category]:
    if not raw:
        return []
    return [
        _Category(name=cat.get("name", ""), description=cat.get("description"))
        for cat in raw
        if isinstance(cat, dict) and cat.get("name")
    ]


def rank_v2(context: dict) -> list[dict]:
    vars_ = context["vars"]
    article_data = _build_article_data(vars_)
    interest_profile_text = vars_.get("interest_profile_text") or ""
    categories = _build_categories(vars_.get("categories"))
    settings = _make_settings()
    return build_messages(
        article_data=article_data,
        interest_profile_text=interest_profile_text,
        settings=settings,
        enabled_categories=categories if categories else None,
        variant="v2",
    )


def rank_v3(context: dict) -> list[dict]:
    vars_ = context["vars"]
    article_data = _build_article_data(vars_)
    interest_profile_text = vars_.get("interest_profile_text") or ""
    categories = _build_categories(vars_.get("categories"))
    settings = _make_settings()
    return build_messages(
        article_data=article_data,
        interest_profile_text=interest_profile_text,
        settings=settings,
        enabled_categories=categories if categories else None,
        variant="v3",
    )
