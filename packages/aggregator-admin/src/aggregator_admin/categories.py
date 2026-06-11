from __future__ import annotations

from typing import Optional

import typer
from sqlalchemy.exc import IntegrityError

from aggregator_common.db import get_session
from aggregator_common.models import Category

from .output import confirm, json_or_table

categories_app = typer.Typer(help="Manage article categories.")

_LIST_COLS = ["id", "name", "description", "enabled", "sort_order"]
_SHOW_COLS = _LIST_COLS + ["created_at", "updated_at"]


def _category_to_row(category: Category) -> dict:
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "enabled": category.enabled,
        "sort_order": category.sort_order,
        "created_at": category.created_at,
        "updated_at": category.updated_at,
    }


def _resolve_category(session, id_or_name: str) -> Category:
    """Resolve a category by id (all-digits) or exact name; exit non-zero if not found."""
    if id_or_name.isdigit():
        category = session.get(Category, int(id_or_name))
    else:
        category = session.query(Category).filter(Category.name == id_or_name).first()
    if category is None:
        typer.echo(f"Error: category '{id_or_name}' not found.", err=True)
        raise typer.Exit(code=1)
    return category


@categories_app.command("list")
def list_categories(
    enabled: Optional[bool] = typer.Option(None, "--enabled/--disabled", help="Filter by enabled state."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List all categories."""
    with get_session() as session:
        q = session.query(Category)
        if enabled is not None:
            q = q.filter(Category.enabled == enabled)
        categories = q.order_by(Category.sort_order, Category.name).all()
        rows = [_category_to_row(c) for c in categories]
    json_or_table(rows, _LIST_COLS, as_json=as_json)


@categories_app.command("show")
def show_category(
    id_or_name: str = typer.Argument(..., help="Category ID or exact name."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show full details for a category."""
    with get_session() as session:
        category = _resolve_category(session, id_or_name)
        row = _category_to_row(category)
    json_or_table([row], _SHOW_COLS, as_json=as_json)


@categories_app.command("add")
def add_category(
    name: str = typer.Argument(..., help="Category name (must be unique)."),
    description: Optional[str] = typer.Option(None, "--description", "-d", help="Category description."),
    sort_order: int = typer.Option(0, "--sort-order", help="Display sort order."),
    disabled: bool = typer.Option(False, "--disabled", help="Create the category in a disabled state."),
) -> None:
    """Add a new category."""
    category = Category(
        name=name,
        description=description,
        sort_order=sort_order,
        enabled=not disabled,
    )
    try:
        with get_session() as session:
            session.add(category)
            session.flush()
            new_id = category.id
    except IntegrityError:
        typer.echo(f"Error: a category named '{name}' already exists.", err=True)
        raise typer.Exit(code=1)
    typer.echo(new_id)


@categories_app.command("rename")
def rename_category(
    id_or_name: str = typer.Argument(..., help="Category ID or exact name."),
    new_name: str = typer.Argument(..., help="New category name."),
) -> None:
    """Rename a category."""
    old_name = None
    try:
        with get_session() as session:
            category = _resolve_category(session, id_or_name)
            old_name = category.name
            category.name = new_name
            session.flush()
    except IntegrityError:
        typer.echo(f"Error: a category named '{new_name}' already exists.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Category '{old_name}' renamed to '{new_name}'.")
    typer.echo("Hint: run 'articles rerank --all' to refresh existing article tags.")


@categories_app.command("set-description")
def set_description(
    id_or_name: str = typer.Argument(..., help="Category ID or exact name."),
    text: str = typer.Argument(..., help="New description text."),
) -> None:
    """Update the description of a category."""
    with get_session() as session:
        category = _resolve_category(session, id_or_name)
        category.description = text
    typer.echo(f"Category '{id_or_name}' description updated.")


@categories_app.command("set-order")
def set_order(
    id_or_name: str = typer.Argument(..., help="Category ID or exact name."),
    n: int = typer.Argument(..., help="New sort order value."),
) -> None:
    """Update the sort order of a category."""
    with get_session() as session:
        category = _resolve_category(session, id_or_name)
        category.sort_order = n
    typer.echo(f"Category '{id_or_name}' sort order set to {n}.")


@categories_app.command("enable")
def enable_category(
    id_or_name: str = typer.Argument(..., help="Category ID or exact name."),
) -> None:
    """Enable a category."""
    with get_session() as session:
        category = _resolve_category(session, id_or_name)
        category.enabled = True
    typer.echo(f"Category '{id_or_name}' enabled.")


@categories_app.command("disable")
def disable_category(
    id_or_name: str = typer.Argument(..., help="Category ID or exact name."),
) -> None:
    """Disable a category."""
    with get_session() as session:
        category = _resolve_category(session, id_or_name)
        category.enabled = False
    typer.echo(f"Category '{id_or_name}' disabled.")


@categories_app.command("remove")
def remove_category(
    id_or_name: str = typer.Argument(..., help="Category ID or exact name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete a category."""
    with get_session() as session:
        category = _resolve_category(session, id_or_name)
        confirm(yes=yes, prompt=f"Delete category '{category.name}'?")
        session.delete(category)
    typer.echo(f"Category '{id_or_name}' deleted.")
