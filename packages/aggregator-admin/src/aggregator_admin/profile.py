from __future__ import annotations

import json
from typing import Optional

import typer
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggregator_common.db import get_session
from aggregator_common.models import InterestProfile

profile_app = typer.Typer(help="Manage the interest profile used for article ranking.")


@profile_app.command("set")
def set_profile(
    text: Optional[str] = typer.Argument(None, help="Profile text to set."),
    file: Optional[str] = typer.Option(None, "--file", help="Path to a file containing profile text."),
) -> None:
    """Set (or replace) the interest profile used for article ranking."""
    if text is not None and file is not None:
        typer.echo("Error: provide either text or --file, not both.", err=True)
        raise typer.Exit(code=1)
    if text is None and file is None:
        typer.echo("Error: provide either text argument or --file.", err=True)
        raise typer.Exit(code=1)

    if file is not None:
        try:
            with open(file) as fh:
                profile_text: str = fh.read()
        except OSError as exc:
            typer.echo(f"Error reading file: {exc}", err=True)
            raise typer.Exit(code=1)
    else:
        assert text is not None  # already validated above that exactly one is provided
        profile_text = text

    with get_session() as session:
        stmt = (
            pg_insert(InterestProfile)
            .values(id=True, profile_text=profile_text)
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"profile_text": profile_text, "updated_at": func.now()},
            )
        )
        session.execute(stmt)

    typer.echo(f"Profile set ({len(profile_text)} characters).")


@profile_app.command("show")
def show_profile(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show the current interest profile."""
    with get_session() as session:
        profile = session.get(InterestProfile, True)

    if profile is None or not profile.profile_text:
        if as_json:
            typer.echo(json.dumps({"profile_text": "", "updated_at": None}))
        else:
            typer.echo("(empty — neutral ranking)")
        return

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "profile_text": profile.profile_text,
                    "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
                }
            )
        )
    else:
        typer.echo(profile.profile_text)
        typer.echo(f"Updated at: {profile.updated_at}")
