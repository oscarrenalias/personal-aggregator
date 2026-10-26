"""Tests for aggregator_brief.generate — tool loop, reconciliation, and error paths."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from aggregator_brief.config import BriefSettings
from aggregator_brief.generate import (
    BriefLoopError,
    GenerationError,
    _reconcile_references,
    generate_brief,
    run_brief_loop,
)
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

        def fake_call(messages, settings, *, force_submit=False, ref_id=None):
            seen_force.append(force_submit)
            return submit if force_submit else search

        with patch("aggregator_brief.generate._call_llm", side_effect=fake_call):
            generate_brief(db_session, brief, _settings(brief_tool_max_calls=max_calls))

        # Forced exactly on the final iteration, and the brief was produced.
        assert seen_force == [False, False, True]
        assert brief.headline == _VALID_SUBMIT["headline"]


class TestTextStopRecovery:
    """Regression: model writes plain text instead of calling submit_brief mid-loop.

    Without the fix the loop breaks immediately on no-tool-call turns and raises
    'Tool loop exhausted … without submit_brief' even after only a handful of turns.
    With the fix one forced recovery call is made; if it succeeds the brief is saved.
    """

    def test_recovery_succeeds_on_forced_call(self, db_session):
        """[search, plain-text stop, submit_brief on forced recovery] -> brief saved."""
        settings = _settings(brief_tool_max_calls=5)
        messages = [{"role": "user", "content": "generate a brief"}]

        call_sequence = [
            _response(tool_calls=[_tool_call("search_articles", {"query": "x"}, "tc1")]),
            _response(tool_calls=None, content="Here is the brief: ..."),  # text stop
            _response(tool_calls=[_tool_call("submit_brief", _VALID_SUBMIT, "tc3")]),
        ]
        force_flags: list[bool] = []

        def fake_call_fn(msgs, force):
            force_flags.append(force)
            return call_sequence.pop(0)

        result = run_brief_loop(db_session, messages, fake_call_fn, settings)

        assert result.payload.headline == _VALID_SUBMIT["headline"]
        assert result.recovered_from_text_stop is True
        assert result.forced_submit is True
        # The recovery call (3rd) must have received force=True
        assert force_flags == [False, False, True]

    def test_recovery_fails_raises_with_recovery_message(self, db_session):
        """Recovery call also returns plain text -> raise with 'recovery attempted' message."""
        settings = _settings(brief_tool_max_calls=5)
        messages = [{"role": "user", "content": "generate a brief"}]

        # First call: text stop; recovery call: also text stop
        call_sequence = [
            _response(tool_calls=None, content="Here is a brief without a tool call."),
            _response(tool_calls=None, content="Still no tool call."),
        ]

        def fake_call_fn(msgs, force):
            return call_sequence.pop(0)

        with pytest.raises(BriefLoopError, match="recovery attempted"):
            run_brief_loop(db_session, messages, fake_call_fn, settings)

    def test_recovery_only_attempted_once(self, db_session):
        """A second text-stop after recovery has already been tried does not recurse."""
        settings = _settings(brief_tool_max_calls=6)
        messages = [{"role": "user", "content": "generate a brief"}]

        # search → text stop → recovery returns search (not submit_brief) → text stop again
        # The recovery should only process submit_brief; a non-submit tool_call in recovery
        # still counts as "no submit" received, causing the raise.
        call_sequence = [
            _response(tool_calls=[_tool_call("search_articles", {"query": "x"}, "tc1")]),
            _response(tool_calls=None, content="Thinking..."),  # text stop → recovery
            _response(tool_calls=None, content="Still no submit."),  # recovery also fails
        ]

        def fake_call_fn(msgs, force):
            return call_sequence.pop(0)

        with pytest.raises(BriefLoopError, match="recovery attempted"):
            run_brief_loop(db_session, messages, fake_call_fn, settings)

    def test_no_tool_calls_on_forced_final_iteration_exhausts_loop(self, db_session):
        """Plain-text stop on the forced final iteration is not retried (force already tried)."""
        settings = _settings(brief_tool_max_calls=2)
        messages = [{"role": "user", "content": "generate a brief"}]

        # Iteration 0 (force=False): search tool call
        # Iteration 1 (force=True, last): plain-text stop despite being forced
        call_sequence = [
            _response(tool_calls=[_tool_call("search_articles", {"query": "x"}, "tc1")]),
            _response(tool_calls=None, content="Still writing..."),
        ]
        force_flags: list[bool] = []

        def fake_call_fn(msgs, force):
            force_flags.append(force)
            return call_sequence.pop(0)

        with pytest.raises(BriefLoopError, match="without submit_brief"):
            run_brief_loop(db_session, messages, fake_call_fn, settings)

        # No recovery call beyond the 2 loop iterations
        assert force_flags == [False, True]

    def test_text_stop_error_message_differs_from_exhaustion(self, db_session):
        """Text-stop error message contains 'recovery attempted'; exhaustion does not."""
        settings = _settings(brief_tool_max_calls=3)
        messages = [{"role": "user", "content": "generate a brief"}]

        # Text-stop path: first call text-stops, recovery also fails
        text_stop_calls = [
            _response(tool_calls=None),
            _response(tool_calls=None),
        ]

        def fake_text_stop(msgs, force):
            return text_stop_calls.pop(0)

        with pytest.raises(BriefLoopError) as exc_info:
            run_brief_loop(db_session, messages, fake_text_stop, settings)
        assert "recovery attempted" in str(exc_info.value)
        assert "without submit_brief" in str(exc_info.value)

        # Exhaustion path: always returns search tool call, never submit_brief
        exhaustion_calls = [
            _response(tool_calls=[_tool_call("search_articles", {"query": "x"})]),
        ] * 5

        def fake_exhaustion(msgs, force):
            return exhaustion_calls.pop(0)

        with pytest.raises(BriefLoopError) as exc_info2:
            run_brief_loop(db_session, messages, fake_exhaustion, settings)
        assert "exhausted" in str(exc_info2.value)
        assert "recovery attempted" not in str(exc_info2.value)
        assert "without submit_brief" in str(exc_info2.value)


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
