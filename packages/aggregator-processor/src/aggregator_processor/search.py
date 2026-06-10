from sqlalchemy import text
from sqlalchemy.orm import Session


def update_search_vector(
    session: Session,
    article_id: int,
    clean_title: str,
    clean_text: str | None,
) -> None:
    session.execute(
        text(
            "UPDATE articles SET search_vector = "
            "setweight(to_tsvector('english', :title), 'A') || "
            "setweight(to_tsvector('english', coalesce(:body, '')), 'B') "
            "WHERE id = :article_id"
        ),
        {"title": clean_title, "body": clean_text, "article_id": article_id},
    )
