"""Lightweight JSONL sent-message history log."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tmail.config import ensure_private_file, get_config_dir


@dataclass
class HistoryEntry:
    timestamp: str
    account: str
    from_addr: str
    to: List[str]
    subject: str
    status: str = "sent"  # "sent" | "failed"
    cc: List[str] = field(default_factory=list)
    bcc_count: int = 0
    attachments: List[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HistoryEntry":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


class HistoryStore:
    """Append-only JSONL log of sent messages (never stores credentials or full bodies)."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = (config_dir or get_config_dir()).expanduser()
        self.path = self.config_dir / "history.jsonl"

    def append(
        self,
        *,
        account: str,
        from_addr: str,
        to: List[str],
        subject: str,
        status: str = "sent",
        cc: Optional[List[str]] = None,
        bcc_count: int = 0,
        attachments: Optional[List[str]] = None,
        error: str = "",
    ) -> HistoryEntry:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        entry = HistoryEntry(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            account=account,
            from_addr=from_addr,
            to=list(to),
            cc=list(cc or []),
            bcc_count=bcc_count,
            subject=subject,
            status=status,
            attachments=[Path(a).name for a in (attachments or [])],
            error=error,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict()) + "\n")
        ensure_private_file(self.path)
        return entry

    def recent(self, limit: int = 20) -> List[HistoryEntry]:
        if not self.path.is_file():
            return []
        entries: List[HistoryEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(HistoryEntry.from_dict(json.loads(line)))
            except json.JSONDecodeError:
                continue
        entries.reverse()
        return entries[:limit] if limit > 0 else entries

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
