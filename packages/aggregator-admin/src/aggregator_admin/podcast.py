from __future__ import annotations

import json
from typing import Any, Optional

import typer
from sqlalchemy import select

from aggregator_common.db import get_session
from aggregator_common.models import PodcastEpisode
from aggregator_common import queries

from .output import console, json_default, json_or_table

podcast_app = typer.Typer(help="Manage podcast episodes.")

_LIST_COLUMNS = ["id", "date", "status", "origin", "duration_seconds", "episode_theme"]


def _episode_to_dict(ep: PodcastEpisode) -> dict[str, Any]:
    return {
        "id": ep.id,
        "date": str(ep.date),
        "status": ep.status,
        "origin": ep.origin,
        "episode_theme": ep.episode_theme,
        "audio_path": ep.audio_path,
        "audio_size_bytes": ep.audio_size_bytes,
        "duration_seconds": ep.duration_seconds,
        "llm_model": ep.llm_model,
        "tts_model": ep.tts_model,
        "tts_voice": ep.tts_voice,
        "error": ep.error,
        "generated_at": ep.generated_at,
        "created_at": ep.created_at,
        "updated_at": ep.updated_at,
    }


@podcast_app.command("generate")
def generate_podcast() -> None:
    """Enqueue an immediate podcast episode for today."""
    with get_session() as session:
        result = queries.enqueue_podcast(session)
    if result["status"] == "already_pending":
        typer.echo(f"Episode already pending (id={result['id']}); the podcast service will pick it up shortly.")
    else:
        typer.echo(f"Episode {result['id']} queued (date={result['date']}, origin=manual).")


@podcast_app.command("list")
def list_episodes(
    limit: int = typer.Option(10, "--limit", help="Maximum number of results."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List recent podcast episodes."""
    with get_session() as session:
        stmt = select(PodcastEpisode).order_by(PodcastEpisode.date.desc()).limit(limit)
        rows = [
            {
                "id": ep.id,
                "date": str(ep.date),
                "status": ep.status,
                "origin": ep.origin,
                "duration_seconds": ep.duration_seconds,
                "episode_theme": (ep.episode_theme or "")[:60],
            }
            for ep in session.scalars(stmt).all()
        ]

    json_or_table(rows, _LIST_COLUMNS, as_json=as_json)


@podcast_app.command("show")
def show_episode(
    episode_id: Optional[int] = typer.Argument(
        None,
        help="Episode ID. Defaults to the latest ready episode.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show details for a podcast episode (default: latest ready)."""
    with get_session() as session:
        if episode_id is not None:
            ep = session.get(PodcastEpisode, episode_id)
            if ep is None:
                typer.echo(f"Error: episode {episode_id} not found.", err=True)
                raise typer.Exit(code=1)
        else:
            ep = queries.get_latest_podcast_episode(session)
            if ep is None:
                typer.echo("No ready podcast episode found.", err=True)
                raise typer.Exit(code=1)

        if as_json:
            typer.echo(json.dumps(_episode_to_dict(ep), default=json_default))
            return

        console.print(f"[bold]Episode {ep.id}[/bold]  date={ep.date}  status={ep.status}  origin={ep.origin}")
        if ep.generated_at:
            console.print(f"Generated: {ep.generated_at.isoformat()}")
        if ep.duration_seconds:
            mins, secs = divmod(ep.duration_seconds, 60)
            console.print(f"Duration:  {mins}:{secs:02d}")
        if ep.episode_theme:
            console.print()
            console.print(f"[bold]{ep.episode_theme}[/bold]")
        if ep.audio_path:
            console.print(f"Audio: {ep.audio_path}")
        if ep.error:
            console.print(f"[red]Error: {ep.error}[/red]")
