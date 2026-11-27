"""Contacts and group alias book for t-mail."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from tmail.config import ensure_private_file, get_config_dir

EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


@dataclass
class Contact:
    alias: str
    emails: List[str]
    name: str = ""

    def formatted_addresses(self) -> List[str]:
        if len(self.emails) == 1 and self.name:
            return [f"{self.name} <{self.emails[0]}>"]
        return list(self.emails)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "Contact":
        raw_emails = data.get("emails") or []
        if isinstance(raw_emails, str):
            raw_emails = [raw_emails]
        return cls(
            alias=str(data.get("alias", "")),
            emails=[str(e) for e in raw_emails],
            name=str(data.get("name", "")),
        )


def is_valid_email(value: str) -> bool:
    """Basic validation for plain email or 'Display Name <addr@host>' format."""
    text = value.strip()
    if "<" in text and text.endswith(">"):
        inner = text.rsplit("<", 1)[-1][:-1].strip()
        return bool(EMAIL_RE.match(inner))
    return bool(EMAIL_RE.match(text))


class ContactStore:
    """Stores contact aliases and recipient groups in contacts.json."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = (config_dir or get_config_dir()).expanduser()
        self.path = self.config_dir / "contacts.json"

    def _load(self) -> Dict[str, Dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: Dict[str, Dict[str, object]]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        ensure_private_file(self.path)

    def add(self, alias: str, emails: Sequence[str], name: str = "") -> Contact:
        key = alias.strip().lower()
        if not key:
            raise ValueError("Contact alias cannot be empty.")
        cleaned: List[str] = []
        for item in emails:
            for part in item.split(","):
                addr = part.strip()
                if not addr:
                    continue
                if not is_valid_email(addr):
                    raise ValueError(f"Invalid email address: '{addr}'")
                cleaned.append(addr)
        if not cleaned:
            raise ValueError("At least one valid email address is required.")

        contact = Contact(alias=key, emails=cleaned, name=name.strip())
        data = self._load()
        data[key] = contact.to_dict()
        self._save(data)
        return contact

    def get(self, alias: str) -> Optional[Contact]:
        data = self._load()
        item = data.get(alias.strip().lower())
        if not item:
            return None
        entry = dict(item)
        entry.setdefault("alias", alias.strip().lower())
        return Contact.from_dict(entry)

    def list_all(self) -> List[Contact]:
        data = self._load()
        contacts: List[Contact] = []
        for key in sorted(data.keys()):
            entry = dict(data[key])
            entry.setdefault("alias", key)
            contacts.append(Contact.from_dict(entry))
        return contacts

    def search(self, query: str) -> List[Contact]:
        q = query.strip().lower()
        results: List[Contact] = []
        for contact in self.list_all():
            haystack = " ".join([contact.alias, contact.name, *contact.emails]).lower()
            if q in haystack:
                results.append(contact)
        return results

    def remove(self, alias: str) -> bool:
        key = alias.strip().lower()
        data = self._load()
        if key not in data:
            return False
        del data[key]
        self._save(data)
        return True

    def resolve_recipients(self, inputs: Sequence[str]) -> List[str]:
        """Expand aliases/groups and comma-separated recipient strings into email addresses."""
        resolved: List[str] = []
        seen = set()
        for raw in inputs:
            for token in raw.split(","):
                item = token.strip()
                if not item:
                    continue
                contact = self.get(item)
                if contact is not None:
                    expanded = contact.formatted_addresses()
                elif is_valid_email(item):
                    expanded = [item]
                else:
                    raise ValueError(
                        f"Unknown contact alias or invalid email: '{item}'. "
                        f"Add it with `tmail contact add {item} user@example.com`."
                    )
                for addr in expanded:
                    norm = addr.lower()
                    if norm not in seen:
                        seen.add(norm)
                        resolved.append(addr)
        return resolved
