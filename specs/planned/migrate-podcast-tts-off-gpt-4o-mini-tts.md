---
name: Migrate podcast TTS off gpt-4o-mini-tts
id: spec-aba2b326
description: Migrate podcast TTS to Google gemini-3.8-flash-lite-tts with the Kore voice and per-topic delivery styles driven by a constrained topic_category enum; validated by POC and listening tests
dependencies: null
priority: high
complexity: null
status: planned
tags:
- podcast
- tts
- migration
- google
scope:
  in: null
  out: null
feature_root_id: B-cc05dece
---
# Migrate podcast TTS off gpt-4o-mini-tts

## Objective

Replace the deprecated `gpt-4o-mini-tts` with **Google `gemini-3.8-flash-lite-tts`**
using the **`Kore`** voice, with **per-topic delivery styles** driven by a constrained
`topic_category` enum, in `packages/aggregator-podcast/src/aggregator_podcast/tts.py`.

This decision is already validated by a POC (see Evidence). The implementation is
mechanical; the unknowns have been resolved.

## Deadline

`gpt-4o-mini-tts` shuts down **2027-01-06**. Both dated snapshots are deprecated, and
`tts-1`/`tts-1-hd` share the date, so there is no bridge inside the OpenAI speech family.
**If this does not land, the podcast stops producing audio.**

## Evidence from the POC (2026-10-02)

POC scripts (uncommitted, in `scripts/`): `podcast_tts_gemini_poc.py` (synthesis),
`podcast_tts_gemini_check.py` (transcribe-and-diff fidelity check). SDK: `google-genai` 2.27.0.

### Verbatim recitation — confirmed

The core worry about generative audio models is paraphrasing. Tested with deliberately
number- and name-dense production copy (16 digits, model names, prices, percentages),
then transcribed the output and diffed against source:

- All 9 source numbers present in the audio.
- No omissions, no paraphrasing, no added commentary.
- Word-sequence similarity 0.935; every apparent divergence was a *Whisper transcription
  convention* ("2 dollars" → "$2", "10 cents" → "0.10"), not a Gemini error — confirmed
  by identical digit sequences across independent clips.

### Delivery control — works, but is not being used

Three mechanisms were tried:

| Mechanism | Result |
|---|---|
| `GenerateContentConfig.system_instruction` | ✗ 400 `Developer instruction is not enabled for this model` |
| Style text prepended to the prompt | ✗ model **reads the directive aloud** as the first sentence |
| `Part.speech_metadata.style` | ✓ works — obeyed, not spoken |

`Part.speech_metadata.style` is the correct mechanism and is confirmed working: style
words absent from the transcribed audio, digits preserved, duration shifted.

**Pace-related wording is the thing to avoid.** A first attempt —
*"Read as a calm, authoritative news anchor. Measured pace, clear articulation, neutral
tone."* — ran **+33%** longer and was rejected on listening as too slow. Tone-only
phrasings behave very differently, but the effect on pace is **not** negligible and
varies per phrasing (measured −2% to −14% against baseline). Duration is a useful smoke
test; it is not a substitute for listening to each one.

### Agreed per-topic styles (from listening tests)

| Style string | Pace vs baseline | Verdict |
|---|---|---|
| *(none — unstyled baseline)* | — | good, generic |
| "Serious, factual, authoritative." | −2.9% | good |
| "Curious and engaged, brighter tone." | −14.1% | **"so much better"** for tech |
| "Lively and engaged, but still a straight news report." | −7.4% | chosen for motorsport |
| "Brisk and energetic, with the crispness of a sports desk. Still factual." | −12.0% | rejected |
| "Energetic and upbeat." | −5.3% | rejected — too light-hearted |
| "Warm and conversational, with a hint of wry humour." | −3.0% | rejected — indistinguishable from baseline |
| "Relaxed and knowledgeable, lightly enthusiastic." | −2.3% | not needed (see below) |

**Final mapping:**

| `topic_category` | Style |
|---|---|
| `World Politics` | `Serious, factual, authoritative.` |
| `AI & Technology` | `Curious and engaged, brighter tone.` |
| `Motorsport` | `Lively and engaged, but still a straight news report.` |
| **everything else** | **no style** — send no `speech_metadata` at all |

Unmapped categories (`Business & Finance`, `Gaming`, `Science`, `Other`, and anything new)
fall back to the **unstyled baseline**, which the listening test rated good as a generic
tone. `Gaming` is deliberately left unmapped: its candidate style was barely
distinguishable from baseline and there have been zero gaming articles in production so
far. Prefer a few clearly-different tones over many near-identical ones.

### Voice

**`Kore`** — chosen by listening test. It is also the voice used for the fidelity test
above, so the verbatim result applies to the shipping voice.

Also confirmed working: `Puck`, `Charon`, `Aoede`, `Zephyr`, `Orus`.

### Audio format

Response returns a **complete WAV container** (mime `audio/wav`), **not raw PCM**.
Verified with ffprobe: `pcm_s16le, 24000 Hz, mono, 16-bit`.

This exactly matches the existing silence generation (`anullsrc=r=24000:cl=mono`), which
enables a simpler and better assembly path (see Changes).

> Trap encountered during the POC: the returned bytes were initially treated as raw PCM
> and wrapped in a second WAV header, putting 44 bytes of header into the audio as a
> click. **Write the returned bytes straight to disk.** Guard with a `data[:4] == b"RIFF"`
> check rather than assuming.

### Rate limits, latency, cost

- **Free tier: 10 requests/minute** for this model — a 429 was hit during the POC.
  An episode is ~17 segments, so this needs throttling or a paid tier.
- Latency 7–17s per segment, scaling with text length.
- **~32 audio tokens/second** measured (760 tokens / 23.85s; 1405 / 44.01s — consistent).
  A 12-minute episode is therefore ~23,000 audio tokens.
- Cost: Google's pricing page is **not yet verified against this measurement**. Third-party
  figures suggested ~$0.108 per 12-min episode now, doubling from January 2027. At the
  measured (higher) token rate, expect roughly ~$0.14 now and ~$0.28 after the increase —
  i.e. **cost parity with the OpenAI Realtime option**, not an advantage. Confirm against
  Google's published rates during implementation and record the real figure.

## Why not `gpt-realtime-2.1-mini` (OpenAI's own recommendation)

- **Not reachable via `/v1/audio/speech`** — verified: `404 Invalid URL (POST /v1/audio/speech)`.
  Would require a WebSocket session adapter.
- **Generative, with no fidelity guarantee.** Instructions are guidance, not a contract;
  it may paraphrase or add commentary. For news copy that is a correctness risk, and it
  would oblige us to build and tune a transcript-divergence validator as a *required*
  safety net. Gemini demonstrably recites verbatim, so that machinery is not needed.
- **No cost advantage** (~$0.288/episode vs Gemini's ~$0.28 post-January).
- Architecturally heavier: session management, PCM accumulation, completion-event handling.

Trade-off accepted: Gemini means a **voice change away from `marin`**, which is
user-visible. The listening test judged `Kore` acceptable, so this is a known, accepted cost.

Also rejected: **ElevenLabs** (~$2.40–3.60/episode — too expensive), **`gpt-audio-1.5`**
(~$64/M audio output tokens), **`tts-1`/`tts-1-hd`** (same shutdown date, no time bought),
**`litellm.speech()`** (verified it does not propagate `instructions` to non-OpenAI
providers; solves nothing here).

## Changes

### 1. Dependency and config

- Add `google-genai` to `packages/aggregator-podcast/pyproject.toml`.
- `GOOGLE_API_KEY` becomes required for the podcast service. Document in `.env.example`
  and `CLAUDE.md`, and add it to the `podcast` service environment in
  `docker-compose.yml` and `docker-compose.prod.yml` (both already pass `env_file: .env`,
  so confirm whether an explicit entry is needed).
- `config.py` defaults: `podcast_tts_model = "gemini-3.8-flash-lite-tts"`,
  `podcast_tts_voice = "Kore"`.
- Add `podcast_tts_style_map` — a JSON object mapping `topic_category` to a style string,
  following the existing `CLUSTERER_SECTION_TITLE_BLOCKLIST` convention for JSON env vars.
  Default:

  ```json
  {
    "World Politics": "Serious, factual, authoritative.",
    "AI & Technology": "Curious and engaged, brighter tone.",
    "Motorsport": "Lively and engaged, but still a straight news report."
  }
  ```

  A category absent from the map means **send no `speech_metadata` at all** — not an
  empty style string. Match on the exact `topic_category` value; since the enum is
  constrained (see below) exact matching is sufficient and a miss degrades safely to
  the unstyled baseline.
- Add `podcast_tts_requests_per_minute: int = 10` to pace calls against the quota.
- `podcast_tts_max_chars_per_chunk` stays at 1000. It is no longer enforced by a hard
  input limit (a 1234-char segment synthesised in one call), but short segments bound
  retry cost. Keep it.

### 2. Constrain the `topic_category` enum

`generate.py:373` currently *suggests* the category list in the Phase 2 prompt:

```
"topic_category": "<e.g. AI & Technology, World Politics, Business & Finance, Motorsport, Gaming, Science, Other>",
```

Change `e.g.` to a hard constraint so the model must choose from the list, e.g.
`"<one of: AI & Technology, World Politics, Business & Finance, Motorsport, Gaming,
Science, Other>"`. Keep the same seven values — they already drive the category badges in
the web UI (`_podcast_detail.html`), so constraining the enum makes those consistent too
rather than changing anything user-visible.

This is what makes the style map total and deterministic: the model can no longer invent
a label the map has never seen.

### 3. Replace the synthesis call in `tts.py`

Replace `_synthesize_chunk()`. Verified working shape:

```python
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

part = types.Part(text=segment_text)
style = settings.podcast_tts_style_map.get(segment.get("topic_category", ""))
if style:
    part.speech_metadata = types.SpeechMetadata(style=style)

resp = client.models.generate_content(
    model=settings.podcast_tts_model,
    contents=[types.Content(role="user", parts=[part])],
    config=types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=settings.podcast_tts_voice
                )
            )
        ),
    ),
)

data = resp.candidates[0].content.parts[0].inline_data.data   # complete WAV bytes
```

Write `data` directly to a `.wav` file. Do **not** re-wrap it.

Log `resp.usage_metadata.candidates_token_count` per segment so real cost is observable
(mirrors how the podcast service already logs LLM usage).

### 4. Switch assembly to WAV-concat-then-single-encode

Currently each segment is encoded to MP3 and the MP3s are concatenated. Since Gemini
returns WAV at exactly the silence generator's format, do this instead:

- Generate silence padding as **WAV** (`anullsrc=r=24000:cl=mono`, `-c:a pcm_s16le`)
  rather than MP3.
- Concatenate all segment and silence WAVs with ffmpeg `concat` + `-c copy`.
- Encode the final episode to MP3 **once** with `libmp3lame`.

This removes a per-segment encode and avoids re-encoding during concat — strictly better
than the current pipeline.

### 5. Rate limiting and retries

- Pace requests to stay within `podcast_tts_requests_per_minute`.
- Treat HTTP 429 as retryable with bounded exponential backoff and jitter; the error body
  carries a `retryDelay`, so honour it when present.
- A failed segment retries from scratch. **Never append to partial audio.**
- Keep the existing behaviour of failing the episode (via `fail_podcast`) if a segment
  cannot be produced, rather than shipping truncated audio.

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-podcast/src/aggregator_podcast/tts.py` | Replace `_synthesize_chunk()` with the Gemini call; WAV passthrough; WAV silence; concat-then-single-encode; rate limiting and 429 retry |
| `packages/aggregator-podcast/src/aggregator_podcast/generate.py` | Line ~373: constrain the `topic_category` enum (`e.g.` → `one of`) |
| `packages/aggregator-podcast/src/aggregator_podcast/config.py` | Model/voice defaults; add `podcast_tts_style_map`, `podcast_tts_requests_per_minute` |
| `packages/aggregator-podcast/pyproject.toml` | Add `google-genai`; drop `openai` if nothing else in the package uses it |
| `.env.example` | `GOOGLE_API_KEY`; updated TTS defaults |
| `CLAUDE.md` | `PODCAST_TTS_MODEL`/`PODCAST_TTS_VOICE` defaults, new vars, `GOOGLE_API_KEY` requirement |
| `docker-compose.yml`, `docker-compose.prod.yml` | Ensure `GOOGLE_API_KEY` reaches the podcast container |
| `scripts/podcast_tts.py` | Update the reference harness to the Gemini path |

## Acceptance Criteria

1. A full ~17-segment episode synthesises end-to-end and produces a valid MP3; the final
   sentence of the last segment is audible (this pipeline has truncated before).
2. Transcribing the produced episode and diffing against `script_json` shows **no missing
   numbers** from any segment.
3. Every `topic_category` emitted by Phase 2 is one of the seven constrained enum values.
4. A segment whose `topic_category` is in `podcast_tts_style_map` is sent the mapped
   style; one that is not (including intro, transition and outro segments, which carry no
   `topic_category`) is sent **no `speech_metadata` key at all** — not an empty string.
5. No style directive text appears in the audio of any segment. Verify by transcribing and
   checking for style vocabulary ("authoritative", "brisk", "engaged", …).
6. Segment audio is written straight from the API response with no double WAV wrapping;
   a `RIFF` guard is present.
7. Final MP3 is produced by a single encode pass; intermediate segment files are WAV.
8. A 429 is retried with backoff and the episode still completes.
9. `duration_seconds` on the episode row continues to be the ffprobe-measured value, not
   an estimate.
10. Per-segment audio token usage is logged; a real per-episode cost figure is recorded in
   this spec or the commit message.
11. `PODCAST_TTS_MODEL` default is a non-deprecated model.

## Risks

- **Voice change from `marin` to `Kore` is user-visible.** Accepted after a listening
  test, but it is the main perceptual change.
- **`gemini-3.8-flash-lite-tts` is labelled Preview**, with a price increase already
  announced for January 2027. Keep model and voice configurable so a switch to
  `gemini-3.8-flash-tts` or another voice is a config change.
- **Fidelity is empirically good but not contractually guaranteed** — it is still a
  generative model. Criterion 2 exists to catch regressions; consider keeping a
  lightweight transcript check if it proves cheap, though it is deliberately not required
  given the POC result.
- **Quota.** 10 req/min on free tier is tight for a 17-segment episode. Confirm whether a
  paid tier is needed before the first scheduled production run.

## Pending Decisions

- **Verify real pricing** against Google's published rates and the measured ~32 audio
  tokens/second, and decide whether a paid tier is required for quota headroom.
- **Whether to keep a transcript fidelity check** in production. Not required by the POC
  evidence, but cheap insurance against a model update changing behaviour.
- **Whether transitions warrant their own style.** Transitions exist to signal a topic
  change, so a distinct tone would reinforce their purpose — but they currently carry no
  `topic_category` and so fall through to unstyled. Judge this only after hearing a full
  assembled episode; adding it is a one-line change to the map lookup.
- **Styles were auditioned on a single story in isolation.** They have not been heard
  back-to-back inside a real episode, where the transition between a tech segment
  (−14% pace) and a world-politics one (−3%) may be more noticeable than it is in
  isolation. If the shifts feel jarring, narrow the spread rather than abandoning the
  feature.
