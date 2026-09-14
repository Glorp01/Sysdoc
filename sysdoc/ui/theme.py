"""Colors, symbols, and the banner shared by Sysdoc's terminal interface."""
from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

# Mid-tone colors stay readable on both dark and light terminal backgrounds.
ACCENT = "#0ea5e9"
VIOLET = "#8b5cf6"
SUCCESS = "#10b981"
WARNING = "#f59e0b"
DANGER = "#ef4444"
MUTED = "#64748b"

THEME = Theme({
    "accent": f"bold {ACCENT}",
    "violet": VIOLET,
    "ok": SUCCESS,
    "warn": WARNING,
    "err": DANGER,
    "muted": MUTED,
    "markdown.code": f"bold {VIOLET}",
    "markdown.link": ACCENT,
    "markdown.link_url": MUTED,
})

BADGES = {
    "low": ("LOW RISK", f"bold black on {SUCCESS}"),
    "medium": ("MEDIUM RISK", f"bold black on {WARNING}"),
    "high": ("HIGH RISK", f"bold white on {DANGER}"),
    "admin": ("ADMIN", f"bold white on {VIOLET}"),
    "manual": ("YOU DO THIS", f"bold black on #94a3b8"),
    "ok": ("OK", f"bold black on {SUCCESS}"),
    "info": ("INFO", f"bold black on {ACCENT}"),
    "warning": ("WARN", f"bold black on {WARNING}"),
    "critical": ("CRIT", f"bold white on {DANGER}"),
}


@dataclass(frozen=True)
class Symbols:
    prompt: str
    bullet: str
    assistant: str
    branch: str
    gutter: str
    ok: str
    fail: str
    step: str
    dot: str


FANCY = Symbols(prompt="❯", bullet="●", assistant="◆", branch="└", gutter="│", ok="✓", fail="✗", step="▶", dot="·")
PLAIN = Symbols(prompt=">", bullet="*", assistant="*", branch="`-", gutter="|", ok="OK", fail="X", step=">", dot="-")


def make_console() -> Console:
    return Console(theme=THEME, highlight=False)


def symbols_for(console: Console) -> Symbols:
    encoding = (console.encoding or "").lower()
    return FANCY if not console.legacy_windows and "utf" in encoding else PLAIN


def badge(kind: str) -> Text:
    label, style = BADGES[kind]
    return Text(f" {label} ", style=style)


_LETTERS = {
    "S": ("███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"),
    "Y": ("██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "),
    "D": ("██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "),
    "O": (" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "),
    "C": (" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"),
}
_GRADIENT = ((56, 189, 248), (139, 92, 246), (236, 72, 153))
BANNER_WIDTH = sum(len(_LETTERS[letter][0]) for letter in "SYSDOC")


def _blend(position: float, brightness: float = 1.0) -> str:
    scaled = position * (len(_GRADIENT) - 1)
    index = min(int(scaled), len(_GRADIENT) - 2)
    fraction = scaled - index
    start, end = _GRADIENT[index], _GRADIENT[index + 1]
    red, green, blue = (round((a + (b - a) * fraction) * brightness) for a, b in zip(start, end))
    return f"#{red:02x}{green:02x}{blue:02x}"


def banner() -> Text:
    """SYSDOC in block letters with a left-to-right gradient; the outline is drawn darker, like a shadow."""
    text = Text()
    for row in range(6):
        line = "".join(_LETTERS[letter][row] for letter in "SYSDOC")
        for column, char in enumerate(line):
            if char == " ":
                text.append(char)
            else:
                text.append(char, style=_blend(column / (BANNER_WIDTH - 1), 1.0 if char == "█" else 0.55))
        if row < 5:
            text.append("\n")
    return text
