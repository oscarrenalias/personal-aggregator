from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from aggregator_common import queries

from aggregator_api.dependencies import get_db
from aggregator_api.models import PaginatedResponse, ThreadMemberResponse, ThreadResponse

router = APIRouter(prefix="/threads", tags=["threads"])

_VALID_SORT_MODES = {"importance", "recent"}


@router.get("", response_model=PaginatedResponse[ThreadResponse])
def list_threads(
    sort: str = "importance",
    show_dismissed: bool = False,
    limit: int = 50,
    cursor: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if sort not in _VALID_SORT_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid sort {sort!r}. Must be one of: {sorted(_VALID_SORT_MODES)}")
    results, next_cursor = queries.list_threads(
        db,
        sort=sort,  # type: ignore[arg-type]
        include_dismissed=show_dismissed,
        limit=limit,
        cursor=cursor,
    )
    return PaginatedResponse(
        items=[ThreadResponse(**vars(r)) for r in results],
        next_cursor=next_cursor,
    )


@router.get("/{thread_id}", response_model=ThreadResponse)
def get_thread(thread_id: int, db: Session = Depends(get_db)):
    result = queries.get_thread(db, thread_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    return ThreadResponse(**vars(result))


@router.get("/{thread_id}/members", response_model=PaginatedResponse[ThreadMemberResponse])
def get_thread_members(thread_id: int, db: Session = Depends(get_db)):
    thread = queries.get_thread(db, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    members = queries.get_thread_members(db, thread_id)
    return PaginatedResponse(
        items=[ThreadMemberResponse(**vars(m)) for m in members],
        next_cursor=None,
    )
