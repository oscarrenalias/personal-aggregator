from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Static

from ..api_client import ApiClient, ApiError, ArticleResponse, ThreadResponse


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

    Call activate_search() to show the search input (/ key). Submitting a
    query runs _execute_search() which posts SearchFailed on ApiError.
    Call deactivate_search() to hide the input and restore list focus.

    Cursor pagination: when the user navigates to the last item and the
    last response had a non-null next_cursor, the next page is fetched
    automatically and appended to the list. A loading indicator is shown
    during the fetch; no further loads are issued when next_cursor is null.
    """

    DEFAULT_CSS = """
    ArticleList {
        height: 1fr;
    }
    ArticleList ListView {
        height: 1fr;
    }
    ArticleList #search-input {
        display: none;
    }
    ArticleList #no-results-placeholder {
        display: none;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    ArticleList #loading-indicator {
        display: none;
        height: 1;
        padding: 0 1;
        color: $text-muted;
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

    class SearchFailed(Message):
        """Posted when a search API call fails with an ApiError."""

        def __init__(self, error: str) -> None:
            super().__init__()
            self.error = error

    def __init__(self, api_client: Optional[ApiClient] = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._api_client: Optional[ApiClient] = api_client
        self._articles: list[ArticleResponse] = []
        self._threads: list[ThreadResponse] = []
        self._next_cursor: Optional[str] = None
        # Pagination state
        self._is_loading: bool = False
        self._current_mode: str = "articles"  # "articles" | "threads" | "search"
        self._current_view: str = "all"
        self._current_category: Optional[str] = None
        self._current_source_id: Optional[int] = None
        self._current_sort: str = "importance"
        self._current_show_dismissed: bool = False
        self._current_search_query: Optional[str] = None

    @property
    def articles(self) -> list[ArticleResponse]:
        return list(self._articles)

    def set_api_client(self, client: ApiClient) -> None:
        """Attach an API client after widget initialization."""
        self._api_client = client

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search articles…", id="search-input")
        yield Static("No results", id="no-results-placeholder")
        yield ListView(id="article-listview")
        yield Static("Loading…", id="loading-indicator")

    # ------------------------------------------------------------------
    # Search input lifecycle
    # ------------------------------------------------------------------

    def activate_search(self) -> None:
        """Show the search input and give it focus."""
        search_input = self.query_one("#search-input", Input)
        search_input.display = True
        search_input.focus()

    def deactivate_search(self) -> None:
        """Hide and clear the search input; restore focus to the list."""
        search_input = self.query_one("#search-input", Input)
        search_input.display = False
        search_input.value = ""
        no_results = self.query_one("#no-results-placeholder", Static)
        no_results.display = False
        listview = self.query_one("#article-listview", ListView)
        listview.display = True
        listview.focus()

    # ------------------------------------------------------------------
    # Loading indicator
    # ------------------------------------------------------------------

    def _show_loading(self) -> None:
        try:
            self.query_one("#loading-indicator", Static).display = True
        except Exception:
            pass

    def _hide_loading(self) -> None:
        try:
            self.query_one("#loading-indicator", Static).display = False
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if isinstance(event.item, ArticleRow):
            self.post_message(self.ArticleSelected(event.item.article))
        elif isinstance(event.item, ThreadRow):
            self.post_message(self.ThreadSelected(event.item.thread))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Trigger next-page load when the user highlights the last item."""
        if event.item is None or self._next_cursor is None or self._is_loading:
            return
        listview = self.query_one("#article-listview", ListView)
        list_items = [c for c in listview.children if isinstance(c, (ArticleRow, ThreadRow))]
        if list_items and event.item is list_items[-1]:
            self.run_worker(self._load_next_page(), exclusive=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Run a search when the user presses Enter in the search input."""
        query = event.value.strip()
        if not query:
            return
        await self._execute_search(query)

    # ------------------------------------------------------------------
    # Cursor pagination
    # ------------------------------------------------------------------

    async def _load_next_page(self) -> None:
        """Fetch the next page using the stored cursor and append items to the list."""
        if self._api_client is None or self._next_cursor is None or self._is_loading:
            return

        self._is_loading = True
        self._show_loading()
        try:
            if self._current_mode == "threads":
                response = await self._api_client.list_threads(
                    sort=self._current_sort,
                    show_dismissed=self._current_show_dismissed,
                    cursor=self._next_cursor,
                )
                if not self._is_loading:
                    return
                self._next_cursor = response.next_cursor
                self._threads.extend(response.items)
                listview = self.query_one("#article-listview", ListView)
                for thread in response.items:
                    listview.append(ThreadRow(thread))
            elif self._current_mode == "search":
                response = await self._api_client.search_articles(
                    q=self._current_search_query or "",
                    category=self._current_category,
                    source_id=self._current_source_id,
                    cursor=self._next_cursor,
                )
                if not self._is_loading:
                    return
                self._next_cursor = response.next_cursor
                self._articles.extend(response.items)
                listview = self.query_one("#article-listview", ListView)
                for article in response.items:
                    listview.append(ArticleRow(article))
            else:
                response = await self._api_client.list_articles(
                    view=self._current_view,
                    category=self._current_category,
                    source_id=self._current_source_id,
                    cursor=self._next_cursor,
                )
                if not self._is_loading:
                    return
                self._next_cursor = response.next_cursor
                self._articles.extend(response.items)
                listview = self.query_one("#article-listview", ListView)
                for article in response.items:
                    listview.append(ArticleRow(article))
        except Exception:
            pass
        finally:
            self._is_loading = False
            self._hide_loading()

    # ------------------------------------------------------------------
    # Search execution
    # ------------------------------------------------------------------

    async def _execute_search(self, query: str) -> None:
        """Call search_articles and populate the list; post SearchFailed on ApiError."""
        if self._api_client is None:
            return

        self._is_loading = False
        listview = self.query_one("#article-listview", ListView)
        no_results = self.query_one("#no-results-placeholder", Static)
        listview.clear()
        listview.display = True
        no_results.display = False
        self._articles = []
        self._threads = []
        self._next_cursor = None
        self._current_mode = "search"
        self._current_search_query = query
        self._current_category = None
        self._current_source_id = None

        try:
            response = await self._api_client.search_articles(q=query)
        except ApiError as exc:
            self.post_message(self.SearchFailed(str(exc)))
            return

        self._articles = response.items
        self._next_cursor = response.next_cursor

        if not self._articles:
            listview.display = False
            no_results.display = True
        else:
            for article in self._articles:
                listview.append(ArticleRow(article))

    # ------------------------------------------------------------------
    # Navigation loading (called by app on nav-item selection)
    # ------------------------------------------------------------------

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

        self._is_loading = False
        listview = self.query_one("#article-listview", ListView)
        no_results = self.query_one("#no-results-placeholder", Static)
        listview.clear()
        listview.display = True
        no_results.display = False
        self._articles = []
        self._threads = []
        self._next_cursor = None

        if query:
            self._current_mode = "search"
            self._current_search_query = query
            self._current_category = category
            self._current_source_id = source_id
        else:
            self._current_mode = "articles"
            self._current_view = view
            self._current_category = category
            self._current_source_id = source_id

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

        self._is_loading = False
        listview = self.query_one("#article-listview", ListView)
        no_results = self.query_one("#no-results-placeholder", Static)
        listview.clear()
        listview.display = True
        no_results.display = False
        self._articles = []
        self._threads = []
        self._next_cursor = None
        self._current_mode = "threads"
        self._current_sort = sort
        self._current_show_dismissed = show_dismissed

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
