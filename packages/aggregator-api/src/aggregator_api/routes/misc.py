from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_common import queries
from aggregator_common.db import get_session
from aggregator_common.models import InterestProfile
from aggregator_common.version import version

from aggregator_api.dependencies import get_db
from aggregator_api.models import (
    BriefResponse,
    BriefTopicResponse,
    CategoryResponse,
    InterestProfileResponse,
    PaginatedResponse,
    SourceResponse,
)
from aggregator_api.settings import ApiSettings

router = APIRouter()
_settings = ApiSettings()


def _brief_result_to_response(result) -> BriefResponse:
    return BriefResponse(
        id=result.id,
        headline=result.headline,
        intro=result.intro,
        generated_at=result.generated_at,
        period_start=result.period_start,
        period_end=result.period_end,
        model=result.model,
        topics=[BriefTopicResponse.model_validate(t) for t in result.topics],
    )


@router.get("/brief/today", response_model=BriefResponse)
def get_brief_today(db: Session = Depends(get_db)):
    result = queries.get_latest_brief(db)
    if result is None:
        raise HTTPException(status_code=404, detail="No ready brief available")
    return _brief_result_to_response(result)


@router.get("/briefs", response_model=PaginatedResponse[BriefResponse])
def list_briefs(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    results, next_cursor = queries.list_briefs(db, limit=limit, cursor=cursor)
    return PaginatedResponse(
        items=[_brief_result_to_response(r) for r in results],
        next_cursor=next_cursor,
    )


@router.get("/briefs/{brief_id}", response_model=BriefResponse)
def get_brief(brief_id: int, db: Session = Depends(get_db)):
    result = queries.get_brief(db, brief_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Brief not found")
    return _brief_result_to_response(result)


@router.get("/sources", response_model=List[SourceResponse])
def get_sources(db: Session = Depends(get_db)):
    return [SourceResponse.model_validate(s) for s in queries.list_sources(db, important_threshold=_settings.web_important_threshold)]


@router.get("/categories", response_model=List[CategoryResponse])
def get_categories(db: Session = Depends(get_db)):
    return [CategoryResponse.model_validate(c) for c in queries.list_categories(db, important_threshold=_settings.web_important_threshold)]


@router.get("/interest-profile", response_model=InterestProfileResponse)
def get_interest_profile(db: Session = Depends(get_db)):
    profile = db.get(InterestProfile, True)
    if profile is None:
        return InterestProfileResponse(profile_text="")
    return InterestProfileResponse(profile_text=profile.profile_text, updated_at=profile.updated_at)


@router.get("/healthz")
def healthz():
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    status_code = 200 if db_status == "ok" else 500
    return JSONResponse(
        status_code=status_code,
        content={"version": version(), "db": db_status},
    )
