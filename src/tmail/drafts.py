"""Local draft storage for t-mail."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tmail.config import ensure_private_file, get_config_dir


@dataclass
class Draft:
    id: str
    created_at: str
    account: str = ""
    to: List[str] = field(default_factory=list)
    cc: List[str] = field(default_factory=list)
    bcc: List[str] = field(default_factory=list)
    subject: str = ""
    body: str = ""
    content_type: str = "plain"  # "plain" | "markdown" | "html"
    attachments: List[str] = field(default_factory=list)
    reply_to: str = ""
    use_signature: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Draft":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


class DraftStore:
    """Stores unsent email drafts as individual JSON files under drafts/."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.drafts_dir = (config_dir or get_config_dir()).expanduser() / "drafts"

    def save(
        self,
        *,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
        account: str = "",
        content_type: str = "plain",
        reply_to: str = "",
        use_signature: bool = True,
        draft_id: Optional[str] = None,
    ) -> Draft:
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        did = draft_id or uuid.uuid4().hex[:8]
        draft = Draft(
            id=did,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            account=account,
            to=list(to),
            cc=list(cc or []),
            bcc=list(bcc or []),
            subject=subject,
            body=body,
            content_type=content_type,
            attachments=[str(Path(a).expanduser().resolve()) for a in (attachments or [])],
            reply_to=reply_to,
            use_signature=use_signature,
        )
        path = self.drafts_dir / f"{did}.json"
        path.write_text(json.dumps(draft.to_dict(), indent=2) + "\n", encoding="utf-8")
        ensure_private_file(path)
        return draft

    def get(self, draft_id: str) -> Optional[Draft]:
        path = self.drafts_dir / f"{draft_id.strip()}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Draft.from_dict(data)
        except (OSError, json.JSONDecodeError):
            return None

    def list_all(self) -> List[Draft]:
        if not self.drafts_dir.is_dir():
            return []
        drafts: List[Draft] = []
        for path in sorted(self.drafts_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                drafts.append(Draft.from_dict(data))
            except (OSError, json.JSONDecodeError):
                continue
        drafts.sort(key=lambda d: d.created_at, reverse=True)
        return drafts

    def remove(self, draft_id: str) -> bool:
        path = self.drafts_dir / f"{draft_id.strip()}.json"
        if not path.is_file():
            return False
        path.unlink()
        return True
