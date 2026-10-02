---
name: Migrate podcast TTS off gpt-4o-mini-tts
id: spec-aba2b326
description: Research findings and recommended migration path for replacing gpt-4o-mini-tts (deprecated 2028-01-06) in the podcast pipeline
dependencies: null
priority: medium
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

OpenAI is shutting down `gpt-4o-mini-tts` on **2028-01-06**. Both dated snapshots
(`gpt-4o-mini-tts-2025-03-20`, `gpt-4o-mini-tts-2025-12-15`) are deprecated; the
undated alias we pin will stop working on the same date. That deadline is approximately
15 months from the spike date (2026-10-02).

Every podcast episode depends on this model via the `PODCAST_TTS_MODEL` env var,
defaulting in `packages/aggregator-podcast/src/aggregator_podcast/config.py:18`, and
called from `packages/aggregator-podcast/src/aggregator_podcast/tts.py` via
`client.audio.speech.with_streaming_response.create(...)`.

## What we ran — spike evidence (2026-10-02)

Spike script: `scripts/podcast_tts_spike.py` (created during this investigation).

**All probes used the same `/v1/audio/speech` endpoint and a 78-character text input.**

| Model | Voice | instructions? | Result |
|---|---|---|---|
| `gpt-4o-mini-tts` | marin | yes | ✓ 75 264 bytes — baseline works |
| `gpt-realtime-2.1-mini` | marin | yes | ✗ 404: `Invalid URL (POST /v1/audio/speech)` |
| `gpt-4o-tts` | marin | yes | ✗ 404: `model gpt-4o-tts does not exist or you do not have access` |
| `tts-1` | nova | yes | ✓ 69 504 bytes — API accepts instructions param |
| `tts-1-hd` | nova | yes | ✓ 74 112 bytes — API accepts instructions param |
| `tts-1` | nova | no | ✓ 68 736 bytes |

Additional probes via Chat Completions for audio output
(`gpt-4o-audio-preview`, `gpt-4o-mini-audio-preview`, etc.):
all returned 404 — not accessible with this API key.

**Verbatim error from `gpt-realtime-2.1-mini`:**
```
Error code: 404 - {'detail': {'code': None, 'message': 'Invalid URL (POST /v1/audio/speech)'},
'error': {'code': None, 'message': 'Invalid URL (POST /v1/audio/speech)',
'param': None, 'type': 'invalid_request_error'}}
```

## Options evaluated

### Option A — gpt-realtime-2.1-mini (OpenAI's recommended successor)

**Confirmed incompatible.** OpenAI's own deprecation page recommends this model, but it
only supports the Realtime WebSocket endpoint (`v1/realtime`). Speech generation via
`/v1/audio/speech` is explicitly unsupported per the model card, and confirmed by the
spike (see error above).

The Realtime API is designed for interactive voice agents (WebRTC/WebSocket sessions),
not batch synthesis. Migrating to it would mean:
- Rewriting `tts.py` to manage WebSocket sessions per segment
- Handling async event streams (audio delta events)
- Receiving raw PCM or Opus (not MP3 directly) — encode step required
- Significantly higher architectural complexity for a pipeline that is deliberately simple

**Pricing:** $10/1M audio input tokens, $20/1M audio output tokens.
Audio tokens billing in session mode is harder to estimate per-episode.

**Verdict: Rejected.** Architecturally mismatched for offline batch synthesis. The
official migration recommendation is misleading for this use case.

### Option B — Stay on tts-1 / tts-1-hd (temporary bridge)

Both models also accept the `instructions` parameter (confirmed by spike — no API error).
However, whether `tts-1`/`tts-1-hd` actually *obeys* the instructions (vs. silently
ignoring them) was not verified. The voices available are `alloy`, `echo`, `fable`,
`onyx`, `nova`, `shimmer` — `marin` and `cedar` are `gpt-4o-mini-tts`-only. Switching
voice would be user-visible.

**Critical:** `tts-1` and `tts-1-hd` share the **same 2028-01-06 deprecation date** as
`gpt-4o-mini-tts`. Migrating to either buys no additional time and trades delivery-control
quality for nothing. Config change only.

**Pricing:** `tts-1` $15/1M chars, `tts-1-hd` $30/1M chars (character-based billing,
unlike token-based `gpt-4o-mini-tts`). For a typical episode (~8 000 chars): tts-1
≈ $0.12/day, tts-1-hd ≈ $0.24/day. Current gpt-4o-mini-tts cost is lower.

**Verdict: Rejected** as a destination. Potentially useful as a last-minute stop-gap if
the migration isn't complete by deadline, but that should not be the plan.

### Option C — ElevenLabs (direct SDK)

ElevenLabs is the de-facto standard for high-quality batch narration TTS. It:
- Supports one-shot HTTP synthesis (no session management)
- Natively outputs MP3 — no encode step
- Offers voice stability, style exaggeration, and style-prompt fields for delivery control
- Has 30+ high-quality voices, including voices suited for news narration
- Is built specifically for offline batch use, not interactive agents

**Delivery control:** ElevenLabs provides `voice_settings` (stability, similarity_boost,
style, use_speaker_boost) and, on Flash v2.5+, a natural-language `style_prompt` field
analogous to `instructions`. This is roughly equivalent expressiveness.

**Not tested** (no `ELEVENLABS_API_KEY` in this environment). API key acquisition cost is
low.

**Pricing:** approximately $0.30/1 000 characters on pay-as-you-go, or subscription tiers
with lower per-char cost at volume. A 12-minute episode is roughly 8 000–12 000 chars →
$2.40–$3.60/episode PAYG, or dramatically less on a Creator/Pro subscription.
This is significantly more expensive than gpt-4o-mini-tts at comparable quality.

**Migration size:** Moderate. `tts.py` would need to replace the OpenAI SDK call with the
ElevenLabs SDK. The `_build_instruction()` function logic stays the same but maps to
ElevenLabs `style_prompt`. Voice changes required (marin → ElevenLabs equivalent).

**Verdict:** Viable as a migration target. Cost increase is notable; worth evaluating
whether subscription pricing fits the deployment profile.

### Option D — Google Gemini TTS (gemini-3.8-flash-tts)

Google released batch TTS via Gemini. It:
- Supports one-shot synthesis (not conversational-only)
- Has delivery control via `speech_metadata.style` (free-text style field) and inline
  markers like `<pause>` — broadly equivalent to `instructions`
- Offers 30 studio voices plus extended library and voice design
- **Does NOT support MP3 output** — only WAV, PCM, mu-law, A-law at up to 24 kHz

The WAV-only limitation matters: we'd need an ffmpeg transcode step per segment (or per
episode). We already use ffmpeg for concat, so this is mechanically possible, but adds
latency and a dependency failure mode.

**Not tested** (no `GOOGLE_API_KEY` in this environment).

**Pricing:** not documented in accessible pages; assumed competitive with OpenAI for
inference-class models.

**Migration size:** Moderate-large. Different SDK (google-generativeai), different auth
(GOOGLE_API_KEY or ADC), no MP3 — encode step in `tts.py`.

**Verdict:** Secondary option. The WAV-only gap is annoying but solvable. Worth keeping
in mind if ElevenLabs cost is prohibitive.

### Option E — Route TTS through litellm.speech()

`litellm` (already installed in the stack) provides a `litellm.speech()` function that
abstracts providers: confirmed support for `openai`, `elevenlabs`, `vertex_ai`, `azure`,
`google` (litellm v1.88.1, discovered during spike).

**The case for it:** switching the TTS call in `tts.py` from `openai.OpenAI.audio.speech`
to `litellm.speech()` would make future provider migrations a one-line config change.

**The case against it — delivery control:** `litellm`'s ElevenLabs integration lists only
`["voice", "response_format", "speed"]` as supported OpenAI params (verified by reading
`ElevenLabsTextToSpeechConfig.get_supported_openai_params()`). The `instructions`
parameter is **not propagated to ElevenLabs through litellm**. Provider-specific delivery
control would require passing raw kwargs outside the abstraction layer.

**The case against it — streaming:** our current code uses
`with_streaming_response.create()` specifically to iterate bytes without the
`stream_to_file` truncation bug. The litellm path returns `HttpxBinaryResponseContent`;
whether this supports the same streaming iteration was not tested.

**Verdict:** Partial recommendation. Switching to `litellm.speech()` for the OpenAI path
today is low-risk and future-proofs provider switching. But delivery control (`instructions`)
can't be abstracted cleanly across providers, so litellm alone doesn't solve the ElevenLabs
migration.

### Option F — Wait for an OpenAI batch TTS successor

No announcement found. OpenAI is retiring the entire `/v1/audio/speech` endpoint family
simultaneously, suggesting they are not planning a direct replacement in that form.

Given the 14-month runway, this is a reasonable hedge if the intent is to stay on OpenAI.
Check OpenAI's changelog quarterly.

**Verdict:** Not the plan, but monitor the situation.

## Recommendation

**Do nothing to production code today.** `gpt-4o-mini-tts` works and has 14 months until
shutdown. There is no urgency to act immediately.

**Recommended migration path (Q3 2027 deadline, earliest start Q1 2027):**

1. **Evaluate ElevenLabs** with a real API key. The key unknowns are voice quality for news
   narration and cost at the daily episode volume. Run a real test with the
   `scripts/podcast_tts_spike.py` pattern: synthesise one representative episode segment
   per voice candidate and listen to the result.

2. **If ElevenLabs cost/quality is acceptable:** migrate `tts.py` directly to the
   ElevenLabs SDK. The `_build_instruction()` function maps cleanly to their
   `style_prompt`. Delivery control is preserved.

3. **As a preparatory step (low risk, can do now):** refactor `_synthesize_chunk()` in
   `tts.py` to call `litellm.speech()` instead of the OpenAI SDK directly, staying on
   OpenAI for now. This decouples the call from a specific SDK and makes the eventual
   provider swap a config + param mapping change, not a client-library swap.

4. **If ElevenLabs is too expensive:** evaluate Gemini TTS (requires GOOGLE_API_KEY setup
   and accepting a WAV→MP3 transcode per segment).

5. **If no acceptable alternative exists by Q4 2027:** use `tts-1` as a bridge to the
   deadline. Same endpoint, same date, but degrades delivery control (instructions probably
   not respected; marin voice not available).

## Quality risk: voice and delivery control

Any migration off `gpt-4o-mini-tts` is a user-visible change:
- **Voice:** `marin` is `gpt-4o-mini-tts`-specific. Any replacement uses a different voice,
  which may sound different enough to notice.
- **Delivery control:** the `instructions` mechanism is what fixed the "flat audio"
  complaint in the original podcast prototype. Losing it or degrading it (e.g., switching
  to a `speed`-only parameter) would be a quality regression. The migration must preserve
  an equivalent delivery-guidance mechanism.

## Files that would change

| File | Change needed |
|---|---|
| `packages/aggregator-podcast/src/aggregator_podcast/tts.py` | Replace `openai.OpenAI.audio.speech` call with provider-agnostic call; update delivery-instruction mapping |
| `packages/aggregator-podcast/src/aggregator_podcast/config.py` | Update `podcast_tts_model` and `podcast_tts_voice` defaults |
| `packages/aggregator-podcast/pyproject.toml` | Add or swap SDK dependency (ElevenLabs SDK, or none if using litellm) |
| `CLAUDE.md` | Update `PODCAST_TTS_MODEL` and `PODCAST_TTS_VOICE` env var documentation |
| `.env.example` | Update default values |
| `scripts/podcast_tts.py` | Update model name and voice defaults (reference harness) |

## Acceptance criteria (for when this spec becomes a bead)

- [ ] A replacement model/provider is identified and tested end-to-end: full episode
  synthesis completes without truncation, MP3 file is valid and complete
- [ ] Delivery control mechanism verified: audio sounds appropriately paced and
  differentiated by segment type (not flat)
- [ ] `PODCAST_TTS_MODEL` default in `config.py:18` points to a non-deprecated model
- [ ] Cost per episode estimated and acceptable
- [ ] `scripts/podcast_tts.py` updated to use the replacement for reference

## Pending decisions

- **Which provider?** ElevenLabs (quality, cost?), Gemini (WAV issue), or OpenAI batch
  successor if announced.
- **litellm abstraction?** Preparatory litellm.speech() switch worth doing now vs. deferred
  to migration.
- **Voice selection**: which voice from the replacement provider sounds best for news
  narration? Requires a human listening test.
