import sys

import typer

from aggregator_common import load_env
from aggregator_common.config import Settings
from aggregator_common.logging_setup import configure_logging

from .articles import articles_app
from .brief import brief_app
from .categories import categories_app
from .clusters import clusters_app
from .llm_stats import llm_stats_cmd
from .ops import ops_app
from .profile import profile_app
from .sources import sources_app

app = typer.Typer(help="aggregator-admin: operate the datastore from the command line.")

app.add_typer(sources_app, name="sources")
app.add_typer(articles_app, name="articles")
app.add_typer(ops_app, name="ops")
app.add_typer(profile_app, name="profile")
app.add_typer(categories_app, name="categories")
app.add_typer(brief_app, name="brief")
app.add_typer(clusters_app, name="clusters")
app.command("llm-stats")(llm_stats_cmd)


@app.callback()
def _startup() -> None:
    load_env()
    configure_logging(Settings(), stream=sys.stderr)


def main() -> None:
    app()
