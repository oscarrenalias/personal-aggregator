import typer

from .articles import articles_app
from .ops import ops_app
from .sources import sources_app

app = typer.Typer(help="aggregator-admin: operate the datastore from the command line.")

app.add_typer(sources_app, name="sources")
app.add_typer(articles_app, name="articles")
app.add_typer(ops_app, name="ops")


def main() -> None:
    app()
