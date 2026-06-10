from __future__ import annotations

from typing import Any

from .config import SummarizeRankSettings
from .schema import PROMPT_VERSION

INTEREST_PROFILE_MAX_CHARS = 2_000

_SYSTEM_WITH_PROFILE = """\
You are an article analyst. For the article provided:
1. Write a concise summary in at most 60 words.
2. Extract up to 5 key topics as a list of short strings.
3. Score this article's importance to the specific user (0-100) based on their interest profile:
   - 0-30: low relevance, general noise, or outside stated interests
   - 31-60: maybe useful, tangentially related to interests
   - 61-80: relevant, directly related to one or more stated interests
   - 81-100: highly important, strongly matches interests
   Consider: interest match, source relevance, novelty, and practical usefulness.
4. Give a one-sentence reason for the score.

Respond with valid JSON matching the RankResult schema."""

_SYSTEM_NEUTRAL = """\
You are an article analyst. For the article provided:
1. Write a concise summary in at most 60 words.
2. Extract up to 5 key topics as a list of short strings.
3. Score this article's general newsworthiness and usefulness to a general reader (0-100):
   - 0-30: low interest, routine, or narrow niche content
   - 31-60: maybe useful to some readers, limited general appeal
   - 61-80: relevant and informative for a general audience
   - 81-100: highly newsworthy, broadly important, or widely applicable
   Consider: newsworthiness, novelty, practical value, and breadth of potential interest.
4. Give a one-sentence reason for the score.

Respond with valid JSON matching the RankResult schema."""


def build_messages(
    article_data: dict[str, Any],
    interest_profile_text: str,
    settings: SummarizeRankSettings,
) -> list[dict[str, str]]:
    is_profiled = bool(interest_profile_text and interest_profile_text.strip())
    system_content = _SYSTEM_WITH_PROFILE if is_profiled else _SYSTEM_NEUTRAL

    source_name = article_data.get("source_name") or "Unknown Source"
    title = (
        article_data.get("title")
        or article_data.get("clean_title")
        or article_data.get("feed_title")
        or "(no title)"
    )

    content = (
        article_data.get("clean_text")
        or article_data.get("excerpt")
        or article_data.get("feed_summary")
        or ""
    )
    if len(content) > settings.llm_max_input_chars:
        content = content[: settings.llm_max_input_chars]

    user_parts = [
        f"Source: {source_name}",
        f"Title: {title}",
    ]
    if is_profiled:
        profile_snippet = interest_profile_text[:INTEREST_PROFILE_MAX_CHARS]
        user_parts.append(f"Interest profile:\n{profile_snippet}")
    user_parts.append(f"Article content:\n{content}")

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def get_prompt_version() -> str:
    return PROMPT_VERSION
