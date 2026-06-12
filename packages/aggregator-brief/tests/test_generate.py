"""Tests for aggregator_brief.generate — tool loop, reconciliation, and error paths."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from aggregator_brief.config import BriefSettings
from aggregator_brief.generate import GenerationError, _reconcile_references, generate_brief
from aggregator_common.models import BriefTopic

from conftest import make_article, make_brief, make_source

def _settings(**overrides) -> BriefSettings:
    defaults: dict = {"brief_tool_max_calls": 5}
    defaults.update(overrides)
    return BriefSettings(**defaults)


def _tool_call(name: str, args: dict, tc_id: str = "tc1") -> MagicMock:
    tc = MagicMock()
    tc.id = tc_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


def _response(tool_calls=None, content: str = "", model: str = "gpt-4.1-mini") -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    resp = MagicMock()
    resp.model = model
    resp.choices = [MagicMock(message=msg)]
    return resp


_VALID_SUBMIT = {
    "headline": "Today's Brief Headline",
    "intro": "A short intro paragraph.",
    "topics": [
        {
            "headline": "Topic One",
            "what_happened": "Something notable happened.",
            "why_it_matters": "This affects many people.",
            "references": [],
        }
    ],
}


class TestGenerateBriefHappyPath:
    def test_persists_headline_intro_and_topics(self, db_session):
        brief = make_brief(db_session, status="generating")
        responses = [
            _response(tool_calls=[_tool_call("search_articles", {"query": "news"}, "tc1")]),
            _response(tool_calls=[_tool_call("submit_brief", _VALID_SUBMIT, "tc2")]),
        ]

        with patch("aggregator_brief.generate._call_llm", side_effect=responses):
            generate_brief(db_session, brief, _settings())

        assert brief.headline == _VALID_SUBMIT["headline"]
        assert brief.intro == _VALID_SUBMIT["intro"]
        topics = db_session.query(BriefTopic).filter_by(brief_id=brief.id).all()
        assert len(topics) == 1
        assert topics[0].headline == "Topic One"
        assert topics[0].position == 0

    def test_topic_position_equals_list_index(self, db_session):
        multi_topic_submit = {
            **_VALID_SUBMIT,
            "topics": [
                {
                    "headline": f"Topic {i}",
                    "what_happened": "Happened.",
                    "why_it_matters": "Matters.",
                    "references": [],
                }
                for i in range(3)
            ],
        }
        brief = make_brief(db_session, status="generating")
        with patch(
            "aggregator_brief.generate._call_llm",
            return_value=_response(tool_calls=[_tool_call("submit_brief", multi_topic_submit)]),
        ):
            generate_brief(db_session, brief, _settings())

        topics = (
            db_session.query(BriefTopic)
            .filter_by(brief_id=brief.id)
            .order_by(BriefTopic.position)
            .all()
        )
        assert len(topics) == 3
        for i, t in enumerate(topics):
            assert t.position == i

    def test_model_name_stored_on_brief(self, db_session):
        brief = make_brief(db_session, status="generating")
        with patch(
            "aggregator_brief.generate._call_llm",
            return_value=_response(
                tool_calls=[_tool_call("submit_brief", _VALID_SUBMIT)],
                model="gpt-4o-mini",
            ),
        ):
            generate_brief(db_session, brief, _settings())

        assert brief.model == "gpt-4o-mini"


class TestToolMaxCallsExceeded:
    def test_raises_generation_error_without_submit(self, db_session):
        brief = make_brief(db_session, status="generating")
        # Always returns search_articles — never submit_brief
        repeated = _response(tool_calls=[_tool_call("search_articles", {"query": "x"})])

        with patch("aggregator_brief.generate._call_llm", return_value=repeated):
            with pytest.raises(GenerationError, match="without submit_brief"):
                generate_brief(db_session, brief, _settings(brief_tool_max_calls=3))

    def test_no_tool_calls_raises(self, db_session):
        brief = make_brief(db_session, status="generating")

        with patch("aggregator_brief.generate._call_llm", return_value=_response(tool_calls=None)):
            with pytest.raises(GenerationError):
                generate_brief(db_session, brief, _settings())


class TestCorrectiveTurn:
    def test_valid_corrective_response_saves_brief(self, db_session):
        brief = make_brief(db_session, status="generating")
        invalid_payload = {"intro": "missing headline", "topics": []}

        responses = [
            _response(tool_calls=[_tool_call("submit_brief", invalid_payload, "tc1")]),
            _response(tool_calls=[_tool_call("submit_brief", _VALID_SUBMIT, "tc2")]),
        ]

        with patch("aggregator_brief.generate._call_llm", side_effect=responses):
            generate_brief(db_session, brief, _settings())

        assert brief.headline == _VALID_SUBMIT["headline"]

    def test_invalid_corrective_response_raises_no_topics(self, db_session):
        brief = make_brief(db_session, status="generating")
        invalid = {"intro": "no headline here", "topics": []}

        responses = [
            _response(tool_calls=[_tool_call("submit_brief", invalid, "tc1")]),
            _response(tool_calls=[_tool_call("submit_brief", invalid, "tc2")]),
        ]

        with patch("aggregator_brief.generate._call_llm", side_effect=responses):
            with pytest.raises(GenerationError, match="still invalid"):
                generate_brief(db_session, brief, _settings())

        topics = db_session.query(BriefTopic).filter_by(brief_id=brief.id).all()
        assert len(topics) == 0

    def test_no_submit_in_corrective_turn_raises(self, db_session):
        brief = make_brief(db_session, status="generating")
        invalid = {"intro": "no headline", "topics": []}

        responses = [
            _response(tool_calls=[_tool_call("submit_brief", invalid, "tc1")]),
            _response(tool_calls=None),  # No submit_brief in corrective turn
        ]

        with patch("aggregator_brief.generate._call_llm", side_effect=responses):
            with pytest.raises(GenerationError, match="did not call submit_brief"):
                generate_brief(db_session, brief, _settings())


class TestForcedSubmitOnFinalIteration:
    """Regression: the model must be forced to submit on the last iteration so a
    brief is always produced instead of failing when it keeps exploring."""

    def test_call_llm_forces_submit_tool_choice(self):
        with patch("aggregator_brief.generate.litellm.completion") as completion:
            from aggregator_brief.generate import _call_llm

            _call_llm([{"role": "user", "content": "hi"}], _settings(), force_submit=True)
            forced_kwargs = completion.call_args.kwargs
            assert forced_kwargs["tool_choice"] == {
                "type": "function",
                "function": {"name": "submit_brief"},
            }

            _call_llm([{"role": "user", "content": "hi"}], _settings(), force_submit=False)
            assert completion.call_args.kwargs["tool_choice"] == "auto"

    def test_final_iteration_forces_submit_and_persists(self, db_session):
        brief = make_brief(db_session, status="generating")
        max_calls = 3
        search = _response(tool_calls=[_tool_call("search_articles", {"query": "x"})])
        submit = _response(tool_calls=[_tool_call("submit_brief", _VALID_SUBMIT, "tcF")])

        seen_force = []

        def fake_call(messages, settings, *, force_submit=False):
            seen_force.append(force_submit)
            return submit if force_submit else search

        with patch("aggregator_brief.generate._call_llm", side_effect=fake_call):
            generate_brief(db_session, brief, _settings(brief_tool_max_calls=max_calls))

        # Forced exactly on the final iteration, and the brief was produced.
        assert seen_force == [False, False, True]
        assert brief.headline == _VALID_SUBMIT["headline"]


class TestReconcileReferences:
    def test_valid_article_id_becomes_internal(self, db_session):
        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, dedup_key="ref-1")

        raw = [{"article_id": article.id, "title": "The Article", "url": "https://ex.com/1"}]
        result = _reconcile_references(db_session, raw)

        assert len(result) == 1
        assert result[0]["internal"] is True
        assert result[0]["article_id"] == article.id

    def test_invalid_id_with_url_becomes_external(self, db_session):
        raw = [{"article_id": 99999, "title": "External", "url": "https://external.com/x"}]
        result = _reconcile_references(db_session, raw)

        assert len(result) == 1
        assert result[0]["internal"] is False
        assert result[0]["article_id"] is None
        assert result[0]["url"] == "https://external.com/x"

    def test_invalid_id_without_url_dropped(self, db_session):
        raw = [{"article_id": 99999, "title": "Dangling", "url": None}]
        result = _reconcile_references(db_session, raw)

        assert result == []

    def test_no_article_id_with_url_kept_as_external(self, db_session):
        raw = [{"article_id": None, "title": "External Link", "url": "https://news.example.com/1"}]
        result = _reconcile_references(db_session, raw)

        assert len(result) == 1
        assert result[0]["internal"] is False

    def test_no_article_id_no_url_dropped(self, db_session):
        raw = [{"article_id": None, "title": "No URL", "url": None}]
        result = _reconcile_references(db_session, raw)

        assert result == []

    def test_empty_input_returns_empty(self, db_session):
        assert _reconcile_references(db_session, []) == []

    def test_mixed_refs_all_reconciled(self, db_session):
        src = make_source(db_session)
        valid = make_article(db_session, source_id=src.id, dedup_key="mixed-1")

        raw = [
            {"article_id": valid.id, "title": "Valid", "url": None},
            {"article_id": 88888, "title": "Bogus+URL", "url": "https://x.com/x"},
            {"article_id": 77777, "title": "Bogus+NoURL", "url": None},
            {"article_id": None, "title": "Pure External", "url": "https://y.com/y"},
        ]
        result = _reconcile_references(db_session, raw)

        assert len(result) == 3  # Bogus+NoURL dropped
        assert result[0]["internal"] is True
        assert result[1]["internal"] is False
        assert result[2]["internal"] is False
