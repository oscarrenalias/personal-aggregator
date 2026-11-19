---
name: "Podcast: model-selected TTS tone per segment"
id: spec-a46946a5
description: Segment writer picks a per-story TTS tone from a fixed list; tone map takes precedence over the topic-category style map
dependencies: null
priority: medium
complexity: null
status: done
tags:
- podcast
- tts
- llm
scope:
  in: null
  out: null
feature_root_id: B-e50a7427
---
# Podcast: model-selected TTS tone per segment

## Objective

Let the segment-writing LLM pick the speaking tone for each podcast story based on what the story is about, instead of deriving it from the story's `topic_category`. A story about a critical security vulnerability, a layoff or a death must be read seriously even when its category is "AI & Technology".

## Problems to Fix

1. **Tone is tied to the category, not the content.** `tts.py:97` looks up `seg["topic_category"]` in `PODCAST_TTS_STYLE_MAP` and sends the result as Gemini `speech_metadata.style`. Every "AI & Technology" story gets "Curious and engaged, brighter tone.", so a story about an actively exploited zero-day is read cheerfully. The same applies to "Motorsport" ("Lively…") for a fatal crash.
2. **The model that knows the story has no say in the tone.** The Phase 2 segment writer (`_SEGMENT_SYSTEM`, `generate.py:347`) already reads the full thread context and returns `tts_hints` (`pause_before`, `pace`), but there is no tone hint.

## Design

### Fixed tone vocabulary

The tone is chosen from a fixed list defined once in code (e.g. a module-level tuple `TTS_TONES` in a place importable by both `generate.py` and `tts.py`):

| Tone | Use for | Default style string sent to Gemini |
|---|---|---|
| `grave` | Deaths, disasters, war casualties, violence, tragedies | `"Grave and restrained. Slow, low and steady, with no brightness."` |
| `serious` | Security vulnerabilities and breaches, layoffs, legal action, political conflict, economic downturns, safety failures | `"Serious, measured, authoritative."` |
| `neutral` | Routine factual news with no strong emotional weight | `"Calm, clear, neutral newsreader delivery."` |
| `engaged` | Product launches, research results, interesting developments | `"Curious and engaged, warm but still straight news delivery."` |
| `upbeat` | Clearly positive news: wins, records, celebrations, breakthroughs | `"Bright and upbeat, lively but still a straight news report."` |

A free-text style from the LLM is deliberately not allowed: a fixed list keeps the voice consistent across episodes and lets the operator tune each tone through config.

### Phase 2 prompt change (in place)

Edit `_SEGMENT_SYSTEM` in `generate.py` in place. Podcast prompts are excluded from the shadow-evaluation requirement by the scope exclusion in ADR-9687cb01, so no prompt variant or `LLM_EXPERIMENT` work is needed.

- Add `"tone": "<grave|serious|neutral|engaged|upbeat>"` to the `tts_hints` object in the JSON format. Build the list from `TTS_TONES` so the prompt and code cannot drift.
- Add a short instruction block telling the model:
  - to choose the tone from the substance and stakes of the story, **not** from its topic category;
  - the use-for guidance from the table above, including the explicit example that a technology story about a vulnerability, breach, outage or layoffs is `serious` (or `grave` if people were harmed);
  - when unsure, to choose `neutral`;
  - to keep the written text consistent with the chosen tone (no jokey phrasing in a `grave` segment).

### Non-story segments

`generate.py` (around lines 757–791) sets fixed `tts_hints` for intro, transitions and outro. Add `"tone": "neutral"` to each of them. For story segments, keep the LLM-provided `tts_hints` as today. If the model's `tts_hints` lacks a `tone`, leave it absent; `tts.py` handles the fallback.

### Phase 2.5 polish pass

`run_polish_phase` already replaces only `text` and keeps all other fields, so `tts_hints.tone` survives. Add `tts_hints` to the list of fields the `_POLISH_SYSTEM` prompt says not to touch, and add a test that confirms `tts_hints` (including `tone`) is unchanged after polishing.

### TTS style resolution

Replace the lookup in `_synthesize_chunk` (`tts.py:97`) with a resolver function, e.g. `resolve_style(seg, settings) -> tuple[str | None, str]` that returns `(style, source)`:

1. Read `seg["tts_hints"]["tone"]`, normalised with `.strip().lower()`. If it is in `TTS_TONES` and `PODCAST_TTS_TONE_MAP` has a non-empty entry for it, use that style. `source = "tone"`.
2. Otherwise fall back to the existing `PODCAST_TTS_STYLE_MAP[seg["topic_category"]]` lookup. `source = "category"`. This also keeps scripts produced before this change working.
3. Otherwise send no `speech_metadata`. `source = "none"`.

If a tone is present but not in `TTS_TONES`, log one WARNING naming the bad value and fall back as in step 2.

Resolve the style once per segment (not per chunk) and log one INFO line per segment, e.g. `segment 3: tone=serious style_source=tone`, so the choices can be checked in `docker logs`.

### Config

Add to `PodcastSettings` (`config.py`):

```python
podcast_tts_tone_map: Dict[str, str] = Field(
    default_factory=lambda: { ...defaults from the table above... },
    description="Mapping of LLM-selected tone to TTS style string; takes precedence over podcast_tts_style_map",
)
```

Env var: `PODCAST_TTS_TONE_MAP` (JSON object). Keys that are not in `TTS_TONES` are ignored and produce one WARNING at startup. Setting `PODCAST_TTS_TONE_MAP={}` turns off tone-based styling and goes back to category-only styling, which is the rollback path.

`PODCAST_TTS_STYLE_MAP` stays as it is. It becomes the fallback only.

## Changes

1. Add `TTS_TONES` and the tone instructions to the Phase 2 segment prompt.
2. Set `tone: "neutral"` on intro, transition and outro `tts_hints`.
3. Add `tts_hints` to the polish prompt's do-not-touch list.
4. Add the tone → category → none style resolver in `tts.py`, with per-segment logging.
5. Add the `PODCAST_TTS_TONE_MAP` setting.
6. Docs: add `PODCAST_TTS_TONE_MAP` to the CLAUDE.md config table and `.env.example`. Update the `PODCAST_TTS_STYLE_MAP` descriptions in both to say it is now a fallback used when no valid tone is present. In the CLAUDE.md aggregator-podcast paragraph, mention that the segment writer picks a per-segment tone.

## Requirements

### ADDED: podcast-tts / Model-selected segment tone
The segment writer SHALL return a `tts_hints.tone` value from the fixed tone list for every story segment, and the TTS stage MUST use the style mapped to that tone in `PODCAST_TTS_TONE_MAP` in preference to the topic-category style.

#### Scenario: Serious tech story
- **WHEN** a story segment has `topic_category = "AI & Technology"` and `tts_hints.tone = "serious"`
- **THEN** the Gemini request's `speech_metadata.style` is `PODCAST_TTS_TONE_MAP["serious"]`
- **AND** the "AI & Technology" style from `PODCAST_TTS_STYLE_MAP` is not used

#### Scenario: Missing or invalid tone
- **WHEN** a story segment has no `tts_hints.tone`, or a tone not in the fixed list
- **THEN** the style comes from `PODCAST_TTS_STYLE_MAP[topic_category]` if present, otherwise no `speech_metadata` is sent

#### Scenario: Non-story segments
- **WHEN** the script is assembled
- **THEN** intro, transition and outro segments carry `tts_hints.tone = "neutral"`

## Files to Modify

| File | Change |
|---|---|
| `packages/aggregator-podcast/src/aggregator_podcast/generate.py` | `TTS_TONES` (or import it), segment prompt tone field and guidance, `tone: neutral` on intro/transition/outro, polish prompt do-not-touch list |
| `packages/aggregator-podcast/src/aggregator_podcast/tts.py` | Style resolver (tone → category → none), resolve once per segment, per-segment INFO log, WARNING on invalid tone |
| `packages/aggregator-podcast/src/aggregator_podcast/config.py` | `podcast_tts_tone_map` setting with defaults; startup WARNING for unknown keys |
| `packages/aggregator-podcast/tests/test_tts.py` | Resolver tests (see acceptance criteria); existing category-map tests keep passing |
| `packages/aggregator-podcast/tests/test_polish_phase.py` | `tts_hints` preserved through polish |
| `packages/aggregator-podcast/tests/` (generate tests) | Prompt contains every tone in `TTS_TONES`; assembled intro/transition/outro have `tone: neutral`; story `tts_hints.tone` passes through |
| `CLAUDE.md`, `.env.example` | Config docs as in Changes §6 |

## Acceptance Criteria

1. `_SEGMENT_SYSTEM` lists exactly the tones in `TTS_TONES` in the `tts_hints` format, and tells the model to choose the tone from the story's substance, not its category (asserted by a test on the prompt string).
2. A story segment with `tts_hints.tone = "serious"` and `topic_category = "AI & Technology"` produces a Gemini `Part` whose `speech_metadata.style` equals `PODCAST_TTS_TONE_MAP["serious"]`.
3. Tone values are normalised: `" Serious "` resolves the same as `"serious"`.
4. A segment with no tone, or with `tone = "cheerful"` (not in the list), uses the `PODCAST_TTS_STYLE_MAP` category style; the invalid case logs one WARNING containing the bad value.
5. A segment with no valid tone and an unmapped category sends no `speech_metadata` (existing behaviour kept).
6. With `PODCAST_TTS_TONE_MAP={}`, styling is the same as before this change (category only).
7. The style is resolved once per segment; a segment split into several chunks sends the same style on every chunk.
8. Intro, transition and outro segments in the assembled script have `tts_hints.tone == "neutral"`.
9. After `run_polish_phase`, every segment's `tts_hints` is identical to its input.
10. Unknown keys in `PODCAST_TTS_TONE_MAP` are ignored with a startup WARNING and do not crash the service.
11. `CLAUDE.md` and `.env.example` document `PODCAST_TTS_TONE_MAP` and the new fallback role of `PODCAST_TTS_STYLE_MAP`.
12. `uv run pytest packages/aggregator-podcast` passes.

## Out of Scope

- Changing the voice (`PODCAST_TTS_VOICE`) per segment. Only the style instruction changes.
- Letting the LLM write free-text style strings.
- Using the existing but unused `tts_hints.pace` hint in synthesis.
- Tone for transitions based on the neighbouring stories. Transitions stay `neutral`.
- Shadow evaluation or prompt variants: podcast prompts are excluded by ADR-9687cb01.

## Pending Decisions

- **Tone list.** Resolved: five tones (`grave`, `serious`, `neutral`, `engaged`, `upbeat`). More can be added later by extending `TTS_TONES` and the tone map.
- **Shadow evaluation.** Resolved: not required. The prompt is edited in place, and ADR-9687cb01 was amended with a podcast scope exclusion.
