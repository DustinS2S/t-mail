"""Lightweight terminal formatting utilities with zero external dependencies."""

from __future__ import annotations

import os
import sys
from typing import Iterable, Sequence


def supports_color(stream=None) -> bool:
    """Return True if ANSI color codes should be emitted to stream."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR") in ("1", "true", "TRUE"):
        return True
    target = stream or sys.stdout
    return hasattr(target, "isatty") and target.isatty()


def _wrap(text: str, code: str, stream=None) -> str:
    if not supports_color(stream):
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text: str, stream=None) -> str:
    return _wrap(text, "1", stream)


def dim(text: str, stream=None) -> str:
    return _wrap(text, "2", stream)


def green(text: str, stream=None) -> str:
    return _wrap(text, "32", stream)


def yellow(text: str, stream=None) -> str:
    return _wrap(text, "33", stream)


def red(text: str, stream=None) -> str:
    return _wrap(text, "31", stream)


def cyan(text: str, stream=None) -> str:
    return _wrap(text, "36", stream)


def ok(msg: str) -> None:
    print(f"{green('[ok]')} {msg}")


def warn(msg: str) -> None:
    print(f"{yellow('[warn]')} {msg}", file=sys.stderr)


def err(msg: str) -> None:
    print(f"{red('[error]')} {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"{cyan('[info]')} {msg}")


def format_size(num_bytes: int) -> str:
    """Human-readable byte size."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def format_table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """Render a clean plain-text table suitable for narrow terminals and Termux."""
    row_list = [list(r) for r in rows]
    widths = [len(h) for h in headers]
    for row in row_list:
        for idx, cell in enumerate(row):
            if idx < len(widths):
                widths[idx] = max(widths[idx], len(cell))

    def fmt_row(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    lines = [
        bold(fmt_row(headers)),
        dim("  ".join("-" * w for w in widths)),
    ]
    for row in row_list:
        lines.append(fmt_row(row))
    return "\n".join(lines)
