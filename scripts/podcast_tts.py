#!/usr/bin/env python3
"""
Convert podcast_script.json → MP3 using OpenAI gpt-4o-mini-tts with delivery instructions.

Usage:
  uv run --all-packages python scripts/podcast_tts.py
  uv run --all-packages python scripts/podcast_tts.py --input podcast_script.json --output podcast.mp3
  uv run --all-packages python scripts/podcast_tts.py --voice cedar --dry-run
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

# Bootstrap: load .env so OPENAI_API_KEY is available
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "aggregator-common" / "src"))
from aggregator_common.env import load_env
load_env()

import openai

MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "marin"

# Base delivery instruction shared across all segments.
BASE_INSTRUCTION = (
    "Read this as a professional daily news podcast. "
    "Calm, authoritative, and conversational. Moderate pace. "
    "Use brief natural pauses between sentences. "
    "Emphasise names, numbers, and major developments with subtle inflection. "
    "Never sound theatrical or overly enthusiastic. "
    "Slight change in cadence when moving between topics."
)

# Per-type additions layered on top of the base instruction.
_TYPE_INSTRUCTION: dict[str, str] = {
    "intro":      "This is the programme opening. Warm and welcoming, slightly measured pace to draw the listener in.",
    "story":      "This is a news story segment. Clear, direct, fact-forward. Let the content carry the weight.",
    "transition": "This is a brief signpost between topics. Slightly lighter cadence, one natural breath of separation.",
    "outro":      "This is the programme closing. Warm and unhurried. Slightly slower than the main stories.",
}

# Pace modifier appended when the hint asks for something other than normal.
_PACE_INSTRUCTION: dict[str, str] = {
    "slow":  " Speak noticeably slower than your default pace.",
    "fast":  " Slightly brisker pace than normal.",
}

# Silence (seconds) inserted BEFORE a segment, keyed on pause_before hint.
PAUSE_DURATIONS = {
    "none":   0.0,
    "short":  0.6,
    "medium": 1.0,
    "long":   1.8,
}

FINAL_SILENCE = 0.5


def _build_instruction(seg: dict) -> str:
    hints = seg.get("tts_hints", {})
    instruction = BASE_INSTRUCTION
    seg_type = seg.get("type", "story")
    instruction += " " + _TYPE_INSTRUCTION.get(seg_type, _TYPE_INSTRUCTION["story"])
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


# gpt-4o-mini-tts input text limit (characters). Segments exceeding this are
# split at sentence boundaries to avoid silent truncation.
_MAX_INPUT_CHARS = 1000


def _synthesize_chunk(text: str, instructions: str, client: openai.OpenAI, voice: str, out_path: str) -> None:
    """Single TTS call; writes raw bytes to avoid stream_to_file flush issues."""
    with client.audio.speech.with_streaming_response.create(
        model=MODEL,
        voice=voice,
        input=text,
        instructions=instructions,
        response_format="mp3",
    ) as response:
        with open(out_path, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=4096):
                f.write(chunk)


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


def _synthesize(text: str, instructions: str, client: openai.OpenAI, voice: str, out_path: str, tmp_dir: str) -> None:
    """TTS with automatic sentence-boundary splitting for long inputs."""
    chunks = _split_sentences(text, _MAX_INPUT_CHARS)
    if len(chunks) == 1:
        _synthesize_chunk(text, instructions, client, voice, out_path)
        return

    # Multiple chunks: synthesise each then concatenate with ffmpeg
    chunk_paths = []
    for i, chunk_text in enumerate(chunks):
        cp = os.path.join(tmp_dir, f"{os.path.basename(out_path)}.part{i}.mp3")
        _synthesize_chunk(chunk_text, instructions, client, voice, cp)
        chunk_paths.append(cp)

    list_path = out_path + ".parts.txt"
    with open(list_path, "w") as f:
        for p in chunk_paths:
            f.write(f"file '{p}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
        check=True, capture_output=True,
    )
    os.unlink(list_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert podcast JSON script to MP3")
    parser.add_argument("--input", default="podcast_script.json")
    parser.add_argument("--output", default=f"podcast_{date.today()}.mp3")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        choices=["marin", "cedar", "alloy", "echo", "fable", "onyx", "nova", "shimmer", "ash", "ballad", "coral", "sage", "verse"],
                        help="OpenAI TTS voice (default: marin)")
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
        # gpt-4o-mini-tts pricing: $0.60/1M input tokens + $12/1M audio output tokens
        # ~4 chars per token for English prose; audio output priced per token of audio
        est_input_tokens = total_chars / 4
        print(f"\nTotal: {total_chars} chars  ~{est_input_tokens:.0f} input tokens")
        print(f"Est. text-input cost: ~${est_input_tokens / 1_000_000 * 0.60:.5f}")
        print("(Audio output cost depends on generated duration, billed separately at $12/1M audio tokens)")
        return

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAPI_API_KEY", "")
    if not api_key:
        sys.exit("OPENAI_API_KEY not set in .env or environment")
    client = openai.OpenAI(api_key=api_key)

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

            instructions = _build_instruction(seg)
            seg_path = os.path.join(tmp_dir, f"seg_{i:03d}.mp3")
            _synthesize(text, instructions, client, args.voice, seg_path, tmp_dir)
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
                "-c", "copy",
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
