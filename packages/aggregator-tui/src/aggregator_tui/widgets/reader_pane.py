from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from ..api_client import ApiError, ArticleResponse, ThreadMemberResponse, ThreadResponse

_PLACEHOLDER = "[dim]Select an article or thread to read.[/dim]"


class ReaderPane(Widget):
    """Scrollable reading pane — renders an article or a thread summary + member list.

    Call load_article(id) or load_thread(id) to display content.
    ApiErrors are forwarded to the app's notify_status() rather than crashing.
    """

    DEFAULT_CSS = """
    ReaderPane {
        height: 100%;
    }
    #reader-scroll {
        height: 100%;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="reader-scroll"):
            yield Static(_PLACEHOLDER, id="reader-content")

    def clear(self) -> None:
        """Reset to placeholder text."""
        self.query_one("#reader-content", Static).update(_PLACEHOLDER)
        self.query_one("#reader-scroll", VerticalScroll).scroll_home(animate=False)

    def load_article(self, article_id: int) -> None:
        """Fetch and display an article asynchronously."""
        self.run_worker(self._fetch_article(article_id), exclusive=True)

    def load_thread(self, thread_id: int) -> None:
        """Fetch and display a thread (summary + member list) asynchronously."""
        self.run_worker(self._fetch_thread(thread_id), exclusive=True)

    async def _fetch_article(self, article_id: int) -> None:
        api = self.app.api_client  # type: ignore[attr-defined]
        try:
            article = await api.get_article(article_id)
        except ApiError as exc:
            self.app.notify_status(str(exc))  # type: ignore[attr-defined]
            return
        self._render_article(article)

    async def _fetch_thread(self, thread_id: int) -> None:
        api = self.app.api_client  # type: ignore[attr-defined]
        try:
            thread = await api.get_thread(thread_id)
            members_page = await api.get_thread_members(thread_id)
        except ApiError as exc:
            self.app.notify_status(str(exc))  # type: ignore[attr-defined]
            return
        self._render_thread(thread, members_page.items)

    def _render_article(self, article: ArticleResponse) -> None:
        lines: list[str] = []

        title = escape(article.title or "(No title)")
        lines.append(f"[bold]{title}[/bold]")

        meta: list[str] = []
        if article.source_name:
            meta.append(escape(article.source_name))
        if article.feed_published_at:
            meta.append(escape(article.feed_published_at[:10]))
        if article.author:
            meta.append(f"by {escape(article.author)}")
        if meta:
            lines.append(f"[dim]{' · '.join(meta)}[/dim]")

        if article.importance_score is not None:
            reason = (
                f" — {escape(article.importance_reason)}"
                if article.importance_reason
                else ""
            )
            lines.append(f"[dim]Importance: {article.importance_score}{reason}[/dim]")

        if article.summary:
            lines.append("")
            lines.append("[bold underline]Summary[/bold underline]")
            lines.append(escape(article.summary))

        if article.topics:
            lines.append("")
            lines.append("[bold underline]Topics[/bold underline]")
            if isinstance(article.topics, dict):
                topics_str = escape(", ".join(str(k) for k in article.topics.keys()))
            else:
                topics_str = escape(str(article.topics))
            lines.append(f"[dim]{topics_str}[/dim]")

        body = article.clean_text or article.excerpt
        if body:
            lines.append("")
            lines.append("[bold underline]Article[/bold underline]")
            lines.append(escape(body))

        self.query_one("#reader-content", Static).update("\n".join(lines))
        self.query_one("#reader-scroll", VerticalScroll).scroll_home(animate=False)

    def _render_thread(
        self, thread: ThreadResponse, members: list[ThreadMemberResponse]
    ) -> None:
        lines: list[str] = []

        title = escape(thread.representative_title)
        lines.append(f"[bold]{title}[/bold]")

        count_meta = [
            f"{thread.member_count} article{'s' if thread.member_count != 1 else ''}",
            f"{thread.source_count} source{'s' if thread.source_count != 1 else ''}",
        ]
        if thread.tier:
            count_meta.append(escape(thread.tier))
        lines.append(f"[dim]{' · '.join(count_meta)}[/dim]")

        if thread.rolling_summary:
            lines.append("")
            lines.append("[bold underline]Summary[/bold underline]")
            lines.append(escape(thread.rolling_summary))

        if thread.known_facts:
            lines.append("")
            lines.append("[bold underline]Key Facts[/bold underline]")
            for fact in thread.known_facts:
                lines.append(f"  • {escape(str(fact))}")

        visible_members = [m for m in members if not m.suppressed]
        if visible_members:
            lines.append("")
            lines.append("[bold underline]Articles[/bold underline]")
            for m in visible_members:
                member_title = escape(m.clean_title or "(No title)")
                member_meta: list[str] = []
                if m.source_name:
                    member_meta.append(escape(m.source_name))
                if m.published_at:
                    member_meta.append(escape(m.published_at[:10]))
                meta_suffix = (
                    f"  [dim]({', '.join(member_meta)})[/dim]" if member_meta else ""
                )
                lines.append(f"  • {member_title}{meta_suffix}")

        self.query_one("#reader-content", Static).update("\n".join(lines))
        self.query_one("#reader-scroll", VerticalScroll).scroll_home(animate=False)
