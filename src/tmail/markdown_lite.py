"""Zero-dependency lightweight Markdown-to-HTML converter for terminal emails."""

from __future__ import annotations

import html
import re
from typing import List


def _render_inline(text: str) -> str:
    """Convert inline markdown (code, bold, italic, links) safely after HTML escaping."""
    escaped = html.escape(text)

    # Inline code first using placeholders so formatting inside backticks is untouched
    code_spans: List[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        idx = len(code_spans)
        code_spans.append(
            f'<code style="background:#f4f4f4;padding:1px 4px;border-radius:3px;'
            f'font-family:monospace;">{match.group(1)}</code>'
        )
        return f"\x00CODE{idx}\x00"

    escaped = re.sub(r"`([^`]+)`", _stash_code, escaped)

    # Links: [label](url) - allow http/https/mailto only
    escaped = re.sub(
        r"\[([^\]]+)\]\(((?:https?|mailto):[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        escaped,
    )

    # Bold (**text** or __text__)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)

    # Italic (*text* or _text_)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)

    for idx, snippet in enumerate(code_spans):
        escaped = escaped.replace(f"\x00CODE{idx}\x00", snippet)

    return escaped


def markdown_to_html(md_text: str) -> str:
    """Convert basic Markdown into clean, email-client-friendly HTML."""
    lines = md_text.splitlines()
    out: List[str] = []
    in_code = False
    code_lines: List[str] = []
    list_mode: str = ""  # "" | "ul" | "ol"
    paragraph: List[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            joined = "<br>\n".join(_render_inline(line) for line in paragraph)
            out.append(f"<p>{joined}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_mode
        if list_mode:
            out.append(f"</{list_mode}>")
            list_mode = ""

    for line in lines:
        stripped = line.strip()

        # Fenced code blocks
        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            if not in_code:
                in_code = True
                code_lines = []
            else:
                in_code = False
                body = html.escape("\n".join(code_lines))
                out.append(
                    '<pre style="background:#f4f4f4;padding:10px;border-radius:4px;'
                    f'overflow-x:auto;font-family:monospace;"><code>{body}</code></pre>'
                )
            continue

        if in_code:
            code_lines.append(line)
            continue

        # Blank line
        if not stripped:
            flush_paragraph()
            close_list()
            continue

        # Horizontal rule
        if re.match(r"^([-*_])\1{2,}$", stripped):
            flush_paragraph()
            close_list()
            out.append("<hr>")
            continue

        # Headings (# .. ######)
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            flush_paragraph()
            close_list()
            level = len(heading_match.group(1))
            out.append(f"<h{level}>{_render_inline(heading_match.group(2))}</h{level}>")
            continue

        # Blockquote
        if stripped.startswith("> "):
            flush_paragraph()
            close_list()
            out.append(
                '<blockquote style="border-left:3px solid #ccc;margin:8px 0;'
                f'padding-left:10px;color:#555;">{_render_inline(stripped[2:])}</blockquote>'
            )
            continue

        # Unordered list
        ul_match = re.match(r"^[-*+]\s+(.+)$", stripped)
        if ul_match:
            flush_paragraph()
            if list_mode != "ul":
                close_list()
                out.append("<ul>")
                list_mode = "ul"
            out.append(f"<li>{_render_inline(ul_match.group(1))}</li>")
            continue

        # Ordered list
        ol_match = re.match(r"^\d+\.\s+(.+)$", stripped)
        if ol_match:
            flush_paragraph()
            if list_mode != "ol":
                close_list()
                out.append("<ol>")
                list_mode = "ol"
            out.append(f"<li>{_render_inline(ol_match.group(1))}</li>")
            continue

        close_list()
        paragraph.append(line)

    flush_paragraph()
    close_list()
    if in_code and code_lines:
        body = html.escape("\n".join(code_lines))
        out.append(f"<pre><code>{body}</code></pre>")

    inner = "\n".join(out)
    return (
        '<!DOCTYPE html><html><body style="font-family:-apple-system,BlinkMacSystemFont,'
        'Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.5;color:#222;">\n'
        f"{inner}\n"
        "</body></html>"
    )


def html_to_plain(html_text: str) -> str:
    """Create a readable plain-text fallback from simple HTML."""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html_text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
