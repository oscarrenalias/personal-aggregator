from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Label, Static

_KEYBINDINGS: list[tuple[str, str]] = [
    ("j / k", "Move down / up in list"),
    ("g / G", "Jump to top / bottom"),
    ("Tab", "Focus next pane"),
    ("Enter", "Open selected item in reader"),
    ("o", "Open in reader pane"),
    ("v", "View in browser"),
    ("m", "Toggle read / unread"),
    ("n", "Mark read and move to next"),
    ("s", "Toggle saved"),
    ("d", "Dismiss / restore thread"),
    ("/", "Search articles"),
    ("?", "Show this help"),
    ("Escape", "Close overlay / back to list"),
    ("q", "Quit application"),
]


class HelpOverlay(ModalScreen[None]):
    """Modal keybinding reference overlay. Dismiss with Escape or q."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("q", "dismiss", "Close", show=False),
    ]

    DEFAULT_CSS = """
    HelpOverlay {
        align: center middle;
    }
    HelpOverlay #help-dialog {
        width: 60;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }
    HelpOverlay #help-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Label(
                "Keybindings  [dim](Esc or q to close)[/dim]",
                markup=True,
                id="help-title",
            )
            yield Static(self._render_table(), markup=True)

    @staticmethod
    def _render_table() -> str:
        lines = []
        for key, desc in _KEYBINDINGS:
            lines.append(f"  [bold cyan]{key:<14}[/bold cyan]  {desc}")
        return "\n".join(lines)
