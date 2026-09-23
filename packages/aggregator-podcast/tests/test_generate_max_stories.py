"""Tests for PODCAST_MAX_STORIES hard cap and prompt interpolation."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")


def _make_settings(max_stories: int = 6):
    from aggregator_podcast.config import PodcastSettings

    return PodcastSettings(
        DATABASE_URL=os.environ["DATABASE_URL"],
        podcast_max_stories=max_stories,
    )


def _make_selection_response(selected: list[dict], episode_theme: str = "") -> MagicMock:
    """Build a fake litellm completion response for the selection LLM."""
    payload = json.dumps({"episode_theme": episode_theme, "selected": selected})
    msg = MagicMock()
    msg.content = payload
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _minimal_candidates(n: int) -> list[dict]:
    return [
        {
            "thread_id": i,
            "title": f"Story {i}",
            "summary": "",
            "top_grade": 90 - i,
            "tier": "A",
            "first_seen": "2026-09-23",
            "last_updated": "2026-09-23",
            "source_diversity": 1.0,
        }
        for i in range(1, n + 1)
    ]


# ── Cap enforcement ───────────────────────────────────────────────────────────

def test_hard_cap_truncates_when_llm_overshoots():
    """When Phase 1 returns more stories than podcast_max_stories, the list is truncated."""
    from aggregator_podcast.generate import run_selection_phase

    settings = _make_settings(max_stories=4)
    # LLM returns 8 stories
    llm_selected = [{"thread_id": i, "rationale": f"reason {i}"} for i in range(1, 9)]
    fake_resp = _make_selection_response(llm_selected, episode_theme="tech day")

    session = MagicMock()

    with (
        patch("aggregator_podcast.generate._get_interest_profile", return_value=""),
        patch("aggregator_podcast.generate._get_candidate_threads", return_value=_minimal_candidates(10)),
        patch("aggregator_podcast.generate._build_continuity_block", return_value=""),
        patch("aggregator_podcast.generate._call_selection_llm", return_value=fake_resp),
    ):
        theme, selected, candidates = run_selection_phase(session, settings)

    assert len(selected) == 4
    # The first 4 stories (by LLM order) are kept
    assert [s["thread_id"] for s in selected] == [1, 2, 3, 4]
    assert theme == "tech day"


def test_selection_at_cap_passes_through_unchanged():
    """When Phase 1 returns exactly podcast_max_stories stories, nothing is truncated."""
    from aggregator_podcast.generate import run_selection_phase

    settings = _make_settings(max_stories=5)
    llm_selected = [{"thread_id": i, "rationale": ""} for i in range(1, 6)]
    fake_resp = _make_selection_response(llm_selected)

    session = MagicMock()

    with (
        patch("aggregator_podcast.generate._get_interest_profile", return_value=""),
        patch("aggregator_podcast.generate._get_candidate_threads", return_value=_minimal_candidates(10)),
        patch("aggregator_podcast.generate._build_continuity_block", return_value=""),
        patch("aggregator_podcast.generate._call_selection_llm", return_value=fake_resp),
    ):
        theme, selected, candidates = run_selection_phase(session, settings)

    assert len(selected) == 5
    assert [s["thread_id"] for s in selected] == [1, 2, 3, 4, 5]


def test_selection_under_cap_passes_through_unchanged():
    """When Phase 1 returns fewer stories than podcast_max_stories, nothing is truncated."""
    from aggregator_podcast.generate import run_selection_phase

    settings = _make_settings(max_stories=6)
    llm_selected = [{"thread_id": i, "rationale": ""} for i in range(1, 4)]
    fake_resp = _make_selection_response(llm_selected)

    session = MagicMock()

    with (
        patch("aggregator_podcast.generate._get_interest_profile", return_value=""),
        patch("aggregator_podcast.generate._get_candidate_threads", return_value=_minimal_candidates(10)),
        patch("aggregator_podcast.generate._build_continuity_block", return_value=""),
        patch("aggregator_podcast.generate._call_selection_llm", return_value=fake_resp),
    ):
        theme, selected, candidates = run_selection_phase(session, settings)

    assert len(selected) == 3


# ── Prompt interpolation ──────────────────────────────────────────────────────

def test_max_stories_appears_in_selection_prompt():
    """The value of podcast_max_stories is injected into the selection system prompt."""
    from aggregator_podcast.generate import _SELECTION_SYSTEM_BASE

    rendered = _SELECTION_SYSTEM_BASE.format(max_stories=9)
    assert "9 stories" in rendered


def test_configured_max_stories_appears_in_generated_prompt():
    """run_selection_phase passes podcast_max_stories into the system prompt seen by the LLM."""
    from aggregator_podcast.generate import run_selection_phase

    settings = _make_settings(max_stories=3)
    llm_selected = [{"thread_id": 1, "rationale": ""}]
    fake_resp = _make_selection_response(llm_selected)

    captured_messages: list = []

    def capture_call(messages, settings):
        captured_messages.extend(messages)
        return fake_resp

    session = MagicMock()

    with (
        patch("aggregator_podcast.generate._get_interest_profile", return_value=""),
        patch("aggregator_podcast.generate._get_candidate_threads", return_value=_minimal_candidates(5)),
        patch("aggregator_podcast.generate._build_continuity_block", return_value=""),
        patch("aggregator_podcast.generate._call_selection_llm", side_effect=capture_call),
    ):
        run_selection_phase(session, settings)

    system_content = captured_messages[0]["content"]
    assert "3 stories" in system_content
