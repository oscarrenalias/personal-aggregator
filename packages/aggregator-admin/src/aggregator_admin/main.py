import typer

app = typer.Typer(help="aggregator-admin: operate the datastore from the command line.")

sources_app = typer.Typer(help="Manage feed sources.")
articles_app = typer.Typer(help="Inspect and operate on articles.")
ops_app = typer.Typer(help="Pipeline diagnostics and maintenance.")

app.add_typer(sources_app, name="sources")
app.add_typer(articles_app, name="articles")
app.add_typer(ops_app, name="ops")


def main() -> None:
    app()
