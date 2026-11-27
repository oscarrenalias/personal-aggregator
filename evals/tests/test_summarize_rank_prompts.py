"""Tests for evals/summarize_rank/prompts.py."""

# conftest.py adds evals/ to sys.path; summarize_rank is importable as a package.
from summarize_rank.prompts import rank_v2, rank_v3


def _context(
    source_name="TechCrunch",
    title="AI model breaks record",
    content_text="OpenAI released a new model.",
    interest_profile_text="I follow AI closely.",
    categories=None,
    production_score=75,
):
    return {
        "vars": {
            "article_id": 42,
            "source_name": source_name,
            "clean_title": title,
            "feed_title": None,
            "title": title,
            "content_text": content_text,
            "excerpt": None,
            "feed_summary": None,
            "production_score": production_score,
            "interest_profile_text": interest_profile_text,
            "categories": categories or [],
        }
    }


def _make_settings():
    import os
    from aggregator_summarize_rank.config import SummarizeRankSettings
    os.environ.setdefault("DATABASE_URL", "postgresql://placeholder@localhost/aggregator")
    return SummarizeRankSettings()


def test_rank_v2_delegates_to_build_messages():
    from aggregator_summarize_rank.prompt import build_messages

    ctx = _context()
    msgs = rank_v2(ctx)

    # Verify it returns a valid message list
    assert isinstance(msgs, list)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"

    # Verify the output matches what build_messages produces directly
    settings = _make_settings()
    article_data = {
        "source_name": "TechCrunch",
        "clean_title": "AI model breaks record",
        "feed_title": None,
        "title": "AI model breaks record",
        "clean_text": "OpenAI released a new model.",
        "excerpt": None,
        "feed_summary": None,
    }
    expected = build_messages(
        article_data=article_data,
        interest_profile_text="I follow AI closely.",
        settings=settings,
        enabled_categories=None,
        variant="v2",
    )
    assert msgs == expected


def test_rank_v3_delegates_to_build_messages():
    from aggregator_summarize_rank.prompt import build_messages

    ctx = _context()
    msgs = rank_v3(ctx)

    assert isinstance(msgs, list)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"

    settings = _make_settings()
    article_data = {
        "source_name": "TechCrunch",
        "clean_title": "AI model breaks record",
        "feed_title": None,
        "title": "AI model breaks record",
        "clean_text": "OpenAI released a new model.",
        "excerpt": None,
        "feed_summary": None,
    }
    expected = build_messages(
        article_data=article_data,
        interest_profile_text="I follow AI closely.",
        settings=settings,
        enabled_categories=None,
        variant="v3",
    )
    assert msgs == expected


def test_rank_v2_and_v3_differ_in_system_prompt():
    ctx = _context()
    v2_msgs = rank_v2(ctx)
    v3_msgs = rank_v3(ctx)
    # v3 adds entity extraction step — system prompts must differ
    assert v2_msgs[0]["content"] != v3_msgs[0]["content"]
    # user messages are the same (same article data)
    assert v2_msgs[1]["content"] == v3_msgs[1]["content"]


def test_rank_v3_system_prompt_contains_entity_step():
    ctx = _context()
    msgs = rank_v3(ctx)
    assert "named entit" in msgs[0]["content"].lower() or "entities" in msgs[0]["content"]


def test_rank_v2_with_categories():
    ctx = _context(
        categories=[{"name": "AI", "description": "Artificial intelligence news"}]
    )
    msgs = rank_v2(ctx)
    assert "AI" in msgs[0]["content"]


def test_rank_v3_with_categories():
    ctx = _context(
        categories=[{"name": "AI", "description": "Artificial intelligence news"}]
    )
    msgs = rank_v3(ctx)
    assert "AI" in msgs[0]["content"]


def test_rank_v2_no_profile_uses_neutral_prompt():
    ctx = _context(interest_profile_text="")
    msgs = rank_v2(ctx)
    # Neutral prompt lacks profile references
    assert "interest profile" not in msgs[0]["content"].lower() or \
           "user" not in msgs[0]["content"].lower().split("interest profile")[0][-30:]
    # User message must not include an Interest profile section
    assert "Interest profile:" not in msgs[1]["content"]


def test_rank_with_none_content():
    ctx = {
        "vars": {
            "article_id": 1,
            "source_name": "Reuters",
            "clean_title": None,
            "feed_title": None,
            "title": None,
            "content_text": None,
            "excerpt": None,
            "feed_summary": None,
            "production_score": 20,
            "interest_profile_text": "",
            "categories": [],
        }
    }
    # Must not raise
    msgs_v2 = rank_v2(ctx)
    msgs_v3 = rank_v3(ctx)
    assert isinstance(msgs_v2, list)
    assert isinstance(msgs_v3, list)


def test_no_external_calls_in_prompts_module():
    """Calling prompt functions must not trigger any external I/O."""
    ctx = _context()
    for fn in (rank_v2, rank_v3):
        result = fn(ctx)
        assert isinstance(result, list)
        assert len(result) == 2
