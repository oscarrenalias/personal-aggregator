"""Web route tests for /threads endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from aggregator_common.models import Article, Source, Thread, ThreadMembership
from aggregator_common.state import ArticleStatus


def _make_thread(
    session,
    *,
    title: str = "Test Thread",
    surfaced: bool = True,
    top_grade: int | None = 75,
    deltas: list | None = None,
) -> Thread:
    now = datetime.now(tz=timezone.utc)
    thread = Thread(
        representative_title=title,
        first_seen=now,
        last_updated=now,
        status="active",
        surfaced=surfaced,
        top_grade=top_grade,
        source_list=[],
        known_facts=[],
        deltas=deltas or [],
    )
    session.add(thread)
    session.flush()
    session.commit()
    session.refresh(thread)
    return thread


def _make_source(session, name: str = "Test Source") -> Source:
    src = Source(name=name, feed_url=f"https://{name.lower().replace(' ', '-')}.example.com/feed.xml")
    session.add(src)
    session.flush()
    session.commit()
    session.refresh(src)
    return src


def _make_article(session, source_id: int, dedup_key: str, title: str = "Article Title") -> Article:
    now = datetime.now(tz=timezone.utc)
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=ArticleStatus.ready,
        clean_title=title,
        feed_published_at=now,
        raw_payload={"link": f"https://example.com/{dedup_key}"},
        retrieved_at=now,
    )
    session.add(article)
    session.flush()
    session.commit()
    session.refresh(article)
    return article


def _make_membership(session, thread_id: int, article_id: int, *, suppressed: bool = False) -> ThreadMembership:
    now = datetime.now(tz=timezone.utc)
    tm = ThreadMembership(
        thread_id=thread_id,
        article_id=article_id,
        suppressed=suppressed,
        assigned_at=now,
    )
    session.add(tm)
    session.flush()
    session.commit()
    session.refresh(tm)
    return tm


class TestThreadsIndex:
    def test_get_threads_returns_200(self, client, db_session):
        response = client.get("/threads")
        assert response.status_code == 200

    def test_get_threads_htmx_returns_partial(self, client, db_session):
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        # HTMX partial returns _thread_list.html content (not a full HTML page)
        assert "<!DOCTYPE html>" not in response.text

    def test_get_threads_full_page_has_doctype(self, client, db_session):
        response = client.get("/threads")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text

    def test_get_threads_shows_surfaced_thread_titles(self, client, db_session):
        _make_thread(db_session, title="Breaking AI News", surfaced=True)
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Breaking AI News" in response.text

    def test_get_threads_unsurfaced_threads_hidden(self, client, db_session):
        _make_thread(db_session, title="Hidden Thread", surfaced=False)
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Hidden Thread" not in response.text

    def test_get_threads_no_tier_section_headings(self, client, db_session):
        _make_thread(db_session, title="Some Thread")
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        # Flat list: no tier section labels in the HTML
        assert "must_know" not in response.text
        assert "worth_tracking" not in response.text
        assert "low_noise" not in response.text

    def test_get_threads_grade_descending_order(self, client, db_session):
        _make_thread(db_session, title="Low Grade Thread", top_grade=30)
        _make_thread(db_session, title="High Grade Thread", top_grade=90)
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        high_pos = response.text.index("High Grade Thread")
        low_pos = response.text.index("Low Grade Thread")
        assert high_pos < low_pos


class TestThreadDetail:
    def test_get_thread_detail_returns_200(self, client, db_session):
        thread = _make_thread(db_session, title="Detailed Thread")
        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200

    def test_get_thread_detail_contains_thread_title(self, client, db_session):
        thread = _make_thread(db_session, title="Climate Change Summit 2025")
        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Climate Change Summit 2025" in response.text

    def test_get_thread_detail_unknown_id_returns_404(self, client, db_session):
        response = client.get("/threads/999999", headers={"HX-Request": "true"})
        assert response.status_code == 404

    def test_get_thread_detail_full_page_returns_shell(self, client, db_session):
        thread = _make_thread(db_session, title="Full Page Thread")
        response = client.get(f"/threads/{thread.id}")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text


class TestThreadsRecluster:
    def test_post_recluster_returns_202(self, client, db_session):
        response = client.post("/threads/recluster")
        assert response.status_code == 202

    def test_post_recluster_has_hx_trigger_header(self, client, db_session):
        response = client.post("/threads/recluster")
        assert response.headers.get("HX-Trigger") == "reclustered"

    def test_post_recluster_enqueues_recluster(self, client, db_session):
        from aggregator_common.models import ClusterState
        response = client.post("/threads/recluster")
        assert response.status_code == 202
        # Verify the ClusterState row was created
        row = db_session.get(ClusterState, True)
        assert row is not None
        assert row.recluster_requested is True


class TestThreadDetailPresentation:
    """Regression tests for thread detail UI cleanup."""

    def test_no_confidence_badge_in_detail(self, client, db_session):
        thread = _make_thread(db_session, title="Badge Removal Test Thread")
        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "thread-confidence" not in response.text
        assert "% confidence" not in response.text
        assert "Explanation" not in response.text

    def test_no_sources_block_in_detail(self, client, db_session):
        thread = _make_thread(db_session, title="Sources Block Test Thread")
        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "thread-sources" not in response.text
        assert "source-diversity" not in response.text

    def test_source_name_inline_with_article_title(self, client, db_session):
        src = _make_source(db_session, name="BBC News Test")
        thread = _make_thread(db_session, title="Source Inline Test Thread")
        article = _make_article(db_session, src.id, "src-inline-1", title="Big Story Today")
        _make_membership(db_session, thread.id, article.id, suppressed=False)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "BBC News Test" in response.text
        assert "Big Story Today" in response.text

    def test_suppressed_member_shown_as_also_covered_by(self, client, db_session):
        src = _make_source(db_session, name="Hacker News Test")
        active_src = _make_source(db_session, name="TechCrunch Test")
        thread = _make_thread(db_session, title="Suppressed Coverage Test Thread")
        active_art = _make_article(db_session, active_src.id, "supp-active-1", title="Active Article")
        suppressed_art = _make_article(db_session, src.id, "supp-dup-1", title="Suppressed Dup")
        _make_membership(db_session, thread.id, active_art.id, suppressed=False)
        _make_membership(db_session, thread.id, suppressed_art.id, suppressed=True)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Also covered by" in response.text
        assert "Hacker News Test" in response.text
        # Suppressed article title should NOT appear as a link label
        assert "Suppressed Dup" not in response.text

    def test_suppressed_row_shows_source_name_only_no_per_row_prefix(self, client, db_session):
        """Regression: suppressed rows must not repeat 'Also covered by' per row (only the heading does)."""
        src = _make_source(db_session, name="Per Row Prefix Source")
        active_src = _make_source(db_session, name="Active Source")
        thread = _make_thread(db_session, title="Per Row Prefix Test Thread")
        active_art = _make_article(db_session, active_src.id, "prefix-active-1", title="Active Article")
        suppressed_art = _make_article(db_session, src.id, "prefix-supp-1", title="Suppressed Article")
        _make_membership(db_session, thread.id, active_art.id, suppressed=False)
        _make_membership(db_session, thread.id, suppressed_art.id, suppressed=True)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        html = response.text
        # Heading appears exactly once
        assert html.count("Also covered by") == 1
        # Row link text is just the source name, not "Also covered by <source>"
        assert "Also covered by Per Row Prefix Source" not in html
        assert "Per Row Prefix Source" in html

    def test_new_facts_rendered_as_list_items(self, client, db_session):
        delta = {
            "label": "same_thread_new_fact",
            "reason": "Two new developments were reported.",
            "new_facts": ["Fact one from the story.", "Fact two from the story."],
            "timestamp": "2026-06-14T10:00:00Z",
            "article_id": 1,
        }
        thread = _make_thread(db_session, title="New Facts Test Thread", deltas=[delta])

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Fact one from the story." in response.text
        assert "Fact two from the story." in response.text
        assert "delta-facts-list" in response.text
        # Raw label/article_id must not appear
        assert "same_thread_new_fact" not in response.text
        assert "article_id" not in response.text

    def test_what_changed_reason_shown_as_lead(self, client, db_session):
        delta = {
            "label": "same_thread_new_fact",
            "reason": "Lead reason sentence here.",
            "new_facts": ["A new fact."],
        }
        thread = _make_thread(db_session, title="Reason Lead Test Thread", deltas=[delta])

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Lead reason sentence here." in response.text

    def test_what_changed_omitted_when_no_new_facts(self, client, db_session):
        delta = {"label": "same_thread_new_fact", "reason": "No facts.", "new_facts": []}
        thread = _make_thread(db_session, title="Empty Facts Thread", deltas=[delta])

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "What changed" not in response.text


class TestThreadListPolish:
    """Regression tests for thread list/detail UI polish (B-22949bf1)."""

    def test_no_suppressed_today_text_in_thread_list(self, client, db_session):
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "suppressed today" not in response.text
        assert "suppressed-summary" not in response.text

    def test_no_recluster_button_in_thread_list(self, client, db_session):
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Re-cluster now" not in response.text
        assert "btn-recluster" not in response.text

    def test_no_recluster_button_in_thread_detail(self, client, db_session):
        thread = _make_thread(db_session, title="Detail Recluster Test")
        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Re-cluster now" not in response.text
        assert "btn-recluster" not in response.text

    def test_sort_toggle_rendered_in_thread_list(self, client, db_session):
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Importance" in response.text
        assert "Last updated" in response.text
        assert "feed-sort-toggle" in response.text

    def test_sort_toggle_importance_active_by_default(self, client, db_session):
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        # Default sort = importance; the Importance button should carry "active" class
        html = response.text
        # Find the Importance button and verify it has 'active' in its class
        import re
        btn = re.search(r'<button[^>]*sort-btn[^>]*>Importance</button>', html)
        assert btn is not None
        assert "active" in btn.group(0)

    def test_sort_toggle_recent_active_when_sort_recent(self, client, db_session):
        response = client.get("/threads?sort=recent", headers={"HX-Request": "true"})
        assert response.status_code == 200
        html = response.text
        import re
        btn = re.search(r'<button[^>]*sort-btn[^>]*>Last updated</button>', html)
        assert btn is not None
        assert "active" in btn.group(0)

    def test_recluster_route_still_exists(self, client, db_session):
        response = client.post("/threads/recluster")
        assert response.status_code == 202


class TestThreadDismissRestore:
    def test_dismiss_returns_200(self, client, db_session):
        thread = _make_thread(db_session, title="Dismiss Route Test")
        response = client.post(f"/threads/{thread.id}/dismiss")
        assert response.status_code == 200

    def test_dismiss_returns_htmx_trigger_header(self, client, db_session):
        thread = _make_thread(db_session, title="Dismiss Trigger Test")
        response = client.post(f"/threads/{thread.id}/dismiss")
        assert response.headers.get("HX-Trigger") == "refreshThreadList"

    def test_restore_returns_200(self, client, db_session):
        thread = _make_thread(db_session, title="Restore Route Test")
        response = client.post(f"/threads/{thread.id}/restore")
        assert response.status_code == 200

    def test_restore_returns_htmx_trigger_header(self, client, db_session):
        thread = _make_thread(db_session, title="Restore Trigger Test")
        response = client.post(f"/threads/{thread.id}/restore")
        assert response.headers.get("HX-Trigger") == "refreshThreadList"

    def test_dismiss_unknown_id_returns_404(self, client, db_session):
        response = client.post("/threads/999999/dismiss")
        assert response.status_code == 404

    def test_restore_unknown_id_returns_404(self, client, db_session):
        response = client.post("/threads/999999/restore")
        assert response.status_code == 404

    def test_dismiss_sets_thread_dismissed(self, client, db_session):
        from aggregator_common.models import Thread as ThreadModel
        thread = _make_thread(db_session, title="Dismiss DB Test")
        client.post(f"/threads/{thread.id}/dismiss")
        db_session.expire(thread)
        db_session.refresh(thread)
        assert thread.dismissed is True  # type: ignore[attr-defined]

    def test_restore_clears_thread_dismissed(self, client, db_session):
        from aggregator_common.models import Thread as ThreadModel
        thread = _make_thread(db_session, title="Restore DB Test")
        client.post(f"/threads/{thread.id}/dismiss")
        client.post(f"/threads/{thread.id}/restore")
        db_session.expire(thread)
        db_session.refresh(thread)
        assert thread.dismissed is False  # type: ignore[attr-defined]


class TestThreadsShowDismissed:
    def test_dismissed_thread_hidden_by_default(self, client, db_session):
        thread = _make_thread(db_session, title="Hidden Dismissed Thread")
        client.post(f"/threads/{thread.id}/dismiss")
        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Hidden Dismissed Thread" not in response.text

    def test_show_dismissed_includes_dismissed_threads(self, client, db_session):
        thread = _make_thread(db_session, title="Shown Dismissed Thread")
        client.post(f"/threads/{thread.id}/dismiss")
        response = client.get("/threads?show_dismissed=1", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "Shown Dismissed Thread" in response.text


class TestListThreadsSort:
    """Unit tests for list_threads sort parameter in queries.py."""

    def test_list_threads_default_sort_is_importance(self, db_session):
        from aggregator_common.queries import list_threads
        _make_thread(db_session, title="Low Grade", top_grade=20)
        _make_thread(db_session, title="High Grade", top_grade=95)
        results = list_threads(db_session)
        titles = [r.representative_title for r in results]
        assert titles.index("High Grade") < titles.index("Low Grade")

    def test_list_threads_sort_recent_orders_by_last_updated(self, db_session):
        from datetime import timedelta
        from aggregator_common.queries import list_threads
        now = datetime.now(tz=timezone.utc)
        older = Thread(
            representative_title="Older Thread",
            first_seen=now - timedelta(hours=10),
            last_updated=now - timedelta(hours=10),
            status="active",
            surfaced=True,
            top_grade=90,
            source_list=[],
            known_facts=[],
            deltas=[],
        )
        newer = Thread(
            representative_title="Newer Thread",
            first_seen=now - timedelta(hours=1),
            last_updated=now - timedelta(hours=1),
            status="active",
            surfaced=True,
            top_grade=20,
            source_list=[],
            known_facts=[],
            deltas=[],
        )
        db_session.add_all([older, newer])
        db_session.commit()
        results = list_threads(db_session, sort="recent")
        titles = [r.representative_title for r in results]
        assert titles.index("Newer Thread") < titles.index("Older Thread")

    def test_list_threads_sort_importance_ignores_last_updated(self, db_session):
        from datetime import timedelta
        from aggregator_common.queries import list_threads
        now = datetime.now(tz=timezone.utc)
        low_grade_recent = Thread(
            representative_title="Low Grade Recent",
            first_seen=now - timedelta(hours=1),
            last_updated=now - timedelta(hours=1),
            status="active",
            surfaced=True,
            top_grade=10,
            source_list=[],
            known_facts=[],
            deltas=[],
        )
        high_grade_older = Thread(
            representative_title="High Grade Older",
            first_seen=now - timedelta(hours=8),
            last_updated=now - timedelta(hours=8),
            status="active",
            surfaced=True,
            top_grade=95,
            source_list=[],
            known_facts=[],
            deltas=[],
        )
        db_session.add_all([low_grade_recent, high_grade_older])
        db_session.commit()
        results = list_threads(db_session, sort="importance")
        titles = [r.representative_title for r in results]
        assert titles.index("High Grade Older") < titles.index("Low Grade Recent")


class TestThreadDetailPublishedAt:
    """Regression tests: article publication date shown in thread detail member rows."""

    def test_member_row_shows_published_label(self, client, db_session):
        src = _make_source(db_session, name="Published At Source")
        thread = _make_thread(db_session, title="Published Date Thread")
        article = _make_article(db_session, src.id, "pub-at-art1", title="Published At Article")
        _make_membership(db_session, thread.id, article.id)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})

        assert response.status_code == 200
        assert "Published" in response.text
        assert "Added" in response.text

    def test_member_row_published_and_added_are_distinct(self, client, db_session):
        src = _make_source(db_session, name="Distinct Dates Source")
        thread = _make_thread(db_session, title="Distinct Dates Thread")
        article = _make_article(db_session, src.id, "distinct-dates-1", title="Distinct Dates Article")
        _make_membership(db_session, thread.id, article.id)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})

        assert response.status_code == 200
        html = response.text
        assert "member-published-at" in html
        assert "member-added-at" in html

    def test_member_row_separator_between_dates(self, client, db_session):
        src = _make_source(db_session, name="Separator Source")
        thread = _make_thread(db_session, title="Separator Thread")
        article = _make_article(db_session, src.id, "sep-art-1", title="Separator Article")
        _make_membership(db_session, thread.id, article.id)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})

        assert response.status_code == 200
        # The · separator must appear between published and added
        assert " · " in response.text
