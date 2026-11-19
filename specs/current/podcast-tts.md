# podcast-tts

## Requirement: Model-selected segment tone
<!-- source: spec-a46946a5 -->
The segment writer SHALL return a `tts_hints.tone` value from the fixed tone list for every story segment, and the TTS stage MUST use the style mapped to that tone in `PODCAST_TTS_TONE_MAP` in preference to the topic-category style.

### Scenario: Serious tech story
- **WHEN** a story segment has `topic_category = "AI & Technology"` and `tts_hints.tone = "serious"`
- **THEN** the Gemini request's `speech_metadata.style` is `PODCAST_TTS_TONE_MAP["serious"]`
- **AND** the "AI & Technology" style from `PODCAST_TTS_STYLE_MAP` is not used

### Scenario: Missing or invalid tone
- **WHEN** a story segment has no `tts_hints.tone`, or a tone not in the fixed list
- **THEN** the style comes from `PODCAST_TTS_STYLE_MAP[topic_category]` if present, otherwise no `speech_metadata` is sent

### Scenario: Non-story segments
- **WHEN** the script is assembled
- **THEN** intro, transition and outro segments carry `tts_hints.tone = "neutral"`
