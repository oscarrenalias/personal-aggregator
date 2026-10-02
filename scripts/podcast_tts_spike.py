#!/usr/bin/env python3
"""
TTS replacement spike: probe candidate models against the /v1/audio/speech endpoint.

Tests:
  1. Baseline: gpt-4o-mini-tts with instructions (current pipeline)
  2. gpt-realtime-2.1-mini via /v1/audio/speech (OpenAI's recommended successor)
  3. gpt-4o-tts (full-size, if it exists)
  4. tts-1 + tts-1-hd with instructions (deprecated siblings; document instruction support)

Usage:
  uv run --all-packages python scripts/podcast_tts_spike.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Bootstrap: load .env so OPENAI_API_KEY is available
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "aggregator-common" / "src"))
from aggregator_common.env import load_env
load_env()

import openai

PROBE_TEXT = "This is a brief test of news narration audio quality and delivery control."

BASE_INSTRUCTION = (
    "Read this as a professional daily news podcast. "
    "Calm, authoritative, and conversational. Moderate pace."
)

CANDIDATES = [
    {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "instructions": BASE_INSTRUCTION,
        "note": "Current model (being deprecated 2028-01-06). Baseline.",
    },
    {
        "model": "gpt-realtime-2.1-mini",
        "voice": "marin",
        "instructions": BASE_INSTRUCTION,
        "note": "OpenAI recommended successor. Expect failure: docs say /v1/audio/speech is NOT supported.",
    },
    {
        "model": "gpt-4o-tts",
        "voice": "marin",
        "instructions": BASE_INSTRUCTION,
        "note": "Full-size gpt-4o TTS variant (if it exists).",
    },
    {
        "model": "tts-1",
        "voice": "nova",
        "instructions": BASE_INSTRUCTION,
        "note": "Legacy tts-1 (also deprecated 2028-01-06). Instructions param may not be supported.",
    },
    {
        "model": "tts-1-hd",
        "voice": "nova",
        "instructions": BASE_INSTRUCTION,
        "note": "Legacy tts-1-hd (also deprecated 2028-01-06). Higher quality, same endpoint.",
    },
    {
        "model": "tts-1",
        "voice": "nova",
        "instructions": None,
        "note": "tts-1 without instructions param (documents whether it's optional).",
    },
]


def probe_model(client: openai.OpenAI, model: str, voice: str, instructions: str | None, out_path: str) -> tuple[bool, str]:
    """Try to synthesise PROBE_TEXT via /v1/audio/speech. Returns (success, detail)."""
    kwargs: dict = {
        "model": model,
        "voice": voice,
        "input": PROBE_TEXT,
        "response_format": "mp3",
    }
    if instructions is not None:
        kwargs["instructions"] = instructions

    try:
        with client.audio.speech.with_streaming_response.create(**kwargs) as response:
            with open(out_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=4096):
                    f.write(chunk)
        size = Path(out_path).stat().st_size
        return True, f"OK — {size} bytes written to {out_path}"
    except openai.BadRequestError as e:
        return False, f"BadRequestError: {e}"
    except openai.AuthenticationError as e:
        return False, f"AuthenticationError: {e}"
    except openai.NotFoundError as e:
        return False, f"NotFoundError (model probably doesn't exist): {e}"
    except openai.UnprocessableEntityError as e:
        return False, f"UnprocessableEntityError: {e}"
    except openai.APIStatusError as e:
        return False, f"APIStatusError {e.status_code}: {e.message}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        sys.exit("OPENAI_API_KEY not set in .env or environment")

    client = openai.OpenAI(api_key=api_key)

    print("=" * 72)
    print("TTS Replacement Spike — probing /v1/audio/speech candidates")
    print(f"Probe text: {PROBE_TEXT!r}")
    print("=" * 72)
    print()

    with tempfile.TemporaryDirectory() as tmp_dir:
        for i, cand in enumerate(CANDIDATES):
            model = cand["model"]
            voice = cand["voice"]
            instructions = cand["instructions"]
            note = cand["note"]
            out_path = os.path.join(tmp_dir, f"probe_{i}_{model.replace('/', '_')}.mp3")

            instr_label = "(with instructions)" if instructions is not None else "(WITHOUT instructions)"
            print(f"[{i+1}/{len(CANDIDATES)}] {model} / voice={voice} {instr_label}")
            print(f"         Note: {note}")

            success, detail = probe_model(client, model, voice, instructions, out_path)
            status = "✓ PASS" if success else "✗ FAIL"
            print(f"         {status}: {detail}")
            print()

    print("=" * 72)
    print("Spike complete.")
    print()
    print("Summary for spec:")
    print("  - gpt-realtime-2.1-mini: Realtime WebSocket only; /v1/audio/speech unsupported per docs")
    print("  - tts-1/tts-1-hd: also deprecated 2027-01-06; instructions param probably unsupported")
    print("  - Gemini TTS: batch synthesis OK, but WAV only (no MP3); needs GOOGLE_API_KEY")
    print("  - ElevenLabs: not tested (no API key); strong batch synthesis + delivery control")


if __name__ == "__main__":
    main()
