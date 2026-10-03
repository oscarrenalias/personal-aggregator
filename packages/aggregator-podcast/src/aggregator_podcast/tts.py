"""TTS synthesis: converts a podcast script JSON dict to an MP3 file."""
from __future__ import annotations

import logging
import os
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from aggregator_podcast.config import PodcastSettings

logger = logging.getLogger(__name__)

PAUSE_DURATIONS: dict[str, float] = {
    "none":   0.0,
    "short":  0.6,
    "medium": 1.0,
    "long":   1.8,
}

FINAL_SILENCE = 0.5

_MAX_RETRIES = 5
_BASE_BACKOFF = 1.0
_last_tts_call: float = 0.0


def _pace_request(requests_per_minute: int) -> None:
    """Sleep if necessary to stay within the configured request rate."""
    global _last_tts_call
    if requests_per_minute <= 0:
        return
    interval = 60.0 / requests_per_minute
    elapsed = time.monotonic() - _last_tts_call
    if elapsed < interval:
        time.sleep(interval - elapsed)
    _last_tts_call = time.monotonic()


def _extract_retry_delay(exc: Exception) -> Optional[float]:
    """Return retryDelay seconds from a 429 error body, or None."""
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', str(exc))
    return float(m.group(1)) if m else None


def _make_silence(duration: float, tmp_dir: str) -> str:
    path = os.path.join(tmp_dir, f"silence_{duration:.2f}s.wav")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(duration),
            "-c:a", "pcm_s16le",
            path,
        ],
        check=True,
        capture_output=True,
    )
    return path


def _split_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into chunks ≤ max_chars, breaking only at sentence boundaries."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks: list[str] = []
    current = ""
    for sent in sentences:
        if current and len(current) + 1 + len(sent) > max_chars:
            chunks.append(current.strip())
            current = sent
        else:
            current = (current + " " + sent).strip() if current else sent
    if current:
        chunks.append(current.strip())
    return chunks


def _synthesize_chunk(
    text: str,
    seg: dict,
    client: genai.Client,
    model: str,
    voice: str,
    out_path: str,
    settings: PodcastSettings,
) -> None:
    """Synthesise one text chunk to a WAV file, with rate limiting and 429 retry."""
    part = types.Part(text=text)
    style = settings.podcast_tts_style_map.get(seg.get("topic_category", ""))
    if style:
        part.speech_metadata = types.SpeechMetadata(style=style)

    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    )

    for attempt in range(_MAX_RETRIES + 1):
        _pace_request(settings.podcast_tts_requests_per_minute)
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=[part])],
                config=config,
            )
            break
        except genai_errors.ClientError as exc:
            if exc.code != 429 or attempt >= _MAX_RETRIES:
                raise
            retry_delay = _extract_retry_delay(exc)
            backoff = retry_delay if retry_delay is not None else (_BASE_BACKOFF * (2 ** attempt))
            jitter = random.uniform(0, backoff * 0.1)
            wait = backoff + jitter
            logger.warning(
                "TTS 429 on attempt %d/%d; waiting %.1fs before retry",
                attempt + 1, _MAX_RETRIES, wait,
            )
            time.sleep(wait)

    data = resp.candidates[0].content.parts[0].inline_data.data
    if data[:4] != b"RIFF":
        raise RuntimeError(f"TTS response is not a WAV file (header: {data[:4]!r})")

    logger.info("segment audio tokens: %d", resp.usage_metadata.candidates_token_count)

    with open(out_path, "wb") as f:
        f.write(data)


def _synthesize(
    text: str,
    seg: dict,
    client: genai.Client,
    model: str,
    voice: str,
    out_path: str,
    tmp_dir: str,
    max_chars: int,
    settings: PodcastSettings,
) -> None:
    """TTS with automatic sentence-boundary splitting for long inputs."""
    chunks = _split_sentences(text, max_chars)
    if len(chunks) == 1:
        _synthesize_chunk(text, seg, client, model, voice, out_path, settings)
        return

    chunk_paths = []
    for i, chunk_text in enumerate(chunks):
        cp = os.path.join(tmp_dir, f"{os.path.basename(out_path)}.part{i}.wav")
        _synthesize_chunk(chunk_text, seg, client, model, voice, cp, settings)
        chunk_paths.append(cp)

    list_path = out_path + ".parts.txt"
    with open(list_path, "w") as f:
        for p in chunk_paths:
            f.write(f"file '{p}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
        check=True,
        capture_output=True,
    )
    os.unlink(list_path)


def generate_audio(
    script_json: dict,
    audio_dir: Path,
    settings: PodcastSettings,
    *,
    episode_id: int,
) -> tuple[str, int]:
    """Synthesise an MP3 from a podcast script dict.

    Returns (audio_path, audio_size_bytes).
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set in environment")
    client = genai.Client(api_key=api_key)

    # Include episode_id in the filename to avoid collisions when two episodes share a date.
    episode_date = script_json.get("iso_date") or script_json.get("date", "unknown")
    output_filename = f"podcast_{episode_date}_{episode_id}.mp3"
    output_path = Path(audio_dir) / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    segments = [s for s in script_json.get("segments", []) if s.get("text", "").strip()]

    with tempfile.TemporaryDirectory() as tmp_dir:
        parts: list[str] = []
        silence_cache: dict[float, str] = {}

        def get_silence(duration: float) -> str:
            if duration not in silence_cache:
                silence_cache[duration] = _make_silence(duration, tmp_dir)
            return silence_cache[duration]

        for i, seg in enumerate(segments):
            text = seg.get("text", "").strip()
            hints = seg.get("tts_hints", {})
            pause_key = hints.get("pause_before", "none")
            pause_secs = PAUSE_DURATIONS.get(pause_key, 0.0)

            if pause_secs > 0:
                parts.append(get_silence(pause_secs))

            seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.wav")
            _synthesize(
                text,
                seg,
                client,
                settings.podcast_tts_model,
                settings.podcast_tts_voice,
                seg_path,
                tmp_dir,
                settings.podcast_tts_max_chars_per_chunk,
                settings,
            )
            parts.append(seg_path)

        if FINAL_SILENCE > 0:
            parts.append(get_silence(FINAL_SILENCE))

        list_path = os.path.join(tmp_dir, "concat.txt")
        with open(list_path, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")

        concat_wav = os.path.join(tmp_dir, "concat.wav")
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-c", "copy",
                concat_wav,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed (exit {result.returncode}): {result.stderr}")

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", concat_wav,
                "-codec:a", "libmp3lame", "-q:a", "2",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg encode failed (exit {result.returncode}): {result.stderr}")

    audio_size_bytes = output_path.stat().st_size
    return str(output_path), audio_size_bytes
