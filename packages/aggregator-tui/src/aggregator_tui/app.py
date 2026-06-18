from __future__ import annotations

import webbrowser
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Resize
from textual.widgets import Footer, Input, Label, ListView, OptionList, Tree

from .api_client import ApiClient, ApiError, ArticleResponse, ThreadResponse
from .widgets.article_list import ArticleList, ArticleRow, ThreadRow
from .widgets.help_overlay import HelpOverlay
from .widgets.nav_sidebar import NavItem, NavSidebar
from .widgets.reader_pane import ReaderPane

# Below this column count only one pane is visible at a time (narrow/single-pane mode).
NARROW_THRESHOLD = 100


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
        width: 34;
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
        Binding("o", "open_article", "Open", show=True),
        Binding("v", "view_in_browser", "View in browser", show=False),
        Binding("tab", "focus_next_pane", "Next pane", show=False, priority=True),
        Binding("escape", "focus_list", "Back to list", show=False, priority=True),
        Binding("left", "back_to_thread", "Back to thread", show=False),
        Binding("m", "toggle_read", "Read", show=True),
        Binding("n", "mark_read_next", "Mark read + next", show=False),
        Binding("s", "toggle_save", "Save", show=True),
        Binding("d", "dismiss_thread", "Dismiss/restore thread", show=False),
        Binding("/", "activate_search", "Search", show=True),
        Binding("u", "toggle_filter", "Filter", show=True),
        Binding("r", "toggle_sort", "Sort", show=False),
        Binding("R", "refresh", "Refresh", show=True),
        Binding("question_mark", "show_help", "Help", show=True),
        Binding("q", "quit_app", "Quit", show=True),
    ]

    def __init__(self, api_url: str = "http://localhost:8000/api/v1", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.api_url = api_url
        self.api_client = ApiClient(api_url)
        self._selected_article: Optional[ArticleResponse] = None
        self._selected_article_row: Optional[ArticleRow] = None
        self._selected_thread: Optional[ThreadResponse] = None
        self._selected_thread_row: Optional[ThreadRow] = None
        self._current_nav_kind: str = "smart"
        self._pane_focus_idx: int = 1  # 0=nav, 1=list, 2=reader
        self._in_search_mode: bool = False
        self._last_nav_item: Optional[NavItem] = None
        self._narrow_mode: bool = False  # set correctly after first Resize
        # Thread to return to when 'left' is pressed after opening a member article.
        self._member_origin_thread_id: Optional[int] = None

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
        yield Footer()

    def on_mount(self) -> None:
        # Start with the nav sidebar (first panel) focused.
        self._pane_focus_idx = 0
        self.query_one("#nav-tree", Tree).focus()
        # Evaluate initial layout based on the actual terminal width at startup.
        self._apply_layout(self.app.size.width)
        # Background sidebar refresh every 60s (mirrors the web UI's poll).
        self.set_interval(60.0, self._background_refresh)

    def _background_refresh(self) -> None:
        """Periodic refresh of the nav sidebar (sources/categories)."""
        try:
            self.query_one("#nav-sidebar", NavSidebar).reload()
        except Exception:
            pass

    def action_refresh(self) -> None:
        """Manual refresh (Shift-R): re-fetch the sidebar and reload the current list."""
        self.query_one("#nav-sidebar", NavSidebar).reload()
        article_list = self.query_one("#list-pane", ArticleList)
        article_list.run_worker(article_list.reload_current(), exclusive=True)
        self.notify_status("Refreshed")

    def on_resize(self, event: Resize) -> None:
        """Switch between narrow (single-pane) and wide (three-pane) layout."""
        self._apply_layout(event.size.width)

    def _apply_layout(self, columns: int) -> None:
        """Show/hide panes according to whether we are in narrow or wide mode."""
        narrow = columns < NARROW_THRESHOLD
        if narrow == self._narrow_mode:
            return
        self._narrow_mode = narrow
        if narrow:
            # Default visible pane in narrow mode is the list (idx 1).
            self._pane_focus_idx = 1
            self._show_narrow_pane(1)
        else:
            # Wide mode: all three panes visible simultaneously.
            for widget_id in ("#nav-sidebar", "#list-pane", "#reader-pane"):
                self.query_one(widget_id).display = True
            self._apply_pane_focus()

    def _show_narrow_pane(self, idx: int) -> None:
        """In narrow mode, show only the pane at *idx* (0=nav, 1=list, 2=reader)."""
        ids = ("#nav-sidebar", "#list-pane", "#reader-pane")
        for i, widget_id in enumerate(ids):
            self.query_one(widget_id).display = i == idx
        self._pane_focus_idx = idx
        self._apply_pane_focus()

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
        self._member_origin_thread_id = None  # opening from the list resets the back target
        if self._selected_article is not None:
            self.query_one("#reader-pane", ReaderPane).load_article(self._selected_article.id)
        elif self._selected_thread is not None:
            self.query_one("#reader-pane", ReaderPane).load_thread(self._selected_thread.id)
        # In narrow mode, switch focus to the reader pane after loading content.
        if self._narrow_mode:
            self._show_narrow_pane(2)

    def action_open_member_article(self, article_id: int) -> None:
        """Open a thread member's article in the reader (clickable member link)."""
        # Remember the thread so 'left' can return to it.
        if self._selected_thread is not None:
            self._member_origin_thread_id = self._selected_thread.id
        self.query_one("#reader-pane", ReaderPane).load_article(article_id)
        if self._narrow_mode:
            self._show_narrow_pane(2)

    def action_back_to_thread(self) -> None:
        """Left arrow: return from a member-opened article to its thread."""
        if self._member_origin_thread_id is None:
            return
        thread_id = self._member_origin_thread_id
        self._member_origin_thread_id = None
        self.query_one("#reader-pane", ReaderPane).load_thread(thread_id)
        if self._narrow_mode:
            self._show_narrow_pane(2)

    def action_view_in_browser(self) -> None:
        if self._selected_article is not None and self._selected_article.url:
            webbrowser.open(self._selected_article.url)
        else:
            self.notify_status("No URL available for this article.")

    def action_focus_next_pane(self) -> None:
        next_idx = (self._pane_focus_idx + 1) % 3
        if self._narrow_mode:
            self._show_narrow_pane(next_idx)
        else:
            self._pane_focus_idx = next_idx
            self._apply_pane_focus()

    def action_focus_list(self) -> None:
        if self._in_search_mode:
            self._in_search_mode = False
            article_list = self.query_one("#list-pane", ArticleList)
            article_list.deactivate_search()
            self._restore_last_nav()
            return
        if self._narrow_mode:
            self._show_narrow_pane(1)
        else:
            self._pane_focus_idx = 1
            self._apply_pane_focus()

    def action_activate_search(self) -> None:
        """Show and focus the search input (/ key)."""
        self._in_search_mode = True
        self.query_one("#list-pane", ArticleList).activate_search()

    def _restore_last_nav(self) -> None:
        """Re-load the view that was active before the user entered search mode."""
        item = self._last_nav_item
        article_list = self.query_one("#list-pane", ArticleList)
        if item is None:
            article_list.run_worker(article_list.load(view="all", title="All"), exclusive=True)
            return
        if item.kind == "smart":
            article_list.run_worker(article_list.load(view=item.view or "all", title=item.label), exclusive=True)
        elif item.kind == "today":
            article_list.run_worker(article_list.load(view="today", title=item.label), exclusive=True)
        elif item.kind == "threads":
            article_list.run_worker(article_list.load_threads(title=item.label), exclusive=True)
        elif item.kind == "category":
            article_list.run_worker(article_list.load(category=item.category, title=item.label), exclusive=True)
        elif item.kind == "source":
            article_list.run_worker(article_list.load(source_id=item.source_id, title=item.label), exclusive=True)

    def _apply_pane_focus(self) -> None:
        try:
            if self._pane_focus_idx == 0:
                self.query_one("#nav-tree", Tree).focus()
            elif self._pane_focus_idx == 1:
                self.query_one("#article-listview", ListView).focus()
            else:
                # Reader pane: focus the member list when a thread with members
                # is shown (so ↑/↓/Enter navigate the links), else the scroll.
                members = self.query_one("#reader-members", OptionList)
                if members.option_count > 0:
                    members.focus()
                else:
                    self.query_one("#reader-scroll", VerticalScroll).focus()
        except Exception:
            pass

    async def action_toggle_read(self) -> None:
        row = self._selected_article_row
        if row is None:
            return
        article = row.article
        old_read = article.is_read
        article.is_read = not old_read
        row.refresh_display()
        try:
            if article.is_read:
                await self.api_client.mark_read(article.id)
            else:
                await self.api_client.mark_unread(article.id)
        except ApiError as exc:
            article.is_read = old_read
            row.refresh_display()
            self.notify_status(f"Error: {exc}")

    async def action_mark_read_next(self) -> None:
        row = self._selected_article_row
        if row is None:
            return
        article = row.article
        if not article.is_read:
            old_read = article.is_read
            article.is_read = True
            row.refresh_display()
            try:
                await self.api_client.mark_read(article.id)
            except ApiError as exc:
                article.is_read = old_read
                row.refresh_display()
                self.notify_status(f"Error: {exc}")
                return
        # Advance to the next item and load it into the reader so the content
        # follows (mark-read-and-read-next flow).
        listview = self.query_one("#article-listview", ListView)
        listview.action_cursor_down()
        next_item = listview.highlighted_child
        if isinstance(next_item, ArticleRow):
            self._selected_article = next_item.article
            self._selected_article_row = next_item
            self.query_one("#reader-pane", ReaderPane).load_article(next_item.article.id)

    async def action_toggle_save(self) -> None:
        row = self._selected_article_row
        if row is None:
            return
        article = row.article
        old_saved = article.is_saved
        article.is_saved = not old_saved
        row.refresh_display()
        try:
            if article.is_saved:
                await self.api_client.save_article(article.id)
            else:
                await self.api_client.unsave_article(article.id)
        except ApiError as exc:
            article.is_saved = old_saved
            row.refresh_display()
            self.notify_status(f"Error: {exc}")

    async def action_dismiss_thread(self) -> None:
        if self._current_nav_kind != "threads":
            return
        row = self._selected_thread_row
        if row is None:
            return
        thread = row.thread
        old_dismissed = thread.dismissed
        thread.dismissed = not old_dismissed
        row.refresh_display()
        try:
            if thread.dismissed:
                await self.api_client.dismiss_thread(thread.id)
            else:
                await self.api_client.restore_thread(thread.id)
        except ApiError as exc:
            thread.dismissed = old_dismissed
            row.refresh_display()
            self.notify_status(f"Error: {exc}")

    async def action_toggle_filter(self) -> None:
        """Toggle the Show-all / Unread read filter on the current article view."""
        await self.query_one("#list-pane", ArticleList).toggle_unread_filter()

    async def action_toggle_sort(self) -> None:
        """Toggle Importance / Recent sort on the current thread view."""
        await self.query_one("#list-pane", ArticleList).toggle_sort()

    def action_show_help(self) -> None:
        """Push the keybinding help overlay (? key)."""
        self.push_screen(HelpOverlay())

    def action_quit_app(self) -> None:
        """Quit unless a text input widget currently has focus."""
        if not isinstance(self.focused, Input):
            self.exit()

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Track the highlighted row so action keys can act on it without Enter."""
        if event.item is not None and isinstance(event.item, ArticleRow):
            self._selected_article = event.item.article
            self._selected_article_row = event.item
            self._selected_thread = None
            self._selected_thread_row = None
        elif event.item is not None and isinstance(event.item, ThreadRow):
            self._selected_thread = event.item.thread
            self._selected_thread_row = event.item
            self._selected_article = None
            self._selected_article_row = None

    def on_article_list_article_selected(self, event: ArticleList.ArticleSelected) -> None:
        """Load the selected article into the reader pane (Enter)."""
        self._selected_article = event.article
        self._member_origin_thread_id = None  # opened from the list, not a thread
        self.query_one("#reader-pane", ReaderPane).load_article(event.article.id)
        if self._narrow_mode:
            self._show_narrow_pane(2)

    def on_article_list_thread_selected(self, event: ArticleList.ThreadSelected) -> None:
        """Load the selected thread into the reader pane (Enter)."""
        self._selected_thread = event.thread
        self._member_origin_thread_id = None  # now viewing the thread itself
        self.query_one("#reader-pane", ReaderPane).load_thread(event.thread.id)
        if self._narrow_mode:
            self._show_narrow_pane(2)

    def on_article_list_search_failed(self, event: ArticleList.SearchFailed) -> None:
        """Route a search ApiError to the status bar."""
        self.notify_status(f"Search error: {event.error}")

    def on_nav_sidebar_nav_item_selected(self, event: NavSidebar.NavItemSelected) -> None:
        """Reload the article list based on the selected nav item."""
        item = event.item
        self._last_nav_item = item
        self._current_nav_kind = item.kind
        if self._in_search_mode:
            self._in_search_mode = False
            self.query_one("#list-pane", ArticleList).deactivate_search()
        self._selected_article = None
        self._selected_article_row = None
        self._selected_thread = None
        self._selected_thread_row = None
        self._member_origin_thread_id = None
        article_list = self.query_one("#list-pane", ArticleList)
        if item.kind == "smart":
            article_list.run_worker(article_list.load(view=item.view or "all", title=item.label), exclusive=True)
        elif item.kind == "today":
            article_list.run_worker(article_list.load(view="today", title=item.label), exclusive=True)
        elif item.kind == "threads":
            article_list.run_worker(article_list.load_threads(title=item.label), exclusive=True)
        elif item.kind == "category":
            article_list.run_worker(article_list.load(category=item.category, title=item.label), exclusive=True)
        elif item.kind == "source":
            article_list.run_worker(article_list.load(source_id=item.source_id, title=item.label), exclusive=True)
