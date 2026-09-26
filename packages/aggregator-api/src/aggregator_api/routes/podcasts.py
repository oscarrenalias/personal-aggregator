import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from aggregator_common import queries

from aggregator_api.dependencies import get_db
from aggregator_api.models import PaginatedResponse

router = APIRouter(prefix="/podcasts", tags=["podcasts"])


class PodcastEpisodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: str
    episode_theme: Optional[str]
    status: str
    duration_seconds: Optional[int]
    audio_url: str
    segment_count: int
    llm_model: Optional[str]
    tts_model: Optional[str]
    tts_voice: Optional[str]
    generated_at: Optional[str]
    created_at: str
    artwork_url: Optional[str] = None


def _to_response(episode) -> PodcastEpisodeResponse:
    script_json = episode.script_json or {}
    segments = script_json.get("segments", []) if isinstance(script_json, dict) else []
    return PodcastEpisodeResponse(
        id=episode.id,
        date=str(episode.date),
        episode_theme=episode.episode_theme,
        status=episode.status,
        duration_seconds=episode.duration_seconds,
        audio_url=f"/api/v1/podcasts/{episode.id}/audio",
        segment_count=len(segments),
        llm_model=episode.llm_model,
        tts_model=episode.tts_model,
        tts_voice=episode.tts_voice,
        generated_at=episode.generated_at.isoformat() if episode.generated_at else None,
        created_at=episode.created_at.isoformat(),
        artwork_url=episode.artwork_url,
    )


@router.get("", response_model=PaginatedResponse[PodcastEpisodeResponse])
def list_episodes(
    limit: int = 50,
    cursor: Optional[str] = None,
    db: Session = Depends(get_db),
):
    episodes, next_cursor = queries.list_podcast_episodes(db, limit=limit, cursor=cursor)
    return PaginatedResponse(
        items=[_to_response(e) for e in episodes],
        next_cursor=next_cursor,
    )


# Declare literal-segment routes before the /{episode_id} int route so FastAPI
# matches them first and does not try to coerce "latest" or "by-date" to int.

@router.get("/latest", response_model=PodcastEpisodeResponse)
def get_latest_episode(db: Session = Depends(get_db)):
    episode = queries.get_latest_podcast_episode(db)
    if episode is None:
        raise HTTPException(status_code=404, detail="No ready podcast episode found")
    return _to_response(episode)


@router.get("/by-date/{date}", response_model=PodcastEpisodeResponse)
def get_episode_by_date(date: str, db: Session = Depends(get_db)):
    try:
        from datetime import date as DateType
        episode_date = DateType.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")
    episode = queries.get_podcast_episode_by_date(db, episode_date)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"No podcast episode found for {date}")
    return _to_response(episode)


@router.get("/{episode_id}", response_model=PodcastEpisodeResponse)
def get_episode(episode_id: int, db: Session = Depends(get_db)):
    episode = queries.get_podcast_episode(db, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Podcast episode {episode_id} not found")
    return _to_response(episode)


def _audio_response(episode_id: int, db: Session):
    episode = queries.get_podcast_episode(db, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Podcast episode {episode_id} not found")
    if not episode.audio_path or not os.path.exists(episode.audio_path):
        raise HTTPException(status_code=404, detail=f"Audio file not found for episode {episode_id}")
    return FileResponse(episode.audio_path, media_type="audio/mpeg")


@router.get("/{episode_id}/audio")
def get_episode_audio(episode_id: int, db: Session = Depends(get_db)):
    return _audio_response(episode_id, db)


@router.head("/{episode_id}/audio", include_in_schema=False)
def head_episode_audio(episode_id: int, db: Session = Depends(get_db)):
    return _audio_response(episode_id, db)


def _script_response(episode_id: int, db: Session):
    episode = queries.get_podcast_episode(db, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail=f"Podcast episode {episode_id} not found")
    if episode.script_json is None:
        raise HTTPException(status_code=404, detail=f"Script not found for episode {episode_id}")
    return JSONResponse(episode.script_json)


@router.get("/{episode_id}/script")
def get_episode_script(episode_id: int, db: Session = Depends(get_db)):
    return _script_response(episode_id, db)


@router.head("/{episode_id}/script", include_in_schema=False)
def head_episode_script(episode_id: int, db: Session = Depends(get_db)):
    return _script_response(episode_id, db)
