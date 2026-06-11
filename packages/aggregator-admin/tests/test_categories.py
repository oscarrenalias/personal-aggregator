"""Integration tests for categories sub-commands."""
from __future__ import annotations

import json

from aggregator_admin.main import app
from aggregator_common.models import Category


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_category(
    session,
    *,
    name: str = "Tech",
    description: str | None = "Technology articles",
    enabled: bool = True,
    sort_order: int = 0,
) -> Category:
    cat = Category(name=name, description=description, enabled=enabled, sort_order=sort_order)
    session.add(cat)
    session.flush()
    session.commit()
    session.refresh(cat)
    return cat


# ---------------------------------------------------------------------------
# categories add
# ---------------------------------------------------------------------------

def test_categories_add_prints_new_id(runner, db_session):
    result = runner.invoke(app, ["categories", "add", "Science"])
    assert result.exit_code == 0
    new_id = int(result.output.strip())
    assert new_id >= 1


def test_categories_add_with_description(runner, db_session):
    result = runner.invoke(app, ["categories", "add", "AI", "--description", "Artificial intelligence"])
    assert result.exit_code == 0


def test_categories_add_duplicate_name_exits_nonzero(runner, db_session):
    make_category(db_session, name="Tech")
    result = runner.invoke(app, ["categories", "add", "Tech"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_categories_add_disabled_flag(runner, db_session):
    result = runner.invoke(app, ["categories", "add", "Hidden", "--disabled"])
    assert result.exit_code == 0
    cat_id = int(result.output.strip())
    cat = db_session.get(Category, cat_id)
    assert cat.enabled is False


# ---------------------------------------------------------------------------
# categories list
# ---------------------------------------------------------------------------

def test_categories_list_all(runner, db_session):
    make_category(db_session, name="Tech", enabled=True)
    make_category(db_session, name="Sports", enabled=False)
    result = runner.invoke(app, ["categories", "list"])
    assert result.exit_code == 0
    assert "Tech" in result.output
    assert "Sports" in result.output


def test_categories_list_enabled_filter(runner, db_session):
    make_category(db_session, name="Enabled", enabled=True)
    make_category(db_session, name="Disabled", enabled=False)
    result = runner.invoke(app, ["categories", "list", "--enabled", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    names = [c["name"] for c in data]
    assert "Enabled" in names
    assert "Disabled" not in names
    assert all(c["enabled"] is True for c in data)


def test_categories_list_disabled_filter(runner, db_session):
    make_category(db_session, name="Enabled", enabled=True)
    make_category(db_session, name="Disabled", enabled=False)
    result = runner.invoke(app, ["categories", "list", "--disabled", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    names = [c["name"] for c in data]
    assert "Disabled" in names
    assert "Enabled" not in names
    assert all(c["enabled"] is False for c in data)


def test_categories_list_json_output(runner, db_session):
    make_category(db_session, name="JSON-Cat")
    result = runner.invoke(app, ["categories", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert any(c["name"] == "JSON-Cat" for c in data)


# ---------------------------------------------------------------------------
# categories show
# ---------------------------------------------------------------------------

def test_categories_show_by_id(runner, db_session):
    cat = make_category(db_session, name="ShowCat")
    result = runner.invoke(app, ["categories", "show", str(cat.id), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["name"] == "ShowCat"


def test_categories_show_by_name(runner, db_session):
    make_category(db_session, name="ByName")
    result = runner.invoke(app, ["categories", "show", "ByName", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data[0]["name"] == "ByName"


def test_categories_show_not_found(runner, db_session):
    result = runner.invoke(app, ["categories", "show", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# categories rename
# ---------------------------------------------------------------------------

def test_categories_rename(runner, db_session):
    cat = make_category(db_session, name="OldName")
    result = runner.invoke(app, ["categories", "rename", str(cat.id), "NewName"])
    assert result.exit_code == 0
    db_session.refresh(cat)
    assert cat.name == "NewName"
    assert "rerank" in result.output.lower()


def test_categories_rename_duplicate_exits_nonzero(runner, db_session):
    make_category(db_session, name="Existing")
    cat2 = make_category(db_session, name="Other")
    result = runner.invoke(app, ["categories", "rename", str(cat2.id), "Existing"])
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_categories_rename_not_found(runner, db_session):
    result = runner.invoke(app, ["categories", "rename", "9999", "Anything"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# categories set-description
# ---------------------------------------------------------------------------

def test_categories_set_description(runner, db_session):
    cat = make_category(db_session, name="DescCat", description="old desc")
    result = runner.invoke(app, ["categories", "set-description", str(cat.id), "new desc"])
    assert result.exit_code == 0
    db_session.refresh(cat)
    assert cat.description == "new desc"


def test_categories_set_description_not_found(runner, db_session):
    result = runner.invoke(app, ["categories", "set-description", "9999", "anything"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# categories set-order
# ---------------------------------------------------------------------------

def test_categories_set_order(runner, db_session):
    cat = make_category(db_session, name="OrderCat", sort_order=0)
    result = runner.invoke(app, ["categories", "set-order", str(cat.id), "5"])
    assert result.exit_code == 0
    db_session.refresh(cat)
    assert cat.sort_order == 5


def test_categories_set_order_not_found(runner, db_session):
    result = runner.invoke(app, ["categories", "set-order", "9999", "3"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# categories enable / disable
# ---------------------------------------------------------------------------

def test_categories_enable(runner, db_session):
    cat = make_category(db_session, name="EnableMe", enabled=False)
    result = runner.invoke(app, ["categories", "enable", str(cat.id)])
    assert result.exit_code == 0
    db_session.refresh(cat)
    assert cat.enabled is True


def test_categories_enable_not_found(runner, db_session):
    result = runner.invoke(app, ["categories", "enable", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_categories_disable(runner, db_session):
    cat = make_category(db_session, name="DisableMe", enabled=True)
    result = runner.invoke(app, ["categories", "disable", str(cat.id)])
    assert result.exit_code == 0
    db_session.refresh(cat)
    assert cat.enabled is False


def test_categories_disable_not_found(runner, db_session):
    result = runner.invoke(app, ["categories", "disable", "9999"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# categories remove
# ---------------------------------------------------------------------------

def test_categories_remove_with_yes(runner, db_session):
    cat = make_category(db_session, name="RemoveMe")
    cat_id = cat.id
    result = runner.invoke(app, ["categories", "remove", str(cat_id), "--yes"])
    assert result.exit_code == 0
    assert db_session.query(Category).filter(Category.id == cat_id).count() == 0


def test_categories_remove_not_found(runner, db_session):
    result = runner.invoke(app, ["categories", "remove", "9999", "--yes"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_categories_remove_no_yes_noninteractive_exits_1(runner, db_session):
    cat = make_category(db_session, name="PromptMe")
    result = runner.invoke(app, ["categories", "remove", str(cat.id)])
    assert result.exit_code == 1
    assert "non-interactive" in result.output
