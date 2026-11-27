"""Interactive terminal compose and $EDITOR integration for t-mail."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from tmail.config import split_command


@dataclass
class ComposedEmail:
    to: List[str] = field(default_factory=list)
    cc: List[str] = field(default_factory=list)
    bcc: List[str] = field(default_factory=list)
    subject: str = ""
    attachments: List[str] = field(default_factory=list)
    body: str = ""


def find_editor() -> List[str]:
    """Detect the user's preferred text editor (compatible with Termux, Linux, macOS, Windows)."""
    for env_var in ("TMAIL_EDITOR", "VISUAL", "EDITOR"):
        val = os.environ.get(env_var)
        if val:
            return split_command(val)

    candidates = ["nano", "vim", "vi", "micro", "code --wait"]
    if os.name == "nt":
        candidates.extend(["notepad"])
    for cand in candidates:
        parts = cand.split()
        if shutil.which(parts[0]):
            return parts

    raise RuntimeError("No text editor found. Set the EDITOR environment variable (e.g. export EDITOR=nano).")


def open_text_in_editor(initial_text: str, suffix: str = ".txt") -> str:
    """Open temporary file in the user's editor and return the edited text."""
    editor_cmd = find_editor()
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=suffix,
        prefix="tmail-",
        delete=False,
    ) as tmp:
        tmp.write(initial_text)
        tmp_path = Path(tmp.name)

    try:
        subprocess.run([*editor_cmd, str(tmp_path)], check=True)
        return tmp_path.read_text(encoding="utf-8")
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def format_editor_buffer(
    *,
    to: Sequence[str] = (),
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    subject: str = "",
    attachments: Sequence[str] = (),
    body: str = "",
) -> str:
    """Format an editable RFC822-style buffer for composing an email."""
    lines = [
        f"To: {', '.join(to)}",
        f"Cc: {', '.join(cc)}",
        f"Bcc: {', '.join(bcc)}",
        f"Subject: {subject}",
        f"Attach: {', '.join(attachments)}",
        "",
        body if body else "",
    ]
    return "\n".join(lines)


def parse_editor_buffer(text: str) -> ComposedEmail:
    """Parse an RFC822-style compose buffer back into structured email fields."""
    lines = text.splitlines()
    headers = {"to": [], "cc": [], "bcc": [], "subject": "", "attach": []}
    body_start = 0

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "":
            body_start = idx + 1
            break
        if stripped.startswith("#"):
            body_start = idx + 1
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            k = key.strip().lower()
            v = val.strip()
            if k in ("to", "cc", "bcc", "attach"):
                if v:
                    headers[k].extend(part.strip() for part in v.split(",") if part.strip())
                body_start = idx + 1
                continue
            if k == "subject":
                headers["subject"] = v
                body_start = idx + 1
                continue
        # First non-header line begins body
        body_start = idx
        break

    body = "\n".join(lines[body_start:]).lstrip("\n")
    return ComposedEmail(
        to=headers["to"],
        cc=headers["cc"],
        bcc=headers["bcc"],
        subject=headers["subject"],
        attachments=headers["attach"],
        body=body,
    )


def compose_in_editor(
    *,
    to: Sequence[str] = (),
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    subject: str = "",
    attachments: Sequence[str] = (),
    body: str = "",
) -> ComposedEmail:
    """Launch $EDITOR prefilled with headers and body, then parse the result."""
    initial = (
        "# Edit headers above the blank line and write your email body below.\n"
        "# Separate multiple addresses or attachment paths with commas.\n"
        + format_editor_buffer(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            attachments=attachments,
            body=body,
        )
    )
    edited = open_text_in_editor(initial, suffix=".eml")
    return parse_editor_buffer(edited)


def compose_interactive(
    *,
    to: Sequence[str] = (),
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    subject: str = "",
    attachments: Sequence[str] = (),
    body: str = "",
) -> ComposedEmail:
    """Simple prompt-based terminal compose flow (great for quick Termux messages)."""
    def prompt_list(label: str, current: Sequence[str], required: bool = False) -> List[str]:
        if current:
            return list(current)
        while True:
            raw = input(f"{label}: ").strip()
            if raw:
                return [p.strip() for p in raw.split(",") if p.strip()]
            if not required:
                return []
            print(f"{label} is required.")

    final_to = prompt_list("To (email or alias, comma-separated)", to, required=True)
    final_cc = prompt_list("Cc (optional)", cc)
    final_bcc = prompt_list("Bcc (optional)", bcc)
    final_subject = subject or input("Subject: ").strip()
    final_attachments = prompt_list("Attachments (file paths, comma-separated, optional)", attachments)

    if body:
        final_body = body
    else:
        print("Body (end with a single '.' on its own line, or Ctrl+D):")
        body_lines: List[str] = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line == ".":
                break
            body_lines.append(line)
        final_body = "\n".join(body_lines)

    return ComposedEmail(
        to=final_to,
        cc=final_cc,
        bcc=final_bcc,
        subject=final_subject,
        attachments=final_attachments,
        body=final_body,
    )
