#!/usr/bin/env python3
"""
Convert podcast_script.json → MP3 using Gemini TTS.

Usage:
  uv run --all-packages python scripts/podcast_tts.py
  uv run --all-packages python scripts/podcast_tts.py --input podcast_script.json --output podcast.mp3
  uv run --all-packages python scripts/podcast_tts.py --voice Puck --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

# Bootstrap: load .env so GOOGLE_API_KEY is available
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "aggregator-common" / "src"))
from aggregator_common.env import load_env
load_env()

from google import genai
from google.genai import types

MODEL = "gemini-3.8-flash-lite-tts"
DEFAULT_VOICE = "Kore"

# Style map: topic_category → TTS style string. Mirrors PODCAST_TTS_STYLE_MAP default.
_DEFAULT_STYLE_MAP: dict[str, str] = {
    "World Politics": "Serious and measured, as befits weighty international affairs.",
    "AI & Technology": "Curious and engaged, with a forward-looking tone.",
    "Motorsport": "Lively and energetic, conveying the excitement of racing.",
}

# Silence (seconds) inserted BEFORE a segment, keyed on pause_before hint.
PAUSE_DURATIONS = {
    "none":   0.0,
    "short":  0.6,
    "medium": 1.0,
    "long":   1.8,
}

FINAL_SILENCE = 0.5


def _load_style_map() -> dict[str, str]:
    raw = os.environ.get("PODCAST_TTS_STYLE_MAP", "")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return _DEFAULT_STYLE_MAP


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


_MAX_INPUT_CHARS = 1000


def _split_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into chunks ≤ max_chars, breaking only at sentence boundaries."""
    import re
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, current = [], ""
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
    voice: str,
    out_path: str,
    style_map: dict[str, str],
) -> None:
    part = types.Part(text=text)
    style = style_map.get(seg.get("topic_category", ""))
    if style:
        part.speech_metadata = types.SpeechMetadata(style=style)

    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Content(role="user", parts=[part])],
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice
                    )
                )
            ),
        ),
    )

    data = resp.candidates[0].content.parts[0].inline_data.data
    if data[:4] != b"RIFF":
        raise RuntimeError(f"TTS response is not a WAV file (header: {data[:4]!r})")

    with open(out_path, "wb") as f:
        f.write(data)


def _synthesize(
    text: str,
    seg: dict,
    client: genai.Client,
    voice: str,
    out_path: str,
    tmp_dir: str,
    style_map: dict[str, str],
) -> None:
    """TTS with automatic sentence-boundary splitting for long inputs."""
    chunks = _split_sentences(text, _MAX_INPUT_CHARS)
    if len(chunks) == 1:
        _synthesize_chunk(text, seg, client, voice, out_path, style_map)
        return

    chunk_paths = []
    for i, chunk_text in enumerate(chunks):
        cp = os.path.join(tmp_dir, f"{os.path.basename(out_path)}.part{i}.wav")
        _synthesize_chunk(chunk_text, seg, client, voice, cp, style_map)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert podcast JSON script to MP3 via Gemini TTS")
    parser.add_argument("--input", default="podcast_script.json")
    parser.add_argument("--output", default=f"podcast_{date.today()}.mp3")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        help="Gemini TTS voice name (default: Kore)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print segment plan without making any API calls")
    args = parser.parse_args()

    script_path = Path(args.input)
    if not script_path.exists():
        sys.exit(f"Input file not found: {script_path}")

    data = json.loads(script_path.read_text())
    segments = [s for s in data.get("segments", []) if s.get("text", "").strip()]

    print(f"Script: {data.get('episode_theme', '(no theme)')}")
    print(f"Date:   {data.get('date', '?')}  |  Est. duration: "
          f"{data.get('duration_estimate_seconds', 0) // 60}m "
          f"{data.get('duration_estimate_seconds', 0) % 60}s")
    print(f"Model:  {MODEL}  |  Voice: {args.voice}  |  Output: {args.output}")
    print()

    if args.dry_run:
        total_chars = 0
        for i, seg in enumerate(segments):
            text = seg.get("text", "")
            hints = seg.get("tts_hints", {})
            print(f"  [{i+1:02d}] {seg['type']:<12} "
                  f"pause_before={hints.get('pause_before','none'):<7} "
                  f"pace={hints.get('pace','normal'):<7} "
                  f"chars={len(text)}")
            total_chars += len(text)
        print(f"\nTotal: {total_chars} chars across {len(segments)} segments")
        return

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        sys.exit("GOOGLE_API_KEY not set in .env or environment")
    client = genai.Client(api_key=api_key)
    style_map = _load_style_map()

    with tempfile.TemporaryDirectory() as tmp_dir:
        parts: list[str] = []
        silence_cache: dict[float, str] = {}

        def get_silence(duration: float) -> str:
            if duration not in silence_cache:
                silence_cache[duration] = _make_silence(duration, tmp_dir)
            return silence_cache[duration]

        total_chars = sum(len(s.get("text", "")) for s in segments)
        done_chars = 0

        for i, seg in enumerate(segments):
            text = seg.get("text", "").strip()
            seg_type = seg.get("type", "story")
            hints = seg.get("tts_hints", {})
            pause_key = hints.get("pause_before", "none")
            pause_secs = PAUSE_DURATIONS.get(pause_key, 0.0)

            if pause_secs > 0:
                parts.append(get_silence(pause_secs))

            done_chars += len(text)
            pct = done_chars * 100 // total_chars
            label = seg.get("headline", seg_type) if seg_type == "story" else seg_type
            print(f"  [{i+1:02d}/{len(segments)}] {seg_type:<12} {len(text):>5} chars  "
                  f"[{pct:>3}%]  {label[:55]}", end=" ... ", flush=True)

            seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.wav")
            _synthesize(text, seg, client, args.voice, seg_path, tmp_dir, style_map)
            parts.append(seg_path)
            print("✓")

        if FINAL_SILENCE > 0:
            parts.append(get_silence(FINAL_SILENCE))

        list_path = os.path.join(tmp_dir, "concat.txt")
        with open(list_path, "w") as f:
            for p in parts:
                f.write(f"file '{p}'\n")

        print(f"\nConcatenating {len(parts)} chunks → {args.output} ...")
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-codec:a", "libmp3lame", "-q:a", "2",
                args.output,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(f"ffmpeg failed (exit {result.returncode})")

        size_kb = Path(args.output).stat().st_size // 1024
        print(f"Done: {args.output}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
