"""Regression test for the auto-read dwell timer race condition.

The htmx:afterSwap listener in app.js must guard against non-GET swaps so
that a late-arriving POST response (e.g. the auto-read mark-read POST) cannot
cancel the dwell timer that was just armed for the next article.

Without the guard, a slow backend causes every other article to skip auto-read
because the read-POST afterSwap fires after the user has already navigated to
the next article and armed its timer; the late afterSwap then cancels it.

These tests verify the guard is present by inspecting the static file text.
Full browser verification (open n articles in sequence, confirm every one
auto-marks) is required to confirm correct runtime behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path

_APP_JS = (
    Path(__file__).parent.parent
    / "src" / "aggregator_web" / "static" / "app.js"
)


def _afterswap_listener_body(js: str) -> str:
    """Extract the body of the htmx:afterSwap addEventListener callback."""
    marker = "document.addEventListener('htmx:afterSwap'"
    start = js.find(marker)
    assert start != -1, "htmx:afterSwap listener not found in app.js"
    depth = 0
    i = start
    body_start = None
    while i < len(js):
        if js[i] == '{':
            depth += 1
            if depth == 1:
                body_start = i
        elif js[i] == '}':
            depth -= 1
            if depth == 0:
                assert body_start is not None
                return js[body_start + 1:i]
        i += 1
    raise AssertionError("Unclosed htmx:afterSwap listener in app.js")


def test_afterswap_listener_guards_non_get_swaps() -> None:
    """The listener must early-return for non-GET htmx swaps."""
    js = _APP_JS.read_text()
    body = _afterswap_listener_body(js)
    assert re.search(
        r"requestConfig.*verb\s*!==\s*['\"]get['\"]",
        body,
    ), (
        "htmx:afterSwap listener is missing the non-GET guard "
        "(event.detail.requestConfig.verb !== 'get'). "
        "Without this guard, late-arriving POST responses cancel the dwell "
        "timer already armed for the next article, causing every other article "
        "to skip auto-read on a slow backend."
    )


def test_afterswap_guard_precedes_arm_call() -> None:
    """The verb guard must appear before the _dwell.arm() call."""
    js = _APP_JS.read_text()
    body = _afterswap_listener_body(js)
    guard_pos = body.find('requestConfig')
    arm_pos = body.find('_dwell.arm(')
    assert guard_pos != -1, "verb guard not found in htmx:afterSwap listener"
    assert arm_pos != -1, "_dwell.arm() call not found in htmx:afterSwap listener"
    assert guard_pos < arm_pos, (
        "verb guard must appear before _dwell.arm() so non-GET swaps are "
        "rejected before the timer is armed"
    )
