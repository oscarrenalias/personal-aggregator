from __future__ import annotations

import webbrowser
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Label, ListView, Tree

from .api_client import ApiClient, ArticleResponse
from .widgets.article_list import ArticleList, ArticleRow
from .widgets.nav_sidebar import NavSidebar
from .widgets.reader_pane import ReaderPane


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

    BINDINGS = [
        Binding("j", "list_down", "Next", show=False),
        Binding("k", "list_up", "Previous", show=False),
        Binding("g", "list_top", "Top", show=False),
        Binding("G", "list_bottom", "Bottom", show=False),
        Binding("o", "open_article", "Open", show=False),
        Binding("v", "view_in_browser", "View in browser", show=False),
        Binding("tab", "focus_next_pane", "Next pane", show=False, priority=True),
        Binding("escape", "focus_list", "Back to list", show=False, priority=True),
    ]

    def __init__(self, api_url: str = "http://127.0.0.1:8000/api/v1", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.api_url = api_url
        self.api_client = ApiClient(api_url)
        self._selected_article: Optional[ArticleResponse] = None
        self._pane_focus_idx: int = 1  # 0=nav, 1=list, 2=reader

    async def on_unmount(self) -> None:
        await self.api_client.aclose()

    def compose(self) -> ComposeResult:
        yield Horizontal(
            NavSidebar(self.api_client, id="nav-sidebar"),
            ArticleList(self.api_client, id="list-pane"),
            ReaderPane(id="reader-pane"),
            id="panes",
        )
        yield StatusBar("", id="status-bar")

    def on_mount(self) -> None:
        self.query_one("#article-listview", ListView).focus()

    def notify_status(self, message: str) -> None:
        """Write a short message to the status bar."""
        self.query_one("#status-bar", StatusBar).show(message)

    def clear_status(self) -> None:
        """Clear the status bar."""
        self.query_one("#status-bar", StatusBar).clear()

    # ------------------------------------------------------------------
    # Keyboard action handlers
    # ------------------------------------------------------------------

    def action_list_down(self) -> None:
        self.query_one("#article-listview", ListView).action_cursor_down()

    def action_list_up(self) -> None:
        self.query_one("#article-listview", ListView).action_cursor_up()

    def action_list_top(self) -> None:
        listview = self.query_one("#article-listview", ListView)
        if len(listview.children) > 0:
            listview.index = 0

    def action_list_bottom(self) -> None:
        listview = self.query_one("#article-listview", ListView)
        count = len(listview.children)
        if count > 0:
            listview.index = count - 1

    def action_open_article(self) -> None:
        if self._selected_article is not None:
            self.query_one("#reader-pane", ReaderPane).load_article(self._selected_article.id)

    def action_view_in_browser(self) -> None:
        if self._selected_article is not None and self._selected_article.url:
            webbrowser.open(self._selected_article.url)
        else:
            self.notify_status("No URL available for this article.")

    def action_focus_next_pane(self) -> None:
        self._pane_focus_idx = (self._pane_focus_idx + 1) % 3
        self._apply_pane_focus()

    def action_focus_list(self) -> None:
        self._pane_focus_idx = 1
        self._apply_pane_focus()

    def _apply_pane_focus(self) -> None:
        try:
            if self._pane_focus_idx == 0:
                self.query_one("#nav-tree", Tree).focus()
            elif self._pane_focus_idx == 1:
                self.query_one("#article-listview", ListView).focus()
            else:
                self.query_one("#reader-scroll", VerticalScroll).focus()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Track the highlighted article so o/v can act on it without Enter."""
        if event.item is not None and isinstance(event.item, ArticleRow):
            self._selected_article = event.item.article

    def on_article_list_article_selected(self, event: ArticleList.ArticleSelected) -> None:
        """Load the selected article into the reader pane (Enter)."""
        self._selected_article = event.article
        self.query_one("#reader-pane", ReaderPane).load_article(event.article.id)

    def on_nav_sidebar_nav_item_selected(self, event: NavSidebar.NavItemSelected) -> None:
        """Reload the article list based on the selected nav item."""
        item = event.item
        article_list = self.query_one("#list-pane", ArticleList)
        if item.kind == "smart":
            article_list.run_worker(article_list.load(view=item.view or "all"), exclusive=True)
        elif item.kind == "today":
            article_list.run_worker(article_list.load(view="today"), exclusive=True)
        elif item.kind == "category":
            article_list.run_worker(article_list.load(category=item.category), exclusive=True)
        elif item.kind == "source":
            article_list.run_worker(article_list.load(source_id=item.source_id), exclusive=True)
