"""promptfoo Python prompt functions for the clusterer.merge eval.

merge_v1 and merge_v2 delegate to the production _build_merge_messages helper so
they never need to copy prompt text — any update to the service prompt is picked up
automatically.

merge_v3 and merge_v3_deals are draft variants that live here because they have not
been promoted into the production PROMPT_VARIANTS registry yet.
"""

from __future__ import annotations

from types import SimpleNamespace

from aggregator_clusterer.worker import _build_merge_messages


# ---------------------------------------------------------------------------
# Draft system prompts
# ---------------------------------------------------------------------------

MERGE_SYSTEM_PROMPT_V3 = (
    "You are a news editor deciding whether two news threads should be combined into one. "
    "Reply with a JSON object only — no markdown: "
    '{"same_story": true|false, "reason": "..."}. '
    "Decide by the underlying matter each thread is about, not by shared names, companies or keywords.\n"
    "Answer true when both threads follow the same underlying story: one event or situation together "
    "with its causes, later developments, consequences and reactions. In particular:\n"
    "- Successive developments in one chain of events are the same story, even when a later one "
    "reverses an earlier one — e.g. a minister's corruption allegation, the party backing him, his "
    "resignation, and the succession fight it triggers.\n"
    "- The same situation reported at a different scope is the same story — e.g. a region-wide "
    "drought emergency and one country in that region rationing water because of it.\n"
    "- Different stages of one event are the same story — e.g. polls and the result of the same "
    "election, qualifying and the race result of the same grand prix, the features and the security "
    "fixes of the same software release.\n"
    "Answer false when the threads only share a person, company, product, place or occasion but are "
    "about different matters — e.g. a president's state visit and a domestic tax bill he comments on; "
    "a carmaker's recall and a separate antitrust probe into the same carmaker; a console launch and "
    "a guide to that console's settings; a celebrity appearance at a festival and the festival's "
    "ticketing problems; two different startups raising money in the same sector.\n"
    "If you are still unsure after applying these rules, answer false."
)

MERGE_SYSTEM_PROMPT_V3_DEALS = MERGE_SYSTEM_PROMPT_V3.replace(
    "fixes of the same software release.\n",
    "fixes of the same software release.\n"
    "- Deal and discount coverage for the same shopping event is the same story, whichever retailer, "
    "brand or product each thread features — e.g. two threads of Prime Day deals, one on laptops at "
    "one retailer and one on headphones at another. This applies only to deal and price coverage of a "
    "named sales event (Black Friday, Cyber Monday, Prime Day, Singles' Day and the like), not to other "
    "news that happens to mention it.\n",
)

assert MERGE_SYSTEM_PROMPT_V3_DEALS != MERGE_SYSTEM_PROMPT_V3


# ---------------------------------------------------------------------------
# Shared user-message helper (must be byte-for-byte identical to what
# _build_merge_messages produces for the user turn)
# ---------------------------------------------------------------------------

def _build_user_message(thread_a_title: str | None, thread_a_summary: str | None,
                        thread_b_title: str | None, thread_b_summary: str | None) -> str:
    return (
        f"Thread A:\nTitle: {thread_a_title or '(no title)'}\n"
        f"Summary: {thread_a_summary or '(no summary)'}\n\n"
        f"Thread B:\nTitle: {thread_b_title or '(no title)'}\n"
        f"Summary: {thread_b_summary or '(no summary)'}\n\n"
        "Are these the same story?"
    )


# ---------------------------------------------------------------------------
# Prompt functions
# ---------------------------------------------------------------------------

def merge_v1(context: dict) -> list[dict]:
    vars_ = context["vars"]
    a = SimpleNamespace(
        representative_title=vars_.get("thread_a_title"),
        rolling_summary=vars_.get("thread_a_summary"),
    )
    b = SimpleNamespace(
        representative_title=vars_.get("thread_b_title"),
        rolling_summary=vars_.get("thread_b_summary"),
    )
    return _build_merge_messages(a, b, variant="v1")


def merge_v2(context: dict) -> list[dict]:
    vars_ = context["vars"]
    a = SimpleNamespace(
        representative_title=vars_.get("thread_a_title"),
        rolling_summary=vars_.get("thread_a_summary"),
    )
    b = SimpleNamespace(
        representative_title=vars_.get("thread_b_title"),
        rolling_summary=vars_.get("thread_b_summary"),
    )
    return _build_merge_messages(a, b, variant="v2")


def merge_v3(context: dict) -> list[dict]:
    vars_ = context["vars"]
    return [
        {"role": "system", "content": MERGE_SYSTEM_PROMPT_V3},
        {
            "role": "user",
            "content": _build_user_message(
                vars_.get("thread_a_title"),
                vars_.get("thread_a_summary"),
                vars_.get("thread_b_title"),
                vars_.get("thread_b_summary"),
            ),
        },
    ]


def merge_v3_deals(context: dict) -> list[dict]:
    vars_ = context["vars"]
    return [
        {"role": "system", "content": MERGE_SYSTEM_PROMPT_V3_DEALS},
        {
            "role": "user",
            "content": _build_user_message(
                vars_.get("thread_a_title"),
                vars_.get("thread_a_summary"),
                vars_.get("thread_b_title"),
                vars_.get("thread_b_summary"),
            ),
        },
    ]
