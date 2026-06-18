from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ListItem, ListView, Static

from ..api_client import ApiClient, ArticleResponse, ThreadResponse


def _format_date(dt_str: Optional[str]) -> str:
    if not dt_str:
        return ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d")
    except (ValueError, AttributeError):
        return ""


class ArticleRow(ListItem):
    """A list item representing one article."""

    DEFAULT_CSS = """
    ArticleRow {
        height: auto;
        padding: 0 1;
    }
    ArticleRow Static {
        width: 1fr;
    }
    """

    def __init__(self, article: ArticleResponse) -> None:
        super().__init__()
        self.article = article

    def _make_markup(self) -> str:
        read_dot = " " if self.article.is_read else "[bold green]●[/]"
        saved_star = "[yellow]★[/]" if self.article.is_saved else " "
        title = self.article.title or "(no title)"
        source = self.article.source_name or ""
        date_str = _format_date(self.article.feed_published_at)
        meta = f"{source}  {date_str}".strip()
        return f"{read_dot}{saved_star} {title}\n[dim]   {meta}[/dim]"

    def compose(self) -> ComposeResult:
        yield Static(self._make_markup(), markup=True)

    def refresh_display(self) -> None:
        try:
            self.query_one(Static).update(self._make_markup())
        except Exception:
            pass


class ThreadRow(ListItem):
    """A list item representing one thread."""

    DEFAULT_CSS = """
    ThreadRow {
        height: auto;
        padding: 0 1;
    }
    ThreadRow Static {
        width: 1fr;
    }
    """

    def __init__(self, thread: ThreadResponse) -> None:
        super().__init__()
        self.thread = thread

    def _make_markup(self) -> str:
        updates_dot = "[bold cyan]●[/]" if self.thread.has_updates else " "
        dismissed_marker = "[dim]✗[/dim]" if self.thread.dismissed else " "
        title = self.thread.representative_title or "(no title)"
        meta_parts: list[str] = []
        if self.thread.member_count:
            meta_parts.append(f"{self.thread.member_count} art.")
        if self.thread.source_count:
            meta_parts.append(f"{self.thread.source_count} src.")
        if self.thread.tier:
            meta_parts.append(self.thread.tier)
        meta = "  ".join(meta_parts)
        return f"{updates_dot}{dismissed_marker} {title}\n[dim]   {meta}[/dim]"

    def compose(self) -> ComposeResult:
        yield Static(self._make_markup(), markup=True)

    def refresh_display(self) -> None:
        try:
            self.query_one(Static).update(self._make_markup())
        except Exception:
            pass


class ArticleList(Widget):
    """Scrollable list of article rows or thread rows with selection tracking.

    Exposes load() for articles and load_threads() for threads. Posts
    ArticleSelected or ThreadSelected when the user activates an item.
    """

    DEFAULT_CSS = """
    ArticleList {
        height: 1fr;
    }
    ArticleList ListView {
        height: 1fr;
    }
    """

    class ArticleSelected(Message):
        """Posted when the user selects an article from the list."""

        def __init__(self, article: ArticleResponse) -> None:
            super().__init__()
            self.article = article

    class ThreadSelected(Message):
        """Posted when the user selects a thread from the list."""

        def __init__(self, thread: ThreadResponse) -> None:
            super().__init__()
            self.thread = thread

    def __init__(self, api_client: Optional[ApiClient] = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._api_client: Optional[ApiClient] = api_client
        self._articles: list[ArticleResponse] = []
        self._threads: list[ThreadResponse] = []
        self._next_cursor: Optional[str] = None

    @property
    def articles(self) -> list[ArticleResponse]:
        return list(self._articles)

    def set_api_client(self, client: ApiClient) -> None:
        """Attach an API client after widget initialization."""
        self._api_client = client

    def compose(self) -> ComposeResult:
        yield ListView(id="article-listview")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if isinstance(event.item, ArticleRow):
            self.post_message(self.ArticleSelected(event.item.article))
        elif isinstance(event.item, ThreadRow):
            self.post_message(self.ThreadSelected(event.item.thread))

    async def load(
        self,
        view: str = "all",
        category: Optional[str] = None,
        source_id: Optional[int] = None,
        query: Optional[str] = None,
    ) -> None:
        """Load articles from the API and repopulate the list.

        Calls search_articles when query is given; otherwise calls list_articles.
        Silently returns if no API client is attached.
        """
        if self._api_client is None:
            return

        listview = self.query_one("#article-listview", ListView)
        listview.clear()
        self._articles = []
        self._threads = []
        self._next_cursor = None

        try:
            if query:
                response = await self._api_client.search_articles(
                    q=query,
                    category=category,
                    source_id=source_id,
                )
            else:
                response = await self._api_client.list_articles(
                    view=view,
                    category=category,
                    source_id=source_id,
                )
        except Exception:
            return

        self._articles = response.items
        self._next_cursor = response.next_cursor

        for article in self._articles:
            listview.append(ArticleRow(article))

    async def load_threads(
        self,
        sort: str = "importance",
        show_dismissed: bool = False,
    ) -> None:
        """Load threads from the API and repopulate the list."""
        if self._api_client is None:
            return

        listview = self.query_one("#article-listview", ListView)
        listview.clear()
        self._articles = []
        self._threads = []
        self._next_cursor = None

        try:
            response = await self._api_client.list_threads(
                sort=sort,
                show_dismissed=show_dismissed,
            )
        except Exception:
            return

        self._threads = response.items
        self._next_cursor = response.next_cursor

        for thread in self._threads:
            listview.append(ThreadRow(thread))
