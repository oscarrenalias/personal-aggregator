from __future__ import annotations

from typing import Any, Protocol, Sequence

from aggregator_common.grades import (
    GOOD_TO_KNOW_MAX,
    IMPORTANT_MAX,
    MUST_KNOW_MAX,
    NOISE_MAX,
    ON_TOPIC_MAX,
)

from .config import SummarizeRankSettings
from .schema import PROMPT_VERSION

INTEREST_PROFILE_MAX_CHARS = 2_000


class _CategoryEntry(Protocol):
    name: str
    description: str | None


_SYSTEM_WITH_PROFILE = f"""\
You are an article analyst. For the article provided:
1. Write a concise summary in at most 60 words.
2. Extract up to 5 key topics as a list of short strings.
3. Score this article's importance to the specific user (0-100) based on their interest profile:
   - 0-{NOISE_MAX}: noise — off-topic, irrelevant, spam, or unrelated to any stated interest
   - {NOISE_MAX + 1}-{ON_TOPIC_MAX}: on-topic — touches stated interests but is routine, expected, or low value
   - {ON_TOPIC_MAX + 1}-{GOOD_TO_KNOW_MAX}: good-to-know — useful and relevant, provides clear value to the user
   - {GOOD_TO_KNOW_MAX + 1}-{IMPORTANT_MAX}: important — directly relevant, timely, and meaningfully advances the user's interests
   - {IMPORTANT_MAX + 1}-{MUST_KNOW_MAX}: must-know — critical, essential reading for someone with these interests
   Most articles, even on-topic, are routine and belong below {ON_TOPIC_MAX}. Be selective — on a typical day only a handful are important or must-know.
   The interest profile may include topics or subjects marked as low-priority, negative, or to be deprioritized.
   If the article's subject matches those low-priority or deprioritized signals, assign a LOWER score (push toward the 0-{NOISE_MAX} band) even if the article is otherwise newsworthy.
   Both the profile's stated priorities AND its deprioritizations shape the score.
   Consider: interest match, source relevance, novelty, and practical usefulness.
4. Give a one-sentence reason for the score."""

_SYSTEM_NEUTRAL = f"""\
You are an article analyst. For the article provided:
1. Write a concise summary in at most 60 words.
2. Extract up to 5 key topics as a list of short strings.
3. Score this article's general newsworthiness and usefulness to a general reader (0-100):
   - 0-{NOISE_MAX}: noise — routine, narrow niche, or low-interest content
   - {NOISE_MAX + 1}-{ON_TOPIC_MAX}: on-topic — touches a relevant subject but is routine or of limited general appeal
   - {ON_TOPIC_MAX + 1}-{GOOD_TO_KNOW_MAX}: good-to-know — useful and informative for a general audience
   - {GOOD_TO_KNOW_MAX + 1}-{IMPORTANT_MAX}: important — relevant, timely, and broadly informative
   - {IMPORTANT_MAX + 1}-{MUST_KNOW_MAX}: must-know — highly newsworthy, widely important, or broadly applicable
   Most articles, even on-topic, are routine and belong below {ON_TOPIC_MAX}. Be selective — on a typical day only a handful are important or must-know.
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
