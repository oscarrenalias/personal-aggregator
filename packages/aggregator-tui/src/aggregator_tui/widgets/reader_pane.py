from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Markdown, Static

from ..api_client import ApiError, ArticleResponse, ThreadMemberResponse, ThreadResponse

_PLACEHOLDER = "[dim]Select an article or thread to read.[/dim]"


def _plaintext_to_markdown(text: str) -> str:
    """Turn the processor's plain-text body into readable Markdown.

    Each source line becomes its own block separated by a blank line, so the
    Markdown widget renders paragraphs with spacing and ``- ``/``* `` lines as
    proper lists — instead of one dense wall of text.
    """
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    blocks = [ln for ln in lines if ln]
    return "\n\n".join(blocks)


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
    #reader-body {
        margin-top: 1;
        padding: 0;
        background: transparent;
    }
    #reader-body.-empty {
        display: none;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="reader-scroll"):
            yield Static(_PLACEHOLDER, id="reader-content")
            yield Markdown("", id="reader-body")

    def clear(self) -> None:
        """Reset to placeholder text."""
        self.query_one("#reader-content", Static).update(_PLACEHOLDER)
        self._set_body("")
        self.query_one("#reader-scroll", VerticalScroll).scroll_home(animate=False)

    def _set_body(self, markdown: str) -> None:
        """Update the Markdown body widget, hiding it when empty."""
        body = self.query_one("#reader-body", Markdown)
        if markdown:
            body.remove_class("-empty")
            body.update(markdown)
        else:
            body.add_class("-empty")
            body.update("")

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
        # Tuck the importance score into the meta line; the verbose
        # importance_reason explanation is intentionally not shown.
        if article.importance_score is not None:
            meta.append(f"importance {article.importance_score}")
        if meta:
            lines.append(f"[dim]{' · '.join(meta)}[/dim]")

        if article.summary:
            lines.append("")
            lines.append("[bold underline]Summary[/bold underline]")
            lines.append(escape(article.summary))

        if article.topics:
            lines.append("")
            lines.append("[bold underline]Topics[/bold underline]")
            if isinstance(article.topics, dict):
                topic_items = [str(k) for k in article.topics.keys()]
            elif isinstance(article.topics, list):
                topic_items = [str(t) for t in article.topics]
            else:
                topic_items = [str(article.topics)]
            lines.append(f"[dim]{escape(', '.join(topic_items))}[/dim]")

        self.query_one("#reader-content", Static).update("\n".join(lines))

        # Render the article body as Markdown (paragraphs/lists) in its own widget.
        body = article.clean_text or article.excerpt
        self._set_body(_plaintext_to_markdown(body) if body else "")
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
            lines.append("[bold underline]Articles[/bold underline]  [dim](click a title to open)[/dim]")
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
                # Make each member title a clickable link that opens the article
                # in this reader pane (jump from a thread to one of its sources).
                link = f"[@click=app.open_member_article({m.article_id})][u]{member_title}[/u][/]"
                lines.append(f"  • {link}{meta_suffix}")

        self.query_one("#reader-content", Static).update("\n".join(lines))
        self._set_body("")  # threads have no markdown body
        self.query_one("#reader-scroll", VerticalScroll).scroll_home(animate=False)
