"""Regression tests for the auto-read dwell observer in app.js.

The old htmx:afterSwap arm/cancel design caused deterministic every-other-article
misses on consecutive navigation (the swap event was not always present at the
moment the handler ran, so alternate articles never got a dwell timer armed).

The fix replaces the entire _dwell IIFE + afterSwap listener with a once-per-second
setInterval DOM-polling observer that has no swap-timing dependency.

These tests verify the new observer is wired correctly by inspecting the static
file text.  Full browser verification (open 5-6 articles in sequence, confirm every
one auto-marks read within ~5 s of visible dwell) is required to confirm correct
runtime behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path

_APP_JS = (
    Path(__file__).parent.parent
    / "src" / "aggregator_web" / "static" / "app.js"
)


# ---------------------------------------------------------------------------
# Removal assertions: old machinery must be gone
# ---------------------------------------------------------------------------

def test_dwell_iife_removed() -> None:
    """The old _dwell IIFE must not exist — it caused the every-other miss."""
    js = _APP_JS.read_text()
    assert 'const _dwell = (' not in js, (
        "_dwell IIFE still present in app.js.  The IIFE arm/cancel design "
        "caused every-other-article auto-read misses and must be removed."
    )


def test_afterswap_dwell_arm_removed() -> None:
    """No htmx:afterSwap handler should call _dwell.arm — that listener is gone."""
    js = _APP_JS.read_text()
    assert '_dwell.arm(' not in js, (
        "_dwell.arm() call found in app.js.  The afterSwap-based arm/cancel "
        "design has been replaced by the setInterval observer."
    )


def test_dwell_cancel_not_called_in_manual_paths() -> None:
    """_dwell.cancel() must not remain in _toggleOpenArticleRead, _markReadAndNext,
    or closeReader — the observer does not need cancellation from those callers."""
    js = _APP_JS.read_text()
    assert '_dwell.cancel(' not in js, (
        "_dwell.cancel() still appears in app.js.  Remove all three cancel() "
        "calls from _toggleOpenArticleRead, _markReadAndNext, and closeReader."
    )


# ---------------------------------------------------------------------------
# Presence assertions: new observer must be wired correctly
# ---------------------------------------------------------------------------

def test_setinterval_observer_present() -> None:
    """A setInterval call must exist at module level for the polling observer."""
    js = _APP_JS.read_text()
    assert 'setInterval(' in js, (
        "setInterval observer not found in app.js.  The DOM-polling auto-read "
        "observer must be registered at module level."
    )


def test_observer_reads_auto_read_seconds() -> None:
    """Observer must read autoReadSeconds from the body dataset."""
    js = _APP_JS.read_text()
    assert 'autoReadSeconds' in js, (
        "autoReadSeconds not referenced in app.js.  The observer must read "
        "document.body.dataset.autoReadSeconds to honour WEB_AUTO_READ_SECONDS."
    )


def test_observer_checks_reader_open() -> None:
    """Observer must check that body has the 'reader-open' class."""
    js = _APP_JS.read_text()
    assert "'reader-open'" in js or '"reader-open"' in js, (
        "'reader-open' class check not found in app.js.  The observer must "
        "skip accumulation when the reader pane is closed."
    )


def test_observer_checks_document_hidden() -> None:
    """Observer must not accumulate time while the tab is hidden."""
    js = _APP_JS.read_text()
    assert 'document.hidden' in js, (
        "document.hidden check not found in app.js.  The observer must stop "
        "accumulating visible dwell when the tab is backgrounded."
    )


def test_observer_uses_fired_set() -> None:
    """Observer must use a Set to fire exactly once per article id."""
    js = _APP_JS.read_text()
    assert '_dwellFired' in js, (
        "_dwellFired Set not found in app.js.  The observer must track which "
        "article ids have already been auto-marked so it fires exactly once."
    )
    assert '_dwellFired.has(' in js, (
        "_dwellFired.has() guard not found in app.js.  The observer must check "
        "the Set before firing to prevent double-marking."
    )
    assert '_dwellFired.add(' in js, (
        "_dwellFired.add() not found in app.js.  The observer must add the id "
        "to the fired Set before posting the read request."
    )


def test_observer_caps_per_tick_delta() -> None:
    """Observer must cap each tick's delta to prevent over-count after throttling."""
    js = _APP_JS.read_text()
    assert re.search(r'Math\.min\([^)]*2000', js), (
        "2000 ms per-tick cap not found in app.js.  The observer must cap "
        "each tick's elapsed ms to 2000 to avoid over-counting when the "
        "interval fires late (e.g. after a backgrounded tab resumes)."
    )


def test_observer_resets_on_article_change() -> None:
    """Observer must reset counters when the tracked article id changes."""
    js = _APP_JS.read_text()
    assert '_dwellTrackedId' in js, (
        "_dwellTrackedId not found in app.js.  The observer must track the "
        "current article id and reset visibleMs when a new article appears."
    )
    assert '_dwellVisibleMs' in js, (
        "_dwellVisibleMs not found in app.js.  The observer must accumulate "
        "visible milliseconds and reset them on article change."
    )
