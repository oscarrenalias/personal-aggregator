---
name: Migrate podcast TTS off gpt-4o-mini-tts
id: spec-aba2b326
description: "Migrate podcast TTS from deprecated gpt-4o-mini-tts to gpt-realtime-2.1-mini over WebSocket, with a fidelity-validation gate and Gemini TTS as the audition alternative"
dependencies: null
priority: high
complexity: null
status: draft
tags:
- podcast
- tts
- migration
- openai
scope:
  in: null
  out: null
feature_root_id: null
---
# Migrate podcast TTS off gpt-4o-mini-tts

## Background and deadline

OpenAI is shutting down `gpt-4o-mini-tts` on **2027-01-06**. Both dated snapshots
(`gpt-4o-mini-tts-2025-03-20`, `gpt-4o-mini-tts-2025-12-15`) are deprecated, so the
undated alias we pin stops working on the same date. `tts-1` and `tts-1-hd` share that
date, so neither is a bridge. Runway from the time of writing (2026-10-02) is **~3 months**.

Every episode depends on this model: `PODCAST_TTS_MODEL` default in
`packages/aggregator-podcast/src/aggregator_podcast/config.py:18`, called from
`tts.py::_synthesize_chunk()`.

**If this migration does not land, the podcast stops producing audio.** There is no
fallback inside the OpenAI `/v1/audio/speech` family.

## Evidence we gathered ourselves (live probes, 2026-10-02)

Spike script: `scripts/podcast_tts_spike.py`. All probes hit `/v1/audio/speech` with a
78-character input.

| Model | Voice | instructions? | Result |
|---|---|---|---|
| `gpt-4o-mini-tts` | marin | yes | ✓ 75,264 bytes — baseline works |
| `gpt-realtime-2.1-mini` | marin | yes | ✗ 404 `Invalid URL (POST /v1/audio/speech)` |
| `gpt-4o-tts` | marin | yes | ✗ 404 model does not exist |
| `tts-1` / `tts-1-hd` | nova | yes | ✓ work, accept `instructions` |

Chat Completions audio probes (`gpt-4o-audio-preview` and similar) all 404'd — not
accessible with our key.

**Conclusion:** `gpt-realtime-2.1-mini` is not reachable via the REST speech endpoint. A
WebSocket adapter is required.

## Rejected options

- **ElevenLabs** — good API fit and quality, but ~$2.40–$3.60 per episode PAYG.
  Rejected on cost for a personal project.
- **`tts-1` / `tts-1-hd` as a bridge** — same 2027-01-06 shutdown. Buys no time and
  degrades delivery control. Not an option.
- **`gpt-audio-1.5` via Chat Completions** — one-shot REST, so architecturally simpler
  than WebSocket, but ~$64/M audio output tokens. Far more expensive than Realtime.
- **`litellm.speech()` as an abstraction layer** — verified by reading
  `ElevenLabsTextToSpeechConfig.get_supported_openai_params()` that `instructions` is not
  propagated to non-OpenAI providers. Does not solve the hard part; revisit only if we
  end up wanting provider portability for its own sake.
- **Waiting for a direct OpenAI batch replacement** — none announced, and the whole
  `/v1/audio/speech` family is being retired at once. Not plannable.

## Chosen direction: `gpt-realtime-2.1-mini` over WebSocket

### Why it wins

- **Keeps the `marin` voice** — no user-visible voice change.
- **Per-response delivery guidance is supported**, so per-segment `instructions` survive.
- **~$0.29/episode (~$9/month)** — roughly 67% more than `gpt-4o-mini-tts`'s ~$0.17,
  which is immaterial in absolute terms.
- Existing segmentation, silence padding, and ffmpeg assembly all survive.

### Protocol details

> **Provenance:** the protocol specifics in this section come from a third-party model
> answer based on OpenAI's current documentation, **not from our own execution**. Treat
> every event name, field, and figure below as needing confirmation against a live call
> during the first implementation bead. Where it conflicts with the docs, the docs win.

Connect:

```
wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1-mini
Authorization: Bearer $OPENAI_API_KEY
```

Use a normal server API key. Do **not** send the obsolete `OpenAI-Beta: realtime=v1`
header.

Event sequence per episode:

1. Receive `session.created`.
2. Send `session.update` (voice, output format, base instructions); await `session.updated`.
3. Per segment: send `response.create`; accumulate every `response.output_audio.delta`
   (base64 PCM); await `response.done`.
4. **Accept the segment only if `response.done` carries `response.status == "completed"`.**

Session config:

```json
{
  "type": "session.update",
  "session": {
    "type": "realtime",
    "instructions": "<base instruction>",
    "output_modalities": ["audio"],
    "audio": {
      "input": {"turn_detection": null},
      "output": {
        "voice": "marin",
        "format": {"type": "audio/pcm", "rate": 24000}
      }
    }
  }
}
```

Per-segment response, isolated from conversation history:

```json
{
  "type": "response.create",
  "response": {
    "conversation": "none",
    "instructions": "<full base instruction + segment modifiers>",
    "input": [
      {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "<segment text>"}]
      }
    ]
  }
}
```

Both `conversation: "none"` **and** an explicit `input` array are required —
`conversation: "none"` alone does not give an isolated input context. `response.instructions`
overrides session instructions for that response only and is **not** appended to them, so
send the complete instruction string every time.

### Constraints and behaviours to design around

- **Output is PCM16 24 kHz mono only** (`audio/pcm`; also `pcmu`/`pcma` G.711). No MP3,
  WAV, AAC or Opus. This costs us almost nothing: we already generate silence at
  `anullsrc=r=24000:cl=mono` and encode with `libmp3lame`.
- **Encode once at the end.** Rather than per-segment MP3 then concat, concatenate raw PCM
  plus PCM silence and encode the whole episode in a single ffmpeg pass. This is *better*
  than the current pipeline — it removes a per-segment encode and avoids re-encoding
  during concat.
- **Session duration cap is 60 minutes.** 17 short sequential responses fit comfortably;
  reconnect per daily job.
- **Voice is immutable once a session has produced audio.** Set it in `session.update`.
- **Do not set a low output-token cap.** Leave `max_output_tokens` unbounded (`"inf"`);
  the model's ceiling is 32,000, not the older 4,096.
- **Our 1000-char sentence split is no longer required** by an input limit, but keep it:
  short segments bound retry cost and make fidelity checks tractable.
- **Rate limits** — published Tier 1 is 200 RPM / 40,000 TPM, but our project's limits are
  authoritative. Handle `rate_limits.updated` and use bounded exponential backoff with
  jitter.

## The main risk: this is a generative model, not a deterministic TTS

`gpt-realtime-2.1-mini` generates audio rather than mechanically reading text. It may
**paraphrase, omit, or add commentary**, and `instructions` are guidance rather than a
contract. For a *news* podcast this is a correctness problem, not just a quality one —
names, numbers, dates and quotations have to come out verbatim.

This is the single biggest unknown in the migration and the reason for an explicit
validation gate.

**Mitigation, to be implemented, not optional:**

- Subscribe to `response.output_audio_transcript.delta` and assemble the model's own
  transcript per segment.
- Compare it against the normalised source text and fail or retry the segment on
  material divergence. Pay particular attention to the **final sentence** — truncation
  has bitten this pipeline before (see below).
- The generated transcript is *not* acoustic proof. During migration, listen to
  representative episodes and independently verify a sample.

Related prior incident worth preserving: we previously used `stream_to_file()`, which
silently dropped the final audio chunk and truncated the last sentence of every episode.
The current code uses explicit `iter_bytes(chunk_size=4096)`. The WebSocket path
reintroduces the same failure class in new clothing — hence requiring
`response.status == "completed"` rather than inferring completion from a terminal event.

## Audition alternative: Google `gemini-3.8-flash-lite-tts`

Worth comparing before committing, and **cheaper**:

| | gpt-realtime-2.1-mini | gemini-3.8-flash-lite-tts |
|---|---|---|
| Cost / 12-min episode | ~$0.288 | ~$0.108 now, ~$0.216 from Jan 2027 |
| Designed for | conversational agents | **narration / exact recitation** |
| Delivery control | per-response `instructions` | style field, supplied separately from text |
| Voice | keeps `marin` | different voice — user-visible change |
| Output | PCM16 24 kHz | WAV (unary) |
| Transport | WebSocket session | one-shot request |

Its documentation explicitly targets exact recitation and batch production, which speaks
directly to the fidelity risk above. Against that: a voice change, an unverified quality
bar, a different SDK and auth, and it is labelled Preview with a price rise already
announced for January 2027.

Not tested — no `GOOGLE_API_KEY` in the environment.

## Plan

1. **Build the Realtime adapter** behind the existing `generate_audio()` interface.
   Replace `_synthesize_chunk()`; keep sentence splitting, silence padding and the
   script-walking logic. Confirm every protocol detail above against live calls as you go.
2. **Switch assembly to PCM-concat-then-single-encode.**
3. **Implement the transcript fidelity check** with retry on divergence.
4. **Generate several complete episodes and listen to them.** Compare against current
   `gpt-4o-mini-tts` output for voice match and narration fidelity.
5. **Decision gate.** If fidelity is unacceptable, audition `gemini-3.8-flash-lite-tts`
   before committing further. Do not skip straight to a full Gemini migration without
   having compared.
6. Make the provider/model selectable by config so the decision is reversible.

## Files that would change

| File | Change |
|---|---|
| `packages/aggregator-podcast/src/aggregator_podcast/tts.py` | Replace `_synthesize_chunk()` with a WebSocket adapter; PCM accumulation; transcript capture; switch to concat-PCM-then-encode-once |
| `packages/aggregator-podcast/src/aggregator_podcast/config.py` | `podcast_tts_model` default; any new transport/validation settings |
| `packages/aggregator-podcast/pyproject.toml` | Add a WebSocket client dependency (e.g. `websocket-client`, or `websockets.sync` to stay synchronous — `loop.py` is sync) |
| `CLAUDE.md` | `PODCAST_TTS_MODEL` / `PODCAST_TTS_VOICE` docs; note PCM-to-MP3 assembly |
| `.env.example` | Updated defaults |
| `scripts/podcast_tts.py` | Reference harness updated to the new path |

## Acceptance criteria

- [ ] A full episode synthesises end-to-end over WebSocket with no truncation; the final
  sentence of the last segment is present in the audio.
- [ ] Every segment is accepted only on `response.done` with `status == "completed"`;
  a disconnect, timeout, `failed`, `cancelled` or `incomplete` response never yields a
  finished segment file.
- [ ] Per-segment delivery guidance demonstrably takes effect — audio is differentiated by
  segment type and pace, not flat.
- [ ] Transcript fidelity check is in place and fails/retries on material divergence from
  the source text.
- [ ] A failed segment retries from scratch; partial PCM is discarded, never appended to.
- [ ] Successful segments persist so a reconnect does not regenerate the whole episode.
- [ ] `PODCAST_TTS_MODEL` default points at a non-deprecated model.
- [ ] Measured per-episode cost logged from `response.done` usage and within ~$0.30.
- [ ] A human has listened to at least one complete episode and signed off on voice and
  fidelity.

## Pending decisions

- **Realtime vs Gemini** — resolve at the step-5 gate, on fidelity and voice quality.
  Cost favours Gemini; voice continuity and architectural familiarity favour Realtime.
- **Sync or async WebSocket client.** `loop.py` is synchronous; `websocket-client` or
  `websockets.sync.client` keeps it that way. An `asyncio.run()` wrapper is also fine if
  contained to `tts.py`.
- **How strict the fidelity check should be.** Exact match will false-positive on
  normalisation differences; too loose and paraphrasing slips through. Needs calibration
  against real output.
