import typer

from .sources import sources_app

app = typer.Typer(help="aggregator-admin: operate the datastore from the command line.")

articles_app = typer.Typer(help="Inspect and operate on articles.")
ops_app = typer.Typer(help="Pipeline diagnostics and maintenance.")

app.add_typer(sources_app, name="sources")
app.add_typer(articles_app, name="articles")
app.add_typer(ops_app, name="ops")


def main() -> None:
    app()
