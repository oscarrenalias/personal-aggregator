"""Unit tests for aggregator_podcast.tts — Gemini TTS migration."""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**kwargs):
    """Return a PodcastSettings with a dummy DATABASE_URL and any overrides."""
    from aggregator_podcast.config import PodcastSettings

    defaults = dict(DATABASE_URL="postgresql://x:x@localhost/x")
    defaults.update(kwargs)
    return PodcastSettings(**defaults)


def _make_wav_response(data: bytes = b"RIFF\x00\x00\x00\x00WAVEfmt "):
    """Return a mock genai response whose inline_data.data is *data*."""
    part = MagicMock()
    part.inline_data.data = data
    candidate = MagicMock()
    candidate.content.parts = [part]
    resp = MagicMock()
    resp.candidates = [candidate]
    resp.usage_metadata.candidates_token_count = 42
    return resp


# ---------------------------------------------------------------------------
# 1. RIFF guard
# ---------------------------------------------------------------------------

class TestRiffGuard:
    def test_raises_when_response_is_not_wav(self, tmp_path):
        """_synthesize_chunk raises RuntimeError when the response header is not RIFF."""
        from aggregator_podcast.tts import _synthesize_chunk

        settings = _make_settings()
        client = MagicMock()
        client.models.generate_content.return_value = _make_wav_response(b"\x00\x00\x00\x00junk")

        seg = {"topic_category": "AI & Technology", "text": "Hello world."}
        out_path = str(tmp_path / "out.wav")

        with pytest.raises(RuntimeError, match="not a WAV"):
            _synthesize_chunk(
                "Hello world.", seg, client,
                model="gemini-3.8-flash-lite-tts",
                voice="Kore",
                out_path=out_path,
                settings=settings,
            )

        # File must NOT have been written when the guard triggers
        assert not (tmp_path / "out.wav").exists()

    def test_succeeds_when_response_is_valid_wav(self, tmp_path):
        """_synthesize_chunk writes the WAV file when the header is RIFF."""
        from aggregator_podcast.tts import _synthesize_chunk

        settings = _make_settings()
        client = MagicMock()
        wav_data = b"RIFF" + b"\x00" * 40
        client.models.generate_content.return_value = _make_wav_response(wav_data)

        seg = {"topic_category": "", "text": "Hello."}
        out_path = str(tmp_path / "out.wav")

        _synthesize_chunk(
            "Hello.", seg, client,
            model="gemini-3.8-flash-lite-tts",
            voice="Kore",
            out_path=out_path,
            settings=settings,
        )

        assert (tmp_path / "out.wav").exists()
        assert (tmp_path / "out.wav").read_bytes() == wav_data


# ---------------------------------------------------------------------------
# 2. Style-map lookup
# ---------------------------------------------------------------------------

class TestStyleMap:
    def test_mapped_category_sets_speech_metadata(self, tmp_path):
        """A segment with a mapped topic_category passes speech_metadata in the Part."""
        from aggregator_podcast.tts import _synthesize_chunk
        from google.genai import types

        style_map = {"World Politics": "Serious, factual."}
        settings = _make_settings(podcast_tts_style_map=style_map)
        client = MagicMock()
        wav_data = b"RIFF" + b"\x00" * 40
        client.models.generate_content.return_value = _make_wav_response(wav_data)

        seg = {"topic_category": "World Politics", "text": "Big news today."}
        out_path = str(tmp_path / "out.wav")

        captured_contents = []

        def capture_call(**kwargs):
            captured_contents.extend(kwargs.get("contents", []))
            return _make_wav_response(wav_data)

        client.models.generate_content.side_effect = capture_call

        _synthesize_chunk(
            "Big news today.", seg, client,
            model="gemini-3.8-flash-lite-tts",
            voice="Kore",
            out_path=out_path,
            settings=settings,
        )

        assert len(captured_contents) == 1
        part = captured_contents[0].parts[0]
        assert part.speech_metadata is not None
        assert part.speech_metadata.style == "Serious, factual."

    def test_unmapped_category_sends_no_speech_metadata(self, tmp_path):
        """A segment with a category not in the style map produces a Part without speech_metadata."""
        from aggregator_podcast.tts import _synthesize_chunk

        style_map = {"World Politics": "Serious, factual."}
        settings = _make_settings(podcast_tts_style_map=style_map)
        client = MagicMock()
        wav_data = b"RIFF" + b"\x00" * 40

        captured_contents = []

        def capture_call(**kwargs):
            captured_contents.extend(kwargs.get("contents", []))
            return _make_wav_response(wav_data)

        client.models.generate_content.side_effect = capture_call

        seg = {"topic_category": "Sports", "text": "Game recap."}
        out_path = str(tmp_path / "out.wav")

        _synthesize_chunk(
            "Game recap.", seg, client,
            model="gemini-3.8-flash-lite-tts",
            voice="Kore",
            out_path=out_path,
            settings=settings,
        )

        assert len(captured_contents) == 1
        part = captured_contents[0].parts[0]
        # When category is unmapped the Part must NOT have speech_metadata set
        assert not part.speech_metadata

    def test_absent_topic_category_sends_no_speech_metadata(self, tmp_path):
        """A segment with no topic_category key at all produces a Part without speech_metadata."""
        from aggregator_podcast.tts import _synthesize_chunk

        style_map = {"World Politics": "Serious, factual."}
        settings = _make_settings(podcast_tts_style_map=style_map)
        client = MagicMock()
        wav_data = b"RIFF" + b"\x00" * 40

        captured_contents = []

        def capture_call(**kwargs):
            captured_contents.extend(kwargs.get("contents", []))
            return _make_wav_response(wav_data)

        client.models.generate_content.side_effect = capture_call

        seg = {}  # no topic_category key
        out_path = str(tmp_path / "out.wav")

        _synthesize_chunk(
            "No category segment.", seg, client,
            model="gemini-3.8-flash-lite-tts",
            voice="Kore",
            out_path=out_path,
            settings=settings,
        )

        assert len(captured_contents) == 1
        part = captured_contents[0].parts[0]
        assert not part.speech_metadata


# ---------------------------------------------------------------------------
# 3. Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_inter_call_delay_respects_rpm(self):
        """_pace_request sleeps when calls are made faster than the configured RPM."""
        import aggregator_podcast.tts as tts_mod

        rpm = 6  # 10 s interval
        tts_mod._last_tts_call = 0.0  # reset module-level state

        sleep_calls: list[float] = []

        with patch("aggregator_podcast.tts.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with patch("aggregator_podcast.tts.time.monotonic", side_effect=[
                # First call: elapsed = 100 s (far past interval) → no sleep
                200.0,  # monotonic() inside _pace_request for elapsed calc
                200.0,  # monotonic() to set _last_tts_call
                # Second call: only 0.5 s elapsed since last call → should sleep ~9.5 s
                200.5,  # monotonic() for elapsed calc
                210.5,  # monotonic() to set _last_tts_call after sleep
            ]):
                tts_mod._pace_request(rpm)   # first call — no sleep expected
                tts_mod._pace_request(rpm)   # second call — sleep expected

        assert len(sleep_calls) == 1
        # Should have slept close to (10.0 - 0.5) = 9.5 s
        assert abs(sleep_calls[0] - 9.5) < 0.01

    def test_no_sleep_when_rpm_is_zero(self):
        """_pace_request does nothing when requests_per_minute is 0."""
        import aggregator_podcast.tts as tts_mod

        with patch("aggregator_podcast.tts.time.sleep") as mock_sleep:
            tts_mod._pace_request(0)

        mock_sleep.assert_not_called()

    def test_no_sleep_when_sufficient_time_has_passed(self):
        """_pace_request does not sleep when enough time has elapsed since the last call."""
        import aggregator_podcast.tts as tts_mod

        tts_mod._last_tts_call = 0.0

        with patch("aggregator_podcast.tts.time.sleep") as mock_sleep:
            with patch("aggregator_podcast.tts.time.monotonic", side_effect=[1000.0, 1000.0]):
                tts_mod._pace_request(60)  # 1 s interval; 1000 s have passed

        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# 4. 429 retry
# ---------------------------------------------------------------------------

class TestRetry429:
    def test_succeeds_after_two_429_errors(self, tmp_path):
        """_synthesize_chunk retries on 429 and returns successfully on the third attempt."""
        from google.genai import errors as genai_errors
        from aggregator_podcast.tts import _synthesize_chunk

        settings = _make_settings(podcast_tts_requests_per_minute=0)  # disable rate limiting
        wav_data = b"RIFF" + b"\x00" * 40

        # ClientError(code, response_json, response=None)
        err_429 = genai_errors.ClientError(429, {"message": "Too many requests"})

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise err_429
            return _make_wav_response(wav_data)

        client = MagicMock()
        client.models.generate_content.side_effect = side_effect

        seg = {"topic_category": "", "text": "Retry test."}
        out_path = str(tmp_path / "out.wav")

        with patch("aggregator_podcast.tts.time.sleep"):  # suppress actual sleeping
            _synthesize_chunk(
                "Retry test.", seg, client,
                model="gemini-3.8-flash-lite-tts",
                voice="Kore",
                out_path=out_path,
                settings=settings,
            )

        assert call_count == 3
        assert (tmp_path / "out.wav").exists()

    def test_non_429_error_reraises_immediately(self, tmp_path):
        """A non-429 ClientError is re-raised without retrying."""
        from google.genai import errors as genai_errors
        from aggregator_podcast.tts import _synthesize_chunk

        settings = _make_settings(podcast_tts_requests_per_minute=0)

        err_500 = genai_errors.ClientError(500, {"message": "Internal error"})

        client = MagicMock()
        client.models.generate_content.side_effect = err_500

        seg = {"topic_category": "", "text": "Error test."}
        out_path = str(tmp_path / "out.wav")

        with pytest.raises(genai_errors.ClientError):
            _synthesize_chunk(
                "Error test.", seg, client,
                model="gemini-3.8-flash-lite-tts",
                voice="Kore",
                out_path=out_path,
                settings=settings,
            )

        assert client.models.generate_content.call_count == 1

    def test_429_exhausted_raises_after_max_retries(self, tmp_path):
        """_synthesize_chunk raises after _MAX_RETRIES consecutive 429 errors."""
        from google.genai import errors as genai_errors
        from aggregator_podcast.tts import _synthesize_chunk, _MAX_RETRIES

        settings = _make_settings(podcast_tts_requests_per_minute=0)

        err_429 = genai_errors.ClientError(429, {"message": "Too many requests"})

        client = MagicMock()
        client.models.generate_content.side_effect = err_429

        seg = {"topic_category": "", "text": "Max retry test."}
        out_path = str(tmp_path / "out.wav")

        with patch("aggregator_podcast.tts.time.sleep"):
            with pytest.raises(genai_errors.ClientError):
                _synthesize_chunk(
                    "Max retry test.", seg, client,
                    model="gemini-3.8-flash-lite-tts",
                    voice="Kore",
                    out_path=out_path,
                    settings=settings,
                )

        # _MAX_RETRIES attempts + 1 initial = _MAX_RETRIES + 1 total calls
        assert client.models.generate_content.call_count == _MAX_RETRIES + 1

    def test_retry_delay_from_error_body_is_honoured(self, tmp_path):
        """retryDelay in the 429 error body is used as the minimum wait time."""
        from google.genai import errors as genai_errors
        from aggregator_podcast.tts import _synthesize_chunk

        settings = _make_settings(podcast_tts_requests_per_minute=0)
        wav_data = b"RIFF" + b"\x00" * 40

        # Pass a raw string as response_json so str(exc) contains JSON-quoted retryDelay
        err_429 = genai_errors.ClientError(429, '{"retryDelay": "5.0s"}')

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise err_429
            return _make_wav_response(wav_data)

        client = MagicMock()
        client.models.generate_content.side_effect = side_effect

        seg = {"topic_category": ""}
        out_path = str(tmp_path / "out.wav")

        sleep_calls: list[float] = []
        with patch("aggregator_podcast.tts.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            _synthesize_chunk(
                "Retry delay test.", seg, client,
                model="gemini-3.8-flash-lite-tts",
                voice="Kore",
                out_path=out_path,
                settings=settings,
            )

        assert len(sleep_calls) == 1
        # sleep duration should be >= 5.0 (retryDelay) plus small jitter
        assert sleep_calls[0] >= 5.0


# ---------------------------------------------------------------------------
# 5. Config field parsing
# ---------------------------------------------------------------------------

class TestConfigParsing:
    def test_default_style_map_has_expected_keys(self):
        """PodcastSettings default podcast_tts_style_map has the documented categories."""
        settings = _make_settings()
        assert "World Politics" in settings.podcast_tts_style_map
        assert "AI & Technology" in settings.podcast_tts_style_map
        assert "Motorsport" in settings.podcast_tts_style_map

    def test_style_map_parsed_from_json_env_var(self, monkeypatch):
        """PODCAST_TTS_STYLE_MAP env var is parsed as JSON into a dict."""
        from aggregator_podcast.config import PodcastSettings

        json_val = '{"Tech": "Curious and bright.", "Finance": "Calm and precise."}'
        monkeypatch.setenv("PODCAST_TTS_STYLE_MAP", json_val)

        settings = PodcastSettings(DATABASE_URL="postgresql://x:x@localhost/x")

        assert settings.podcast_tts_style_map == {
            "Tech": "Curious and bright.",
            "Finance": "Calm and precise.",
        }

    def test_empty_style_map_json_env_var(self, monkeypatch):
        """PODCAST_TTS_STYLE_MAP='{}'  results in an empty dict (no speech_metadata for any segment)."""
        from aggregator_podcast.config import PodcastSettings

        monkeypatch.setenv("PODCAST_TTS_STYLE_MAP", "{}")

        settings = PodcastSettings(DATABASE_URL="postgresql://x:x@localhost/x")

        assert settings.podcast_tts_style_map == {}


# ---------------------------------------------------------------------------
# 6. Filename uniqueness (regression for same-date collision bug)
# ---------------------------------------------------------------------------

class TestFilenameUniqueness:
    """Two episodes sharing a date must write to distinct paths and not overwrite each other."""

    def _make_script(self, iso_date: str) -> dict:
        return {
            "iso_date": iso_date,
            "segments": [
                {
                    "type": "intro",
                    "text": "Hello.",
                    "tts_hints": {"pause_before": "none"},
                }
            ],
        }

    def _patch_generate_audio(self, monkeypatch, tmp_path):
        """Patch all external calls inside generate_audio so no real TTS/ffmpeg runs."""
        import aggregator_podcast.tts as tts_mod

        wav_data = b"RIFF" + b"\x00" * 40

        def fake_synthesize(text, seg, client, model, voice, out_path, tmp_dir, max_chars, settings):
            with open(out_path, "wb") as f:
                f.write(wav_data)

        def fake_make_silence(duration, tmp_dir):
            p = os.path.join(tmp_dir, f"silence_{duration:.2f}s.wav")
            with open(p, "wb") as f:
                f.write(wav_data)
            return p

        def fake_subprocess_run(cmd, **kwargs):
            # Simulate ffmpeg concat/encode: write dummy MP3 bytes to the output file.
            # The output path is the last positional argument that does not start with '-'.
            for arg in reversed(cmd):
                if not arg.startswith("-"):
                    with open(arg, "wb") as f:
                        f.write(b"\xff\xfb" + b"\x00" * 100)  # dummy MP3 header
                    break
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
        monkeypatch.setattr(tts_mod, "_synthesize", fake_synthesize)
        monkeypatch.setattr(tts_mod, "_make_silence", fake_make_silence)
        monkeypatch.setattr("aggregator_podcast.tts.subprocess.run", fake_subprocess_run)
        monkeypatch.setattr("google.genai.Client", MagicMock)

    def test_different_episode_ids_produce_different_paths(self, tmp_path, monkeypatch):
        """Two episodes with the same date but different ids must write to distinct paths."""
        self._patch_generate_audio(monkeypatch, tmp_path)
        from aggregator_podcast.tts import generate_audio

        settings = _make_settings()
        script = self._make_script("2026-10-03")

        path_a, _ = generate_audio(script, tmp_path, settings, episode_id=10759)
        path_b, _ = generate_audio(script, tmp_path, settings, episode_id=11651)

        assert path_a != path_b
        assert "10759" in path_a
        assert "11651" in path_b

    def test_audio_path_matches_file_on_disk(self, tmp_path, monkeypatch):
        """The returned audio_path must point to a file that actually exists."""
        self._patch_generate_audio(monkeypatch, tmp_path)
        from aggregator_podcast.tts import generate_audio

        settings = _make_settings()
        script = self._make_script("2026-10-03")

        path, size = generate_audio(script, tmp_path, settings, episode_id=42)

        assert os.path.exists(path), f"audio file not found at {path}"
        assert size > 0

    def test_second_same_date_episode_leaves_first_file_intact(self, tmp_path, monkeypatch):
        """Generating a second same-date episode must not overwrite the first episode's file."""
        self._patch_generate_audio(monkeypatch, tmp_path)
        from aggregator_podcast.tts import generate_audio

        settings = _make_settings()
        script = self._make_script("2026-10-03")

        path_a, _ = generate_audio(script, tmp_path, settings, episode_id=10759)
        first_content = open(path_a, "rb").read()

        path_b, _ = generate_audio(script, tmp_path, settings, episode_id=11651)

        assert os.path.exists(path_a), "First episode's file was deleted"
        assert open(path_a, "rb").read() == first_content, "First episode's file was overwritten"
        assert path_a != path_b


# ---------------------------------------------------------------------------
# 7. Integration test (requires live GOOGLE_API_KEY)
# ---------------------------------------------------------------------------

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")


@pytest.mark.skipif(not GOOGLE_API_KEY, reason="GOOGLE_API_KEY not set — live TTS test skipped")
def test_generate_audio_live(tmp_path):
    """Integration: generate_audio produces a non-empty MP3 from a minimal script."""
    from aggregator_podcast.tts import generate_audio

    settings = _make_settings(
        podcast_tts_model="gemini-3.8-flash-lite-tts",
        podcast_tts_voice="Kore",
        podcast_tts_max_chars_per_chunk=1000,
        podcast_tts_requests_per_minute=10,
    )

    script = {
        "iso_date": "2026-01-01",
        "segments": [
            {"type": "intro", "text": "Welcome to today's briefing.", "tts_hints": {"pause_before": "none"}},
        ],
    }

    audio_path, audio_size = generate_audio(script, tmp_path, settings, episode_id=1)

    assert audio_path.endswith(".mp3")
    assert audio_size > 0
    assert os.path.exists(audio_path)
