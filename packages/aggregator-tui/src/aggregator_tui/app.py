from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label, Placeholder

from .api_client import ApiClient


class StatusBar(Label):
    """One-line notification bar at the bottom for transient error/info messages."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }
    """

    def show(self, message: str) -> None:
        self.update(message)

    def clear(self) -> None:
        self.update("")


class AggregatorApp(App[None]):
    """Three-pane TUI reader for the personal aggregator JSON API."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #panes {
        height: 1fr;
    }
    #nav-sidebar {
        width: 24;
        border-right: solid $surface-darken-1;
    }
    #list-pane {
        width: 2fr;
        border-right: solid $surface-darken-1;
    }
    #reader-pane {
        width: 3fr;
    }
    """

    def __init__(self, api_url: str = "http://127.0.0.1:8000/api/v1", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.api_url = api_url
        self.api_client: ApiClient | None = None

    async def on_mount(self) -> None:
        self.api_client = ApiClient(self.api_url)

    async def on_unmount(self) -> None:
        if self.api_client is not None:
            await self.api_client.aclose()

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Placeholder("Nav", id="nav-sidebar"),
            Placeholder("Articles", id="list-pane"),
            Placeholder("Reader", id="reader-pane"),
            id="panes",
        )
        yield StatusBar("", id="status-bar")

    def notify_status(self, message: str) -> None:
        """Write a short message to the status bar."""
        self.query_one("#status-bar", StatusBar).show(message)

    def clear_status(self) -> None:
        """Clear the status bar."""
        self.query_one("#status-bar", StatusBar).clear()
