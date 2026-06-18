from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Static, Tree
from textual.widgets._tree import TreeNode

from ..api_client import ApiClient, ApiError


@dataclass
class NavItem:
    """Payload carried by a NavItemSelected message."""

    kind: str  # "smart" | "today" | "threads" | "category" | "source"
    view: str | None = None  # for kind="smart"
    category: str | None = None  # for kind="category"
    source_id: int | None = None  # for kind="source"
    label: str = ""


class NavSidebar(Static):
    """Left-hand navigation sidebar.

    Renders static nav sections (Today, Threads, Smart Views) plus
    dynamic Categories and Sources loaded from the API on mount.
    Posts NavItemSelected to the App when the user activates an item.
    """

    class NavItemSelected(Message):
        """Posted when the user selects a nav item."""

        def __init__(self, item: NavItem) -> None:
            super().__init__()
            self.item = item

    DEFAULT_CSS = """
    NavSidebar {
        height: 100%;
        overflow-y: auto;
    }
    NavSidebar Tree {
        height: 1fr;
        padding: 0;
    }
    """

    def __init__(self, api_client: ApiClient, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._api_client = api_client
        # Map tree-node id → NavItem for fast lookup on selection
        self._node_map: dict[int, NavItem] = {}

    def compose(self) -> ComposeResult:
        tree: Tree[None] = Tree("Navigation", id="nav-tree")
        tree.show_root = False
        yield tree

    def on_mount(self) -> None:
        tree = self.query_one("#nav-tree", Tree)
        self._build_static_sections(tree)
        # Load dynamic data asynchronously
        self.run_worker(self._load_dynamic(), exclusive=False)

    # ------------------------------------------------------------------
    # Static nav sections
    # ------------------------------------------------------------------

    def _build_static_sections(self, tree: Tree[None]) -> None:
        """Add Today, Threads, and Smart Views nodes synchronously."""
        # Today
        today_node = tree.root.add("Today", expand=True)
        self._register(today_node, NavItem(kind="today", label="Today"))

        # Threads
        threads_node = tree.root.add("Threads", expand=True)
        self._register(threads_node, NavItem(kind="threads", label="Threads"))

        # Smart Views section
        smart_section = tree.root.add("Smart Views", expand=True)
        smart_section.allow_expand = False

        for view, label in [
            ("all", "All"),
            ("unread", "Unread"),
            ("saved", "Saved"),
            ("important", "Important"),
            ("uncategorized", "Uncategorized"),
        ]:
            child = smart_section.add_leaf(label)
            self._register(child, NavItem(kind="smart", view=view, label=label))

        # Placeholder sections until API data arrives
        self._categories_section = tree.root.add("Categories", expand=True)
        self._categories_section.allow_expand = False
        self._categories_section.add_leaf("Loading…")

        self._sources_section = tree.root.add("Sources", expand=True)
        self._sources_section.allow_expand = False
        self._sources_section.add_leaf("Loading…")

    def _register(self, node: TreeNode[None], item: NavItem) -> None:
        self._node_map[node.id] = item

    # ------------------------------------------------------------------
    # Dynamic data loading
    # ------------------------------------------------------------------

    async def _load_dynamic(self) -> None:
        """Fetch categories and sources from the API and populate the tree."""
        await self._load_categories()
        await self._load_sources()

    async def _load_categories(self) -> None:
        section = self._categories_section
        try:
            categories = await self._api_client.list_categories()
        except ApiError as exc:
            self._post_status(f"Failed to load categories: {exc}")
            section.remove_children()
            section.add_leaf("(unavailable)")
            return

        section.remove_children()
        if not categories:
            section.add_leaf("(none)")
            return
        for cat in sorted(categories, key=lambda c: (c.sort_order, c.name)):
            child = section.add_leaf(cat.name)
            self._register(child, NavItem(kind="category", category=cat.name, label=cat.name))

    async def _load_sources(self) -> None:
        section = self._sources_section
        try:
            sources = await self._api_client.list_sources()
        except ApiError as exc:
            self._post_status(f"Failed to load sources: {exc}")
            section.remove_children()
            section.add_leaf("(unavailable)")
            return

        section.remove_children()
        if not sources:
            section.add_leaf("(none)")
            return
        for src in sorted(sources, key=lambda s: s.name.lower()):
            child = section.add_leaf(src.name)
            self._register(child, NavItem(kind="source", source_id=src.id, label=src.name))

    def _post_status(self, message: str) -> None:
        """Route an error message to the App's status bar."""
        # The App listens for NavItemSelected; for error routing we call
        # app.notify_status if it exists, otherwise fall back to logging.
        app = self.app
        if hasattr(app, "notify_status"):
            app.notify_status(message)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_tree_node_selected(self, event: Tree.NodeSelected[None]) -> None:
        """Translate a tree selection into a NavItemSelected message."""
        event.stop()
        node_id = event.node.id
        item = self._node_map.get(node_id)
        if item is not None:
            self.post_message(self.NavItemSelected(item))
