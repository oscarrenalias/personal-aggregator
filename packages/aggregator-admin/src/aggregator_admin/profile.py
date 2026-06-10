from __future__ import annotations

import json

import typer

from aggregator_common.db import get_session
from aggregator_common.models import InterestProfile

profile_app = typer.Typer(help="Manage the interest profile used for article ranking.")


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
