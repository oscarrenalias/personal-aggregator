from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from aggregator_common import queries

from aggregator_api.dependencies import get_db
from aggregator_api.models import ArticleResponse, PaginatedResponse

router = APIRouter(prefix="/articles", tags=["articles"])

_VALID_VIEWS = {"all", "unread", "important", "saved", "today", "uncategorized"}


@router.get("", response_model=PaginatedResponse[ArticleResponse])
def list_articles(
    view: str = "all",
    category: Optional[str] = None,
    source_id: Optional[int] = None,
    unread_only: bool = False,
    limit: int = 50,
    cursor: Optional[str] = None,
    db: Session = Depends(get_db),
):
    if view not in _VALID_VIEWS:
        raise HTTPException(status_code=400, detail=f"Invalid view {view!r}. Must be one of: {sorted(_VALID_VIEWS)}")
    try:
        results, next_cursor = queries.list_articles(
            db,
            view=view,
            category=category,
            source_id=source_id,
            unread_only=unread_only,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return PaginatedResponse(
        items=[ArticleResponse(**vars(r)) for r in results],
        next_cursor=next_cursor,
    )


@router.get("/search", response_model=PaginatedResponse[ArticleResponse])
def search_articles(
    q: str,
    category: Optional[str] = None,
    source_id: Optional[int] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
    db: Session = Depends(get_db),
):
    results, next_cursor = queries.search_articles(
        db,
        query=q,
        category=category,
        source_id=source_id,
        limit=limit,
        cursor=cursor,
    )
    return PaginatedResponse(
        items=[ArticleResponse(**vars(r)) for r in results],
        next_cursor=next_cursor,
    )


@router.get("/{article_id}", response_model=ArticleResponse)
def get_article(article_id: int, db: Session = Depends(get_db)):
    try:
        result = queries.get_article(db, article_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    return ArticleResponse(**vars(result))
