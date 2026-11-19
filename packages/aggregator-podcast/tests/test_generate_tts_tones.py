"""Tests for TTS tone integration in generate.py (AC[2], AC[3], AC[7])."""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")


def _make_settings(**kwargs):
    from aggregator_podcast.config import PodcastSettings

    defaults = dict(DATABASE_URL=os.environ["DATABASE_URL"])
    defaults.update(kwargs)
    return PodcastSettings(**defaults)


# ---------------------------------------------------------------------------
# AC[2]: _SEGMENT_SYSTEM contains all TTS_TONES members
# ---------------------------------------------------------------------------

def test_segment_system_contains_all_tts_tones():
    """_SEGMENT_SYSTEM prompt contains every tone value from TTS_TONES."""
    from aggregator_podcast.generate import _SEGMENT_SYSTEM
    from aggregator_podcast.config import TTS_TONES

    for tone in TTS_TONES:
        assert tone in _SEGMENT_SYSTEM, (
            f"TTS tone {tone!r} not found in _SEGMENT_SYSTEM prompt; "
            f"the LLM cannot produce it if it's not in the enum"
        )


def test_segment_system_tone_enum_covers_all_tts_tones():
    """The pipe-separated tone enum embedded in _SEGMENT_SYSTEM lists exactly TTS_TONES."""
    from aggregator_podcast.generate import _SEGMENT_SYSTEM, _TONE_ENUM
    from aggregator_podcast.config import TTS_TONES

    assert _TONE_ENUM == "|".join(TTS_TONES)
    assert _TONE_ENUM in _SEGMENT_SYSTEM


def test_segment_system_contains_vulnerability_guidance():
    """_SEGMENT_SYSTEM must mention 'vulnerability' in the tone-choice guidance."""
    from aggregator_podcast.generate import _SEGMENT_SYSTEM

    assert "vulnerability" in _SEGMENT_SYSTEM


def test_segment_system_contains_when_unsure_guidance():
    """_SEGMENT_SYSTEM must contain 'when unsure' guidance for the neutral default."""
    from aggregator_podcast.generate import _SEGMENT_SYSTEM

    assert "when unsure" in _SEGMENT_SYSTEM.lower()


# ---------------------------------------------------------------------------
# AC[3] + AC[7]: assembled intro / transition / outro carry tts_hints.tone == 'neutral'
# ---------------------------------------------------------------------------

def test_assembled_intro_has_neutral_tone():
    """_assemble_script sets tts_hints.tone='neutral' on the intro segment (AC[3], AC[7])."""
    from aggregator_podcast.generate import _assemble_script

    segments = [
        {
            "thread_id": 1,
            "headline": "Big story",
            "topic_category": "AI & Technology",
            "text": "Tech happened.",
            "tts_hints": {"pause_before": "short", "pace": "normal", "tone": "serious"},
            "sources": ["The Verge"],
            "is_developing": False,
        }
    ]
    wrapper = {"intro": "Welcome to the briefing.", "transitions": [], "outro": "That's all."}

    result = _assemble_script(
        "Monday, October 1, 2026", "2026-10-01", "theme", segments, wrapper, "gpt-4.1"
    )

    intro = result["segments"][0]
    assert intro["type"] == "intro"
    assert intro["tts_hints"]["tone"] == "neutral"


def test_assembled_outro_has_neutral_tone():
    """_assemble_script sets tts_hints.tone='neutral' on the outro segment (AC[3], AC[7])."""
    from aggregator_podcast.generate import _assemble_script

    segments = [
        {
            "thread_id": 1,
            "headline": "Story",
            "topic_category": "Other",
            "text": "Something happened.",
            "tts_hints": {"pause_before": "none", "pace": "normal", "tone": "upbeat"},
            "sources": [],
            "is_developing": False,
        }
    ]
    wrapper = {"intro": "Hi.", "transitions": [], "outro": "Goodbye."}

    result = _assemble_script(
        "Tuesday, October 2, 2026", "2026-10-02", "theme", segments, wrapper, "gpt-4.1"
    )

    outro = result["segments"][-1]
    assert outro["type"] == "outro"
    assert outro["tts_hints"]["tone"] == "neutral"


def test_assembled_transitions_have_neutral_tone():
    """_assemble_script sets tts_hints.tone='neutral' on each transition segment (AC[3], AC[7])."""
    from aggregator_podcast.generate import _assemble_script

    segments = [
        {
            "thread_id": i,
            "headline": f"Story {i}",
            "topic_category": "Other",
            "text": f"Story {i} text.",
            "tts_hints": {"pause_before": "short", "pace": "normal", "tone": "grave"},
            "sources": [],
            "is_developing": False,
        }
        for i in range(1, 4)
    ]
    wrapper = {
        "intro": "Welcome.",
        "transitions": ["Moving on.", "And now."],
        "outro": "Goodbye.",
    }

    result = _assemble_script(
        "Wednesday, October 3, 2026", "2026-10-03", "theme", segments, wrapper, "gpt-4.1"
    )

    transitions = [s for s in result["segments"] if s["type"] == "transition"]
    assert len(transitions) == 2, f"expected 2 transitions, got {len(transitions)}"
    for t in transitions:
        assert t["tts_hints"]["tone"] == "neutral", (
            f"transition tts_hints.tone expected 'neutral', got {t['tts_hints'].get('tone')!r}"
        )


def test_assembled_story_tts_hints_passed_through_unchanged():
    """_assemble_script passes story segment tts_hints through without modification."""
    from aggregator_podcast.generate import _assemble_script

    story_hints = {"pause_before": "short", "pace": "slow", "tone": "grave"}
    segments = [
        {
            "thread_id": 1,
            "headline": "Serious story",
            "topic_category": "World Politics",
            "text": "Events unfolded.",
            "tts_hints": story_hints,
            "sources": ["Reuters"],
            "is_developing": True,
        }
    ]
    wrapper = {"intro": "Hi.", "transitions": [], "outro": "Bye."}

    result = _assemble_script(
        "Monday, October 1, 2026", "2026-10-01", "theme", segments, wrapper, "gpt-4.1"
    )

    story_segs = [s for s in result["segments"] if s["type"] == "story"]
    assert len(story_segs) == 1
    assert story_segs[0]["tts_hints"] == story_hints
