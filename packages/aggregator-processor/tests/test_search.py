"""Integration tests for aggregator_processor.search — requires live Postgres."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from aggregator_processor.search import update_search_vector

from .conftest import make_article, make_source


class TestUpdateSearchVector:
    def test_title_term_makes_article_searchable(self, db_session):
        src = make_source(db_session, url="https://sv1.example.com/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="sv-title-1")

        update_search_vector(db_session, article.id, "Python programming language", "unrelated body")
        db_session.commit()

        rows = db_session.execute(
            text("SELECT id FROM articles WHERE search_vector @@ to_tsquery('english', 'python')")
        ).fetchall()
        found_ids = [r[0] for r in rows]
        assert article.id in found_ids

    def test_body_term_makes_article_searchable(self, db_session):
        src = make_source(db_session, url="https://sv2.example.com/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="sv-body-2")

        update_search_vector(db_session, article.id, "Generic Title", "xylophonics is a rare keyword body")
        db_session.commit()

        rows = db_session.execute(
            text("SELECT id FROM articles WHERE search_vector @@ to_tsquery('english', 'xylophon')")
        ).fetchall()
        found_ids = [r[0] for r in rows]
        assert article.id in found_ids

    def test_none_clean_text_does_not_raise(self, db_session):
        src = make_source(db_session, url="https://sv3.example.com/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="sv-none-3")

        update_search_vector(db_session, article.id, "Title Only Article", None)
        db_session.commit()

        rows = db_session.execute(
            text("SELECT id FROM articles WHERE search_vector IS NOT NULL AND id = :id"),
            {"id": article.id},
        ).fetchall()
        assert len(rows) == 1

    def test_empty_string_clean_text_does_not_raise(self, db_session):
        src = make_source(db_session, url="https://sv4.example.com/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="sv-empty-4")

        update_search_vector(db_session, article.id, "Title With Empty Body", "")
        db_session.commit()

        rows = db_session.execute(
            text("SELECT id FROM articles WHERE search_vector IS NOT NULL AND id = :id"),
            {"id": article.id},
        ).fetchall()
        assert len(rows) == 1

    def test_title_term_ranks_above_body_only_match(self, db_session):
        """Title terms (weight A) must rank higher than body-only terms (weight B)."""
        src = make_source(db_session, url="https://sv5.example.com/feed.xml")

        # Article 1: term in title (A weight)
        a_title = make_article(db_session, source_id=src.id, dedup_key="sv-rank-title")
        update_search_vector(db_session, a_title.id, "machinelearningstuff overview", "other content")
        db_session.commit()

        # Article 2: same term in body only (B weight)
        a_body = make_article(db_session, source_id=src.id, dedup_key="sv-rank-body")
        update_search_vector(db_session, a_body.id, "Unrelated Title Here", "machinelearningstuff in body only")
        db_session.commit()

        rows = db_session.execute(
            text(
                "SELECT id, ts_rank(search_vector, to_tsquery('english', 'machinelearningstuff')) AS rank "
                "FROM articles "
                "WHERE search_vector @@ to_tsquery('english', 'machinelearningstuff') "
                "ORDER BY rank DESC"
            )
        ).fetchall()

        ranked_ids = [r[0] for r in rows]
        assert a_title.id in ranked_ids, "Title-match article should be in results"
        assert a_body.id in ranked_ids, "Body-match article should be in results"
        assert ranked_ids.index(a_title.id) < ranked_ids.index(a_body.id), (
            "Title match (weight A) must rank above body-only match (weight B)"
        )
