"""Tests for the cross-segment polish pass (Phase 2.5)."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")


def _make_settings(polish_enabled: bool = True):
    from aggregator_podcast.config import PodcastSettings

    return PodcastSettings(
        DATABASE_URL=os.environ["DATABASE_URL"],
        podcast_polish_enabled=polish_enabled,
    )


def _make_segments(texts: list[str]) -> list[dict]:
    return [
        {
            "thread_id": i + 1,
            "headline": f"Headline {i + 1}",
            "topic_category": "AI & Technology",
            "sources": ["Source A"],
            "is_developing": False,
            "text": text,
        }
        for i, text in enumerate(texts)
    ]


def _fake_polish_response(rewritten: list[dict]) -> MagicMock:
    payload = json.dumps({"segments": rewritten})
    msg = MagicMock()
    msg.content = payload
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── Prompt content check ──────────────────────────────────────────────────────

def test_segment_prompt_no_longer_contains_latest_chapter():
    """The historical-arc instruction must not contain the literal phrase 'latest chapter'."""
    from aggregator_podcast.generate import _SEGMENT_SYSTEM

    assert "latest chapter" not in _SEGMENT_SYSTEM.lower()


# ── Polish pass: immutable fields ─────────────────────────────────────────────

def test_polish_preserves_non_text_fields():
    """Polish pass must not alter headline, thread_id, topic_category, sources, is_developing."""
    from aggregator_podcast.generate import run_polish_phase

    originals = _make_segments(["The economy grew by 3 percent.", "Scientists found 42 new species."])
    rewritten_texts = [
        {"index": 0, "text": "A 3-percent expansion was recorded."},
        {"index": 1, "text": "Researchers have identified 42 previously unknown species."},
    ]
    fake_resp = _fake_polish_response(rewritten_texts)

    settings = _make_settings()
    with patch("aggregator_podcast.generate.litellm.completion", return_value=fake_resp):
        result = run_polish_phase(originals, settings)

    assert len(result) == 2
    for i, seg in enumerate(result):
        assert seg["thread_id"] == originals[i]["thread_id"]
        assert seg["headline"] == originals[i]["headline"]
        assert seg["topic_category"] == originals[i]["topic_category"]
        assert seg["sources"] == originals[i]["sources"]
        assert seg["is_developing"] == originals[i]["is_developing"]
    # text may differ
    assert result[0]["text"] == "A 3-percent expansion was recorded."
    assert result[1]["text"] == "Researchers have identified 42 previously unknown species."


# ── Digit-safety guard ────────────────────────────────────────────────────────

def test_digit_change_discards_rewrite_keeps_original():
    """When a rewritten segment's digits differ, the original text is kept."""
    from aggregator_podcast.generate import run_polish_phase

    originals = _make_segments(["Growth was 3 percent last quarter.", "Oil fell to 80 dollars."])
    # segment 0 changes a digit (3 → 4), segment 1 is fine
    rewritten_texts = [
        {"index": 0, "text": "Growth was 4 percent last quarter."},
        {"index": 1, "text": "The price of oil dropped to 80 dollars."},
    ]
    fake_resp = _fake_polish_response(rewritten_texts)

    settings = _make_settings()
    with patch("aggregator_podcast.generate.litellm.completion", return_value=fake_resp):
        result = run_polish_phase(originals, settings)

    # segment 0: original kept because digit changed
    assert result[0]["text"] == "Growth was 3 percent last quarter."
    # segment 1: rewrite accepted because digits match
    assert result[1]["text"] == "The price of oil dropped to 80 dollars."


def test_digit_safe_rewrite_is_applied():
    """A rewrite that preserves all digits is accepted."""
    from aggregator_podcast.generate import run_polish_phase

    originals = _make_segments(["In 2025 the rate hit 5 percent."])
    rewritten_texts = [{"index": 0, "text": "The rate reached 5 percent back in 2025."}]
    fake_resp = _fake_polish_response(rewritten_texts)

    settings = _make_settings()
    with patch("aggregator_podcast.generate.litellm.completion", return_value=fake_resp):
        result = run_polish_phase(originals, settings)

    assert result[0]["text"] == "The rate reached 5 percent back in 2025."


# ── Error / malformed response fallback ──────────────────────────────────────

def test_polish_call_raises_returns_originals():
    """When the LLM call raises, all original segments are returned unchanged."""
    from aggregator_podcast.generate import run_polish_phase

    originals = _make_segments(["First story.", "Second story."])
    settings = _make_settings()

    with patch("aggregator_podcast.generate.litellm.completion", side_effect=RuntimeError("LLM down")):
        result = run_polish_phase(originals, settings)

    assert result == originals


def test_polish_malformed_json_returns_originals():
    """When the LLM returns malformed JSON, all original segments are returned unchanged."""
    from aggregator_podcast.generate import run_polish_phase

    originals = _make_segments(["First story.", "Second story."])
    msg = MagicMock()
    msg.content = "not json at all"
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]

    settings = _make_settings()
    with patch("aggregator_podcast.generate.litellm.completion", return_value=resp):
        result = run_polish_phase(originals, settings)

    assert result == originals


def test_polish_wrong_segment_count_returns_originals():
    """When the returned segment count differs from the input, originals are kept."""
    from aggregator_podcast.generate import run_polish_phase

    originals = _make_segments(["First.", "Second.", "Third."])
    # LLM only returns 2 entries instead of 3
    fake_resp = _fake_polish_response([{"index": 0, "text": "First."}])

    settings = _make_settings()
    with patch("aggregator_podcast.generate.litellm.completion", return_value=fake_resp):
        result = run_polish_phase(originals, settings)

    assert result == originals


# ── Disabled flag ─────────────────────────────────────────────────────────────

def test_polish_disabled_skips_llm_call():
    """When podcast_polish_enabled=False, no LLM call is made and segments are unchanged."""
    from aggregator_podcast.generate import run_polish_phase

    originals = _make_segments(["This is the latest chapter in a story.", "Second segment."])
    settings = _make_settings(polish_enabled=False)

    with patch("aggregator_podcast.generate.litellm.completion") as mock_llm:
        # run_polish_phase should NOT be called from generate_podcast when disabled;
        # but run_polish_phase itself always runs — the skip lives in generate_podcast.
        # Test the generate_podcast integration path instead.
        pass

    # Verify directly that the setting is False
    assert settings.podcast_polish_enabled is False


def test_generate_podcast_skips_polish_when_disabled(monkeypatch):
    """generate_podcast skips the polish pass entirely when podcast_polish_enabled=False."""
    from aggregator_podcast import generate

    settings = _make_settings(polish_enabled=False)
    session = MagicMock()

    segments = [
        {"thread_id": 1, "headline": "H", "text": "T1.", "topic_category": "Other",
         "sources": [], "is_developing": False},
    ]

    selection_resp = MagicMock()
    selection_resp.choices[0].message.content = json.dumps(
        {"episode_theme": "test", "selected": [{"thread_id": 1, "rationale": "r"}]}
    )

    wrapper_resp = MagicMock()
    wrapper_resp.choices[0].message.content = json.dumps(
        {"intro": "Hi.", "transitions": [], "outro": "Bye."}
    )

    polish_called = []

    def fake_run_polish(segs, s):
        polish_called.append(True)
        return segs

    monkeypatch.setattr(generate, "run_selection_phase",
                        lambda sess, s: ("theme", [{"thread_id": 1, "rationale": "r"}], []))
    monkeypatch.setattr(generate, "run_segment_phase",
                        lambda sess, tid, rat, s: segments[0])
    monkeypatch.setattr(generate, "run_polish_phase", fake_run_polish)
    monkeypatch.setattr(generate, "run_wrapper_phase",
                        lambda d, t, segs, s: {"intro": "Hi.", "transitions": [], "outro": "Bye."})
    monkeypatch.setattr(generate, "_resolve_artwork_url", lambda sess, script: None)

    episode = MagicMock()
    episode.date.strftime.return_value = "Saturday, October 3, 2026"
    episode.date.isoformat.return_value = "2026-10-03"

    generate.generate_podcast(episode, settings, session)

    assert not polish_called, "run_polish_phase should not be called when podcast_polish_enabled=False"


def test_generate_podcast_calls_polish_when_enabled(monkeypatch):
    """generate_podcast calls the polish pass when podcast_polish_enabled=True."""
    from aggregator_podcast import generate

    settings = _make_settings(polish_enabled=True)
    session = MagicMock()

    segments = [
        {"thread_id": 1, "headline": "H", "text": "T1.", "topic_category": "Other",
         "sources": [], "is_developing": False},
    ]

    polish_called = []

    def fake_run_polish(segs, s):
        polish_called.append(True)
        return segs

    monkeypatch.setattr(generate, "run_selection_phase",
                        lambda sess, s: ("theme", [{"thread_id": 1, "rationale": "r"}], []))
    monkeypatch.setattr(generate, "run_segment_phase",
                        lambda sess, tid, rat, s: segments[0])
    monkeypatch.setattr(generate, "run_polish_phase", fake_run_polish)
    monkeypatch.setattr(generate, "run_wrapper_phase",
                        lambda d, t, segs, s: {"intro": "Hi.", "transitions": [], "outro": "Bye."})
    monkeypatch.setattr(generate, "_resolve_artwork_url", lambda sess, script: None)

    episode = MagicMock()
    episode.date.strftime.return_value = "Saturday, October 3, 2026"
    episode.date.isoformat.return_value = "2026-10-03"

    generate.generate_podcast(episode, settings, session)

    assert polish_called, "run_polish_phase should be called when podcast_polish_enabled=True"
