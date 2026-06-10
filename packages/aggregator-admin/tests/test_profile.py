"""Integration tests for profile sub-commands."""
from __future__ import annotations

import json

from aggregator_admin.main import app
from aggregator_common.models import InterestProfile


# ---------------------------------------------------------------------------
# profile show
# ---------------------------------------------------------------------------

def test_profile_show_empty_no_row(runner):
    result = runner.invoke(app, ["profile", "show"])
    assert result.exit_code == 0
    assert "(empty" in result.output


def test_profile_show_empty_no_row_json(runner):
    result = runner.invoke(app, ["profile", "show", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["profile_text"] == ""
    assert data["updated_at"] is None


def test_profile_show_populated(runner, db_session):
    db_session.add(InterestProfile(id=True, profile_text="Python news"))
    db_session.commit()
    result = runner.invoke(app, ["profile", "show"])
    assert result.exit_code == 0
    assert "Python news" in result.output


def test_profile_show_populated_json(runner, db_session):
    db_session.add(InterestProfile(id=True, profile_text="Python news"))
    db_session.commit()
    result = runner.invoke(app, ["profile", "show", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["profile_text"] == "Python news"
    assert data["updated_at"] is not None


# ---------------------------------------------------------------------------
# profile set
# ---------------------------------------------------------------------------

def test_profile_set_positional_text(runner, db_session):
    result = runner.invoke(app, ["profile", "set", "I like Python"])
    assert result.exit_code == 0
    assert "13" in result.output  # len("I like Python") == 13
    db_session.expire_all()
    profile = db_session.get(InterestProfile, True)
    assert profile is not None
    assert profile.profile_text == "I like Python"


def test_profile_set_from_file(runner, db_session, tmp_path):
    f = tmp_path / "profile.txt"
    f.write_text("Interests from file")
    result = runner.invoke(app, ["profile", "set", "--file", str(f)])
    assert result.exit_code == 0
    db_session.expire_all()
    profile = db_session.get(InterestProfile, True)
    assert profile is not None
    assert profile.profile_text == "Interests from file"


def test_profile_set_neither_arg_exits_nonzero(runner):
    result = runner.invoke(app, ["profile", "set"])
    assert result.exit_code == 1


def test_profile_set_both_args_exits_nonzero(runner, tmp_path):
    f = tmp_path / "profile.txt"
    f.write_text("something")
    result = runner.invoke(app, ["profile", "set", "text arg", "--file", str(f)])
    assert result.exit_code == 1


def test_profile_set_singleton_invariant(runner, db_session):
    runner.invoke(app, ["profile", "set", "First profile"])
    result = runner.invoke(app, ["profile", "set", "Updated profile"])
    assert result.exit_code == 0
    db_session.expire_all()
    profiles = db_session.query(InterestProfile).all()
    assert len(profiles) == 1
    assert profiles[0].profile_text == "Updated profile"


# ---------------------------------------------------------------------------
# profile clear
# ---------------------------------------------------------------------------

def test_profile_clear_with_yes_clears_text(runner, db_session):
    db_session.add(InterestProfile(id=True, profile_text="Something"))
    db_session.commit()
    result = runner.invoke(app, ["profile", "clear", "--yes"])
    assert result.exit_code == 0
    assert "cleared" in result.output
    db_session.expire_all()
    profile = db_session.get(InterestProfile, True)
    assert profile is not None
    assert profile.profile_text == ""


def test_profile_clear_no_yes_noninteractive_exits_nonzero(runner, db_session):
    db_session.add(InterestProfile(id=True, profile_text="Something"))
    db_session.commit()
    result = runner.invoke(app, ["profile", "clear"])
    assert result.exit_code == 1
    assert "non-interactive" in result.output


def test_profile_clear_no_row_is_noop(runner):
    result = runner.invoke(app, ["profile", "clear", "--yes"])
    assert result.exit_code == 0
