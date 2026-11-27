"""Configuration, provider presets, and credential resolution for t-mail."""

from __future__ import annotations

import getpass
import json
import os
import shlex
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    smtp_host: str
    smtp_port: int
    security: str  # "ssl" | "starttls" | "none"
    note: str = ""


PROVIDER_PRESETS: Dict[str, ProviderPreset] = {
    "gmail": ProviderPreset(
        key="gmail",
        label="Gmail / Google Workspace",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        security="ssl",
        note="Use a Google App Password (https://myaccount.google.com/apppasswords).",
    ),
    "outlook": ProviderPreset(
        key="outlook",
        label="Outlook / Microsoft 365",
        smtp_host="smtp.office365.com",
        smtp_port=587,
        security="starttls",
        note="Requires SMTP AUTH enabled and an app password if MFA is active.",
    ),
    "fastmail": ProviderPreset(
        key="fastmail",
        label="Fastmail",
        smtp_host="smtp.fastmail.com",
        smtp_port=465,
        security="ssl",
        note="Create an app-specific password under Settings -> Privacy & Security.",
    ),
    "icloud": ProviderPreset(
        key="icloud",
        label="iCloud Mail",
        smtp_host="smtp.mail.me.com",
        smtp_port=587,
        security="starttls",
        note="Use an app-specific password generated at appleid.apple.com.",
    ),
    "yahoo": ProviderPreset(
        key="yahoo",
        label="Yahoo Mail",
        smtp_host="smtp.mail.yahoo.com",
        smtp_port=465,
        security="ssl",
        note="Requires a Yahoo app password from Account Security.",
    ),
    "proton": ProviderPreset(
        key="proton",
        label="Proton Mail Bridge (local)",
        smtp_host="127.0.0.1",
        smtp_port=1025,
        security="starttls",
        note="Requires Proton Mail Bridge running locally.",
    ),
    "custom": ProviderPreset(
        key="custom",
        label="Custom SMTP Server",
        smtp_host="",
        smtp_port=587,
        security="starttls",
        note="Enter your SMTP host, port, and TLS mode.",
    ),
}


@dataclass
class AccountConfig:
    name: str
    email: str
    smtp_host: str
    smtp_port: int = 587
    security: str = "starttls"  # "ssl", "starttls", "none"
    username: str = ""
    display_name: str = ""
    password_mode: str = "env"  # "env", "cmd", "store", "prompt"
    password_env: str = ""
    password_cmd: str = ""
    password: str = ""
    signature: str = ""
    timeout: int = 30

    def effective_username(self) -> str:
        return self.username or self.email

    def effective_env_var(self) -> str:
        if self.password_env:
            return self.password_env
        safe = "".join(c if c.isalnum() else "_" for c in self.name.upper())
        return f"TMAIL_PASSWORD_{safe}"

    def formatted_from(self) -> str:
        if self.display_name:
            return f"{self.display_name} <{self.email}>"
        return self.email

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountConfig":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


def is_termux() -> bool:
    """Detect whether we are running inside Termux on Android."""
    prefix = os.environ.get("PREFIX", "")
    return "com.termux" in prefix or os.environ.get("TERMUX_VERSION") is not None


def get_termux_ca_bundle() -> Optional[Path]:
    """Return Termux CA bundle path if present."""
    prefix = os.environ.get("PREFIX", "")
    if not prefix:
        return None
    candidate = Path(prefix) / "etc" / "ssl" / "certs" / "ca-certificates.crt"
    return candidate if candidate.is_file() else None


def get_config_dir() -> Path:
    """Return platform-appropriate configuration directory."""
    override = os.environ.get("TMAIL_CONFIG_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "tmail"
        return Path.home() / ".config" / "tmail"

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "tmail"
    return Path.home() / ".config" / "tmail"


def ensure_private_file(path: Path) -> None:
    """Restrict file permissions to owner read/write on POSIX (Linux/macOS/Termux)."""
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def check_file_permissions(path: Path) -> Optional[str]:
    """Return a warning message if a sensitive file is group/world readable on POSIX."""
    if os.name != "posix" or not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return f"{path} has loose permissions ({oct(mode)}); run `chmod 600 {path}`."
    return None


class ConfigStore:
    """Manages t-mail accounts and global settings stored in config.json."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = (config_dir or get_config_dir()).expanduser()
        self.config_file = self.config_dir / "config.json"

    def _ensure_dir(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            try:
                os.chmod(self.config_dir, 0o700)
            except OSError:
                pass

    def load_raw(self) -> Dict[str, Any]:
        if not self.config_file.exists():
            return {"default_account": "", "default_signature": "", "accounts": {}}
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Failed to read config at {self.config_file}: {exc}") from exc
        data.setdefault("default_account", "")
        data.setdefault("default_signature", "")
        data.setdefault("accounts", {})
        return data

    def save_raw(self, data: Dict[str, Any]) -> None:
        self._ensure_dir()
        self.config_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        ensure_private_file(self.config_file)

    def list_accounts(self) -> List[AccountConfig]:
        raw = self.load_raw()
        accounts: List[AccountConfig] = []
        for name, item in raw.get("accounts", {}).items():
            entry = dict(item)
            entry.setdefault("name", name)
            accounts.append(AccountConfig.from_dict(entry))
        return accounts

    def get_default_account_name(self) -> str:
        raw = self.load_raw()
        default = raw.get("default_account", "")
        if default and default in raw.get("accounts", {}):
            return default
        accounts = list(raw.get("accounts", {}).keys())
        return accounts[0] if accounts else ""

    def get_account(self, name: Optional[str] = None) -> AccountConfig:
        raw = self.load_raw()
        accounts = raw.get("accounts", {})
        if not accounts:
            raise ConfigError("No email accounts configured. Run `tmail init` to set one up.")
        target = name or self.get_default_account_name()
        if target not in accounts:
            available = ", ".join(sorted(accounts.keys()))
            raise ConfigError(f"Account '{target}' not found. Available: {available}")
        entry = dict(accounts[target])
        entry["name"] = target
        return AccountConfig.from_dict(entry)

    def save_account(self, account: AccountConfig, set_default: bool = False) -> None:
        raw = self.load_raw()
        raw["accounts"][account.name] = account.to_dict()
        if set_default or not raw.get("default_account"):
            raw["default_account"] = account.name
        self.save_raw(raw)

    def set_default_account(self, name: str) -> None:
        raw = self.load_raw()
        if name not in raw.get("accounts", {}):
            raise ConfigError(f"Account '{name}' does not exist.")
        raw["default_account"] = name
        self.save_raw(raw)

    def remove_account(self, name: str) -> bool:
        raw = self.load_raw()
        accounts = raw.get("accounts", {})
        if name not in accounts:
            return False
        del accounts[name]
        if raw.get("default_account") == name:
            remaining = sorted(accounts.keys())
            raw["default_account"] = remaining[0] if remaining else ""
        self.save_raw(raw)
        return True

    def get_signature(self, account_name: Optional[str] = None) -> str:
        raw = self.load_raw()
        if account_name and account_name in raw.get("accounts", {}):
            acct_sig = raw["accounts"][account_name].get("signature", "")
            if acct_sig:
                return acct_sig
        return raw.get("default_signature", "")

    def set_signature(self, text: str, account_name: Optional[str] = None) -> None:
        raw = self.load_raw()
        if account_name:
            if account_name not in raw.get("accounts", {}):
                raise ConfigError(f"Account '{account_name}' does not exist.")
            raw["accounts"][account_name]["signature"] = text
        else:
            raw["default_signature"] = text
        self.save_raw(raw)


def split_command(command: str) -> List[str]:
    """Split a shell-like command string cleanly on both POSIX/Termux and Windows."""
    if os.name == "nt":
        tokens = shlex.split(command, posix=False)
        cleaned: List[str] = []
        for tok in tokens:
            if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ('"', "'"):
                cleaned.append(tok[1:-1])
            else:
                cleaned.append(tok)
        return cleaned
    return shlex.split(command, posix=True)


def resolve_password(account: AccountConfig, allow_prompt: bool = True) -> str:
    """Resolve SMTP password according to account settings with safe fallbacks.

    Precedence:
    1. Account-specific env var (e.g. TMAIL_PASSWORD_WORK) or TMAIL_PASSWORD
    2. Configured password_cmd (when mode == "cmd" or password_cmd is set)
    3. Stored password (when mode == "store")
    4. Interactive getpass prompt (when allow_prompt is True and stdin is a TTY)
    """
    env_var = account.effective_env_var()
    for var in (env_var, "TMAIL_PASSWORD"):
        val = os.environ.get(var)
        if val:
            return val

    if account.password_mode == "cmd" or (account.password_cmd and account.password_mode != "prompt"):
        if not account.password_cmd:
            raise ConfigError(f"Account '{account.name}' is set to command auth but has no password_cmd.")
        try:
            args = split_command(account.password_cmd)
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ConfigError(f"password_cmd failed for account '{account.name}': {exc}") from exc
        secret = proc.stdout.strip()
        if not secret:
            raise ConfigError(f"password_cmd returned empty output for account '{account.name}'.")
        return secret

    if account.password_mode == "store" and account.password:
        return account.password

    if allow_prompt and sys.stdin.isatty():
        return getpass.getpass(f"SMTP password for {account.email}: ")

    raise ConfigError(
        f"No password available for account '{account.name}'. "
        f"Set {env_var} or TMAIL_PASSWORD, configure password_cmd, or run interactively."
    )
