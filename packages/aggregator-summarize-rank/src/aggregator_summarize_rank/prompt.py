from __future__ import annotations

from typing import Any, Protocol, Sequence

from .config import SummarizeRankSettings
from .schema import PROMPT_VERSION

INTEREST_PROFILE_MAX_CHARS = 2_000


class _CategoryEntry(Protocol):
    name: str
    description: str | None


_SYSTEM_WITH_PROFILE = """\
You are an article analyst. For the article provided:
1. Write a concise summary in at most 60 words.
2. Extract up to 5 key topics as a list of short strings.
3. Score this article's importance to the specific user (0-100) based on their interest profile:
   - 0-30: low relevance, general noise, or outside stated interests
   - 31-60: maybe useful, tangentially related to interests
   - 61-80: relevant, directly related to one or more stated interests
   - 81-100: highly important, strongly matches interests
   The interest profile may include topics or subjects marked as low-priority, negative, or to be deprioritized.
   If the article's subject matches those low-priority or deprioritized signals, assign a LOWER score (push toward the 0-30 band) even if the article is otherwise newsworthy.
   Both the profile's stated priorities AND its deprioritizations shape the score.
   Consider: interest match, source relevance, novelty, and practical usefulness.
4. Give a one-sentence reason for the score."""

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
4. Give a one-sentence reason for the score."""

_SYSTEM_SUFFIX = "\n\nRespond with valid JSON matching the RankResult schema."


def _build_category_instruction(categories: Sequence[_CategoryEntry]) -> str:
    lines = []
    for cat in categories:
        if cat.description:
            lines.append(f"- {cat.name}: {cat.description}")
        else:
            lines.append(f"- {cat.name}")
    category_list = "\n".join(lines)
    return (
        "5. Assign the article to zero or more of these categories using the exact names from the list below. "
        "Only assign a category when the article clearly matches that category's description. "
        "Honor any exclusions stated in a description (e.g. if a description says it EXCLUDES a topic, do not assign that category to articles about that topic). "
        "Prefer the most specific matching category. "
        "Do not over-assign, pad, or guess — assigning zero categories is acceptable when nothing clearly fits. "
        f"Only use names from this list:\n{category_list}"
    )


def build_messages(
    article_data: dict[str, Any],
    interest_profile_text: str,
    settings: SummarizeRankSettings,
    enabled_categories: Sequence[_CategoryEntry] | None = None,
) -> list[dict[str, str]]:
    is_profiled = bool(interest_profile_text and interest_profile_text.strip())
    system_base = _SYSTEM_WITH_PROFILE if is_profiled else _SYSTEM_NEUTRAL

    if enabled_categories:
        system_content = (
            system_base + "\n" + _build_category_instruction(enabled_categories) + _SYSTEM_SUFFIX
        )
    else:
        system_content = system_base + _SYSTEM_SUFFIX

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
