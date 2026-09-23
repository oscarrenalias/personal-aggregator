"""TTS synthesis: converts a podcast script JSON dict to an MP3 file."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import openai

from aggregator_podcast.config import PodcastSettings

BASE_INSTRUCTION = (
    "Read this as a professional daily news podcast. "
    "Calm, authoritative, and conversational. Moderate pace. "
    "Use brief natural pauses between sentences. "
    "Emphasise names, numbers, and major developments with subtle inflection. "
    "Never sound theatrical or overly enthusiastic. "
    "Slight change in cadence when moving between topics."
)

_TYPE_INSTRUCTION: dict[str, str] = {
    "intro":      "This is the programme opening. Warm and welcoming, slightly measured pace to draw the listener in.",
    "story":      "This is a news story segment. Clear, direct, fact-forward. Let the content carry the weight.",
    "transition": "This is a brief signpost between topics. Slightly lighter cadence, one natural breath of separation.",
    "outro":      "This is the programme closing. Warm and unhurried. Slightly slower than the main stories.",
}

_PACE_INSTRUCTION: dict[str, str] = {
    "slow": " Speak noticeably slower than your default pace.",
    "fast": " Slightly brisker pace than normal.",
}

PAUSE_DURATIONS: dict[str, float] = {
    "none":   0.0,
    "short":  0.6,
    "medium": 1.0,
    "long":   1.8,
}

FINAL_SILENCE = 0.5


def _build_instruction(seg: dict) -> str:
    hints = seg.get("tts_hints", {})
    seg_type = seg.get("type", "story")
    instruction = BASE_INSTRUCTION + " " + _TYPE_INSTRUCTION.get(seg_type, _TYPE_INSTRUCTION["story"])
    pace = hints.get("pace", "normal")
    instruction += _PACE_INSTRUCTION.get(pace, "")
    return instruction


def _make_silence(duration: float, tmp_dir: str) -> str:
    path = os.path.join(tmp_dir, f"silence_{duration:.2f}s.mp3")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(duration),
            "-q:a", "9", "-acodec", "libmp3lame",
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
    instructions: str,
    client: openai.OpenAI,
    model: str,
    voice: str,
    out_path: str,
) -> None:
    with client.audio.speech.with_streaming_response.create(
        model=model,
        voice=voice,
        input=text,
        instructions=instructions,
        response_format="mp3",
    ) as response:
        with open(out_path, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=4096):
                f.write(chunk)


def _synthesize(
    text: str,
    instructions: str,
    client: openai.OpenAI,
    model: str,
    voice: str,
    out_path: str,
    tmp_dir: str,
    max_chars: int,
) -> None:
    """TTS with automatic sentence-boundary splitting for long inputs."""
    chunks = _split_sentences(text, max_chars)
    if len(chunks) == 1:
        _synthesize_chunk(text, instructions, client, model, voice, out_path)
        return

    chunk_paths = []
    for i, chunk_text in enumerate(chunks):
        cp = os.path.join(tmp_dir, f"{os.path.basename(out_path)}.part{i}.mp3")
        _synthesize_chunk(chunk_text, instructions, client, model, voice, cp)
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
) -> tuple[str, int]:
    """Synthesise an MP3 from a podcast script dict.

    Returns (audio_path, audio_size_bytes).
    """
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAPI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in environment")
    client = openai.OpenAI(api_key=api_key)

    episode_date = script_json.get("date", "unknown")
    output_filename = f"podcast_{episode_date}.mp3"
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

            instructions = _build_instruction(seg)
            seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.mp3")
            _synthesize(
                text,
                instructions,
                client,
                settings.podcast_tts_model,
                settings.podcast_tts_voice,
                seg_path,
                tmp_dir,
                settings.podcast_tts_max_chars_per_chunk,
            )
            parts.append(seg_path)

        if FINAL_SILENCE > 0:
            parts.append(get_silence(FINAL_SILENCE))

        list_path = os.path.join(tmp_dir, "concat.txt")
        with open(list_path, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-c", "copy",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr}")

    audio_size_bytes = output_path.stat().st_size
    return str(output_path), audio_size_bytes
