"""Regression tests for podcast mobile reader-pane wiring (B-5a0d17fa).

Two bugs were fixed:
1. Podcast card click never added the `reader-open` class, so the reader pane
   stayed off-screen on mobile after tapping an episode card.
2. GET /podcasts/{id} without an HX-Request header returned a bare unstyled
   fragment instead of the full shell — deep-links and refreshes were broken.
"""
from __future__ import annotations

import pytest

from conftest import make_podcast_episode


class TestPodcastCardMarkup:
    """Podcast card must wire a reader-open handler, matching the thread card pattern."""

    def test_podcast_card_calls_select_episode_on_click(self, client, db_session):
        """The podcast card @click must call selectEpisode(), not bare selectedId assignment.

        Before the fix: @click="selectedId = N" never added `reader-open` so the reader
        pane stayed hidden on mobile.
        After the fix: @click="selectEpisode(N)" adds `reader-open` via the podcastList
        Alpine component.
        """
        make_podcast_episode(db_session, episode_theme="My Test Episode")
        response = client.get("/podcasts", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "selectEpisode(" in response.text, (
            "Podcast card @click must call selectEpisode() to add reader-open on mobile. "
            "Found bare selectedId assignment instead."
        )

    def test_podcast_card_does_not_have_click_prevent_on_link(self, client, db_session):
        """The episode title <a> must not have @click.prevent blocking HTMX.

        Before the fix: @click.prevent blocked the native navigation but did NOT add
        reader-open, so clicking the title left the reader pane hidden on mobile.
        After the fix: the <a> relies on HTMX (which already prevents default) and the
        parent <article> @click calls selectEpisode().
        """
        make_podcast_episode(db_session)
        response = client.get("/podcasts", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "@click.prevent" not in response.text, (
            "Podcast card <a> must not use @click.prevent; HTMX handles preventDefault "
            "and selectEpisode() on the parent <article> adds reader-open."
        )

    def test_podcast_list_uses_podcast_list_alpine_component(self, client, db_session):
        """The podcast list container must use the podcastList Alpine component.

        podcastList exposes selectEpisode() which adds reader-open.
        """
        make_podcast_episode(db_session)
        response = client.get("/podcasts", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "podcastList" in response.text, (
            "Podcast list x-data must spread podcastList() so selectEpisode() is available."
        )

    def test_podcast_card_has_data_episode_id(self, client, db_session):
        """Each episode card must carry data-episode-id for future keyboard navigation."""
        ep = make_podcast_episode(db_session)
        response = client.get("/podcasts", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert f'data-episode-id="{ep.id}"' in response.text


class TestPodcastDetailRoute:
    """GET /podcasts/{id} must return the full shell on direct browser navigation."""

    def test_podcast_detail_without_hx_returns_full_shell(self, client, db_session):
        """Direct navigation / deep-link / refresh must return a complete HTML page.

        Before the fix: returned the bare podcasts.html fragment, producing an unstyled
        partial page in the browser.
        After the fix: returns shell.html with the episode pre-loaded in #reader-content
        and reader-open set on <body>, matching /threads/{id} and /article/{id}.
        """
        ep = make_podcast_episode(db_session, episode_theme="Deep Link Episode")
        response = client.get(f"/podcasts/{ep.id}")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text, (
            "GET /podcasts/{id} without HX-Request must return a full HTML document, "
            "not a bare fragment."
        )

    def test_podcast_detail_without_hx_has_reader_open_class(self, client, db_session):
        """The shell body must carry class='reader-open' when serving a deep-linked episode.

        Before the fix: podcasts.html fragment has no shell, so reader-open was never set.
        After the fix: shell.html applies reader-open server-side from initial_reader_open=True.
        """
        ep = make_podcast_episode(db_session, episode_theme="Reader Open Episode")
        response = client.get(f"/podcasts/{ep.id}")
        assert response.status_code == 200
        assert 'class="reader-open"' in response.text, (
            "Shell body must have class='reader-open' on direct podcast episode load."
        )

    def test_podcast_detail_without_hx_contains_episode_content(self, client, db_session):
        """The full-page response must contain the episode detail in the reader pane."""
        ep = make_podcast_episode(db_session, episode_theme="Episode In Shell")
        response = client.get(f"/podcasts/{ep.id}")
        assert response.status_code == 200
        assert "Episode In Shell" in response.text

    def test_podcast_detail_with_hx_request_returns_fragment(self, client, db_session):
        """HTMX partial request must still return only _podcast_detail.html, not the shell."""
        ep = make_podcast_episode(db_session)
        response = client.get(f"/podcasts/{ep.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "<!DOCTYPE html>" not in response.text, (
            "HX-Request to /podcasts/{id} must return a partial, not the full shell."
        )

    def test_podcast_detail_without_hx_unknown_id_returns_404(self, client, db_session):
        """Non-existent episode must return 404 on both HX and non-HX paths."""
        response = client.get("/podcasts/9999999")
        assert response.status_code == 404


class TestPodcastListRoute:
    """GET /podcasts must return the full shell on direct browser navigation."""

    def test_podcast_list_without_hx_returns_full_page(self, client, db_session):
        """Direct navigation to /podcasts must return a complete HTML document."""
        response = client.get("/podcasts")
        assert response.status_code == 200
        assert "<!DOCTYPE html>" in response.text

    def test_podcast_list_with_hx_request_returns_fragment(self, client, db_session):
        """HTMX sidebar navigation must receive only the fragment (no full page)."""
        response = client.get("/podcasts", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "<!DOCTYPE html>" not in response.text
        assert 'id="article-list"' in response.text


class TestPodcastAppJs:
    """app.js must define and register the podcastList Alpine component."""

    def test_app_js_defines_podcast_list_function(self, client):
        """podcastList() function must be defined in app.js."""
        response = client.get("/static/app.js")
        assert response.status_code == 200
        assert "function podcastList" in response.text

    def test_app_js_defines_select_episode_method(self, client):
        """selectEpisode() must be defined inside podcastList."""
        response = client.get("/static/app.js")
        assert response.status_code == 200
        assert "selectEpisode" in response.text

    def test_app_js_registers_podcast_list_with_alpine(self, client):
        """podcastList must be registered with Alpine.data so x-data='podcastList' resolves."""
        response = client.get("/static/app.js")
        assert response.status_code == 200
        assert "Alpine.data('podcastList'" in response.text or \
               'Alpine.data("podcastList"' in response.text

    def test_select_episode_adds_reader_open(self, client):
        """selectEpisode() must add the reader-open class to document.body."""
        response = client.get("/static/app.js")
        assert response.status_code == 200
        js = response.text
        select_episode_idx = js.find("selectEpisode")
        reader_open_idx = js.find("reader-open", select_episode_idx)
        assert reader_open_idx != -1, (
            "selectEpisode() must call document.body.classList.add('reader-open') "
            "so the reader pane slides in on mobile."
        )
