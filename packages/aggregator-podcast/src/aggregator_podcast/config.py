from pydantic import Field
from pydantic_settings import SettingsConfigDict

from aggregator_common.config import Settings


class PodcastSettings(Settings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    podcast_generation_hour: int = Field(7, description="Hour of day (in podcast_timezone) to generate the podcast")
    podcast_timezone: str = Field("UTC", description="Timezone for scheduling podcast generation")
    podcast_poll_interval_seconds: int = Field(60, description="Seconds between scheduler poll cycles")
    podcast_claim_lease_seconds: int = Field(900, description="Work-claim lease duration for podcast jobs in seconds")
    podcast_audio_dir: str = Field("/data/podcasts", description="Directory to write generated audio files")
    podcast_candidate_window_hours: int = Field(36, description="Hours of article history to include as candidates")
    podcast_llm_model: str = Field("gpt-5.6-terra", description="LLM model for podcast script generation")
    podcast_llm_max_tokens: int = Field(4096, description="Maximum output tokens for LLM script generation calls")
    podcast_tts_model: str = Field("gpt-4o-mini-tts", description="TTS model for audio synthesis")
    podcast_tts_voice: str = Field("marin", description="Voice name for TTS synthesis")
    podcast_tts_max_chars_per_chunk: int = Field(1000, description="Maximum characters per TTS API chunk")
    podcast_continuity_count: int = Field(2, description="Number of previous podcasts included for continuity context")
