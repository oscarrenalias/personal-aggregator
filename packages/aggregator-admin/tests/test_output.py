"""Unit tests for output utilities: render_table, json_or_table, confirm."""
from __future__ import annotations

import io
import json

import pytest
import typer
from rich.console import Console

import aggregator_admin.output as output_mod


def test_render_table_headers_and_values():
    buf = io.StringIO()
    old = output_mod.console
    output_mod.console = Console(file=buf, width=200, highlight=False, markup=False)
    try:
        output_mod.render_table(
            [{"name": "Feed A", "url": "http://example.com"}],
            ["name", "url"],
        )
    finally:
        output_mod.console = old
    out = buf.getvalue()
    assert "name" in out
    assert "url" in out
    assert "Feed A" in out
    assert "http://example.com" in out


def test_render_table_multi_column():
    buf = io.StringIO()
    old = output_mod.console
    output_mod.console = Console(file=buf, width=200, highlight=False, markup=False)
    try:
        output_mod.render_table(
            [{"col_a": "val1", "col_b": "val2", "col_c": "val3"}],
            ["col_a", "col_b", "col_c"],
        )
    finally:
        output_mod.console = old
    out = buf.getvalue()
    assert "col_a" in out
    assert "col_b" in out
    assert "col_c" in out
    assert "val1" in out
    assert "val2" in out
    assert "val3" in out


def test_json_or_table_as_json_valid(capsys):
    rows = [{"id": 1, "name": "foo"}, {"id": 2, "name": "bar"}]
    output_mod.json_or_table(rows, ["id", "name"], as_json=True)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed == rows


def test_json_or_table_as_json_empty_list(capsys):
    output_mod.json_or_table([], ["id"], as_json=True)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []


def test_json_or_table_table_mode_renders_rows():
    buf = io.StringIO()
    old = output_mod.console
    output_mod.console = Console(file=buf, width=200, highlight=False, markup=False)
    try:
        output_mod.json_or_table(
            [{"id": "42", "status": "ready"}],
            ["id", "status"],
            as_json=False,
        )
    finally:
        output_mod.console = old
    out = buf.getvalue()
    assert "42" in out
    assert "ready" in out


def test_confirm_yes_proceeds():
    # Must not raise
    output_mod.confirm(yes=True, prompt="Delete everything?")


def test_confirm_non_tty_without_yes_exits_nonzero():
    # In the test runner sys.stdin is not a TTY → non-interactive path
    with pytest.raises(typer.Exit) as exc_info:
        output_mod.confirm(yes=False, prompt="Delete?")
    assert exc_info.value.exit_code == 1


def test_confirm_tty_answer_no_exits_nonzero(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "N")
    with pytest.raises(typer.Exit) as exc_info:
        output_mod.confirm(yes=False, prompt="Really?")
    assert exc_info.value.exit_code == 1


def test_confirm_tty_answer_yes_proceeds(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("typer.prompt", lambda *args, **kwargs: "y")
    output_mod.confirm(yes=False, prompt="Really?")
