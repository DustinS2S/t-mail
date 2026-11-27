"""Reusable email templates with {{variable}} substitution."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from tmail.config import ensure_private_file, get_config_dir

VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*\}\}")


@dataclass
class MailTemplate:
    name: str
    subject: str
    body: str
    content_type: str = "plain"  # "plain" | "markdown" | "html"

    def variables(self) -> Set[str]:
        found = set(VAR_PATTERN.findall(self.subject))
        found.update(VAR_PATTERN.findall(self.body))
        return found

    def render(self, variables: Dict[str, str], strict: bool = True) -> Tuple[str, str]:
        missing = sorted(v for v in self.variables() if v not in variables)
        if missing and strict:
            joined = ", ".join(missing)
            raise ValueError(
                f"Template '{self.name}' is missing required variable(s): {joined}. "
                f"Pass them via --var key=value."
            )

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            return variables.get(key, match.group(0))

        return VAR_PATTERN.sub(repl, self.subject), VAR_PATTERN.sub(repl, self.body)

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "MailTemplate":
        return cls(
            name=str(data.get("name", "")),
            subject=str(data.get("subject", "")),
            body=str(data.get("body", "")),
            content_type=str(data.get("content_type", "plain")),
        )


def parse_var_pairs(pairs: List[str]) -> Dict[str, str]:
    """Parse ['key=value', ...] arguments into a dictionary."""
    result: Dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid --var '{pair}'. Expected format: key=value")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Empty variable name in --var '{pair}'")
        result[key] = value
    return result


class TemplateStore:
    """Stores templates in templates.json inside the config directory."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = (config_dir or get_config_dir()).expanduser()
        self.path = self.config_dir / "templates.json"

    def _load(self) -> Dict[str, Dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: Dict[str, Dict[str, str]]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        ensure_private_file(self.path)

    def add(self, name: str, subject: str, body: str, content_type: str = "plain") -> MailTemplate:
        key = name.strip().lower()
        if not key:
            raise ValueError("Template name cannot be empty.")
        tpl = MailTemplate(name=key, subject=subject, body=body, content_type=content_type)
        data = self._load()
        data[key] = tpl.to_dict()
        self._save(data)
        return tpl

    def get(self, name: str) -> Optional[MailTemplate]:
        key = name.strip().lower()
        data = self._load()
        item = data.get(key)
        if not item:
            return None
        entry = dict(item)
        entry.setdefault("name", key)
        return MailTemplate.from_dict(entry)

    def list_all(self) -> List[MailTemplate]:
        data = self._load()
        result: List[MailTemplate] = []
        for key in sorted(data.keys()):
            entry = dict(data[key])
            entry.setdefault("name", key)
            result.append(MailTemplate.from_dict(entry))
        return result

    def remove(self, name: str) -> bool:
        key = name.strip().lower()
        data = self._load()
        if key not in data:
            return False
        del data[key]
        self._save(data)
        return True
