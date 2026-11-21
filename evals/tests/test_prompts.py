"""Tests for evals/clusterer_merge/prompts.py message equality and content."""

from types import SimpleNamespace

from aggregator_clusterer.worker import _MERGE_SYSTEM_PROMPTS, _build_merge_messages
from clusterer_merge.prompts import (
    MERGE_SYSTEM_PROMPT_V3,
    MERGE_SYSTEM_PROMPT_V3_DEALS,
    MERGE_SYSTEM_PROMPT_V4B,
    _build_user_message,
    merge_v1,
    merge_v2,
    merge_v3,
    merge_v3_deals,
    merge_v4b,
)


def _context(title_a=None, summary_a=None, title_b=None, summary_b=None):
    return {
        "vars": {
            "thread_a_id": 1,
            "thread_a_title": title_a,
            "thread_a_summary": summary_a,
            "thread_b_id": 2,
            "thread_b_title": title_b,
            "thread_b_summary": summary_b,
        }
    }


def _reference_messages(title_a, summary_a, title_b, summary_b, variant):
    a = SimpleNamespace(representative_title=title_a, rolling_summary=summary_a)
    b = SimpleNamespace(representative_title=title_b, rolling_summary=summary_b)
    return _build_merge_messages(a, b, variant=variant)


# ---------------------------------------------------------------------------
# v1 / v2 exact delegation tests
# ---------------------------------------------------------------------------

def test_merge_v1_delegates_to_build_merge_messages():
    ctx = _context("Title A", "Summary A", "Title B", "Summary B")
    expected = _reference_messages("Title A", "Summary A", "Title B", "Summary B", "v1")
    assert merge_v1(ctx) == expected


def test_merge_v2_delegates_to_build_merge_messages():
    ctx = _context("Title A", "Summary A", "Title B", "Summary B")
    expected = _reference_messages("Title A", "Summary A", "Title B", "Summary B", "v2")
    assert merge_v2(ctx) == expected


def test_merge_v1_with_none_title_and_summary():
    ctx = _context(None, None, "Title B", "Summary B")
    expected = _reference_messages(None, None, "Title B", "Summary B", "v1")
    assert merge_v1(ctx) == expected


def test_merge_v2_with_none_title_and_summary():
    ctx = _context("Title A", None, None, None)
    expected = _reference_messages("Title A", None, None, None, "v2")
    assert merge_v2(ctx) == expected


def test_merge_v1_all_none():
    ctx = _context(None, None, None, None)
    expected = _reference_messages(None, None, None, None, "v1")
    assert merge_v1(ctx) == expected


def test_merge_v2_all_none():
    ctx = _context(None, None, None, None)
    expected = _reference_messages(None, None, None, None, "v2")
    assert merge_v2(ctx) == expected


def test_merge_v1_and_v2_differ():
    """v1 and v2 should produce different system prompts."""
    ctx = _context("Title A", "Summary A", "Title B", "Summary B")
    v1_msgs = merge_v1(ctx)
    v2_msgs = merge_v2(ctx)
    assert v1_msgs[0]["content"] != v2_msgs[0]["content"]
    # user messages are the same
    assert v1_msgs[1]["content"] == v2_msgs[1]["content"]


# ---------------------------------------------------------------------------
# Draft variant user message equality test
# ---------------------------------------------------------------------------

def test_draft_v3_user_message_matches_build_merge_messages():
    """The v3 user message is byte-for-byte identical to _build_merge_messages user turn."""
    title_a, summary_a = "Thread A title", "Thread A summary"
    title_b, summary_b = "Thread B title", "Thread B summary"

    ctx = _context(title_a, summary_a, title_b, summary_b)
    reference = _reference_messages(title_a, summary_a, title_b, summary_b, "v1")
    v3_msgs = merge_v3(ctx)

    # The user message (index 1) must match exactly
    assert v3_msgs[1]["content"] == reference[1]["content"]


def test_draft_v3_deals_user_message_matches_build_merge_messages():
    ctx = _context("A title", "A summary", "B title", "B summary")
    reference = _reference_messages("A title", "A summary", "B title", "B summary", "v1")
    v3_deals_msgs = merge_v3_deals(ctx)

    assert v3_deals_msgs[1]["content"] == reference[1]["content"]


def test_draft_v3_user_message_with_none_values():
    """None title/summary produce the (no title)/(no summary) fallback, matching the helper."""
    ctx = _context(None, None, None, None)
    reference = _reference_messages(None, None, None, None, "v1")
    v3_msgs = merge_v3(ctx)

    assert v3_msgs[1]["content"] == reference[1]["content"]
    assert "(no title)" in v3_msgs[1]["content"]
    assert "(no summary)" in v3_msgs[1]["content"]


def test_build_user_message_helper_matches_reference():
    """_build_user_message matches the user turn _build_merge_messages produces."""
    title_a, summary_a = "Title A", "Summary A"
    title_b, summary_b = "Title B", "Summary B"
    reference = _reference_messages(title_a, summary_a, title_b, summary_b, "v1")
    helper_msg = _build_user_message(title_a, summary_a, title_b, summary_b)
    assert helper_msg == reference[1]["content"]


# ---------------------------------------------------------------------------
# v3 / v3-deals content tests
# ---------------------------------------------------------------------------

DEALS_RULE_FRAGMENT = "Deal and discount coverage for the same shopping event"


def test_v3_deals_system_prompt_contains_deals_rule():
    assert DEALS_RULE_FRAGMENT in MERGE_SYSTEM_PROMPT_V3_DEALS


def test_v3_does_not_contain_deals_rule():
    assert DEALS_RULE_FRAGMENT not in MERGE_SYSTEM_PROMPT_V3


def test_v3_deals_starts_with_same_text_as_v3():
    """The v3-deals prompt shares its prefix with v3 (it extends v3 by insertion)."""
    # Both start identically up to the insertion point
    # Find the split point: v3-deals inserts after "fixes of the same software release.\n"
    split_marker = "fixes of the same software release.\n"
    v3_up_to = MERGE_SYSTEM_PROMPT_V3[: MERGE_SYSTEM_PROMPT_V3.index(split_marker) + len(split_marker)]
    deals_up_to = MERGE_SYSTEM_PROMPT_V3_DEALS[: MERGE_SYSTEM_PROMPT_V3_DEALS.index(split_marker) + len(split_marker)]
    assert v3_up_to == deals_up_to


def test_v3_and_v3_deals_differ():
    assert MERGE_SYSTEM_PROMPT_V3 != MERGE_SYSTEM_PROMPT_V3_DEALS


def test_merge_v3_uses_v3_system_prompt():
    ctx = _context("A", "A sum", "B", "B sum")
    msgs = merge_v3(ctx)
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == MERGE_SYSTEM_PROMPT_V3


def test_merge_v3_deals_uses_v3_deals_system_prompt():
    ctx = _context("A", "A sum", "B", "B sum")
    msgs = merge_v3_deals(ctx)
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == MERGE_SYSTEM_PROMPT_V3_DEALS


# ---------------------------------------------------------------------------
# No LLM/network/DB calls
# ---------------------------------------------------------------------------

def test_no_external_calls_in_prompts_module():
    """Importing and calling prompt functions must not trigger any external I/O.
    This test just verifies the calls complete without side-effects (no assertion
    failure = no network/DB access was needed)."""
    ctx = _context("title", "summary", "title b", "summary b")
    for fn in (merge_v1, merge_v2, merge_v3, merge_v3_deals):
        result = fn(ctx)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"


# ---------------------------------------------------------------------------
# v4b tests
# ---------------------------------------------------------------------------

def test_merge_v4b_delegates_to_build_merge_messages():
    """merge_v4b must produce the same messages as _build_merge_messages(variant='v4b')."""
    ctx = _context("Title A", "Summary A", "Title B", "Summary B")
    expected = _reference_messages("Title A", "Summary A", "Title B", "Summary B", "v4b")
    assert merge_v4b(ctx) == expected


def test_merge_v4b_with_none_values():
    """None title/summary produce the (no title)/(no summary) fallback, matching the helper."""
    ctx = _context(None, None, None, None)
    expected = _reference_messages(None, None, None, None, "v4b")
    assert merge_v4b(ctx) == expected


def test_v4b_system_prompt_matches_production_registry():
    """MERGE_SYSTEM_PROMPT_V4B in prompts.py is byte-for-byte equal to worker._MERGE_SYSTEM_PROMPTS['v4b']."""
    assert MERGE_SYSTEM_PROMPT_V4B == _MERGE_SYSTEM_PROMPTS["v4b"]


def test_v4b_system_prompt_differs_from_v1_and_v2():
    """v4b must be a distinct prompt from v1 and v2."""
    assert MERGE_SYSTEM_PROMPT_V4B != _MERGE_SYSTEM_PROMPTS["v1"]
    assert MERGE_SYSTEM_PROMPT_V4B != _MERGE_SYSTEM_PROMPTS["v2"]


def test_merge_v4b_user_message_matches_reference():
    """The v4b user message is byte-for-byte identical to what _build_merge_messages produces."""
    ctx = _context("Title A", "Summary A", "Title B", "Summary B")
    reference = _reference_messages("Title A", "Summary A", "Title B", "Summary B", "v1")
    v4b_msgs = merge_v4b(ctx)
    assert v4b_msgs[1]["content"] == reference[1]["content"]
