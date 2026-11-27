"""MIME message builder and SMTP/SMTPS/STARTTLS delivery engine for t-mail."""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from tmail import __version__
from tmail.config import AccountConfig, get_termux_ca_bundle, resolve_password
from tmail.markdown_lite import html_to_plain, markdown_to_html
from tmail.ui import bold, cyan, dim, format_size

DEFAULT_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB


class MailError(Exception):
    """Raised when building or sending an email fails."""


@dataclass(frozen=True)
class AttachmentInfo:
    path: Path
    filename: str
    size: int
    mime_type: str


def inspect_attachments(
    paths: Sequence[str],
    max_total_bytes: Optional[int] = DEFAULT_MAX_ATTACHMENT_BYTES,
) -> List[AttachmentInfo]:
    """Validate attachment paths, detect MIME types, and check total size."""
    infos: List[AttachmentInfo] = []
    total_bytes = 0
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.exists():
            raise MailError(f"Attachment not found: {raw}")
        if not p.is_file():
            raise MailError(f"Attachment is not a regular file: {raw}")
        size = p.stat().st_size
        total_bytes += size
        ctype, encoding = mimetypes.guess_type(str(p))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        infos.append(
            AttachmentInfo(
                path=p.resolve(),
                filename=p.name,
                size=size,
                mime_type=ctype,
            )
        )

    if max_total_bytes is not None and total_bytes > max_total_bytes:
        raise MailError(
            f"Total attachment size ({format_size(total_bytes)}) exceeds limit "
            f"({format_size(max_total_bytes)}). Pass --max-size-mb to override."
        )
    return infos


def build_message(
    *,
    from_addr: str,
    to: Sequence[str],
    subject: str,
    body: str,
    cc: Optional[Sequence[str]] = None,
    reply_to: str = "",
    content_type: str = "plain",  # "plain" | "markdown" | "html"
    signature: str = "",
    attachments: Optional[Sequence[str]] = None,
    max_attachment_bytes: Optional[int] = DEFAULT_MAX_ATTACHMENT_BYTES,
) -> Tuple[EmailMessage, List[AttachmentInfo]]:
    """Construct a standards-compliant EmailMessage with optional HTML and attachments."""
    if not to:
        raise MailError("At least one recipient (--to) is required.")

    attachment_infos = inspect_attachments(attachments or [], max_total_bytes=max_attachment_bytes)

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg["User-Agent"] = f"t-mail/{__version__}"
    if reply_to:
        msg["Reply-To"] = reply_to

    full_body = body
    if signature.strip():
        if content_type == "html":
            sig_html = signature.strip().replace("\n", "<br>")
            full_body = f"{body}\n<br><p style=\"color:#666;\">-- <br>{sig_html}</p>"
        else:
            sep = "\n\n-- \n" if body.strip() else "-- \n"
            full_body = f"{body.rstrip()}{sep}{signature.strip()}\n"

    if content_type == "markdown":
        msg.set_content(full_body)
        msg.add_alternative(markdown_to_html(full_body), subtype="html")
    elif content_type == "html":
        plain_fallback = html_to_plain(full_body)
        msg.set_content(plain_fallback or "(HTML email)")
        msg.add_alternative(full_body, subtype="html")
    else:
        msg.set_content(full_body)

    for item in attachment_infos:
        maintype, subtype = item.mime_type.split("/", 1)
        data = item.path.read_bytes()
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=item.filename,
        )

    return msg, attachment_infos


def create_ssl_context() -> ssl.SSLContext:
    """Create a hardened SSL context with Termux CA bundle fallback."""
    ctx = ssl.create_default_context()
    termux_bundle = get_termux_ca_bundle()
    if termux_bundle is not None:
        stats = ctx.cert_store_stats()
        if stats.get("x509_ca", 0) == 0:
            ctx.load_verify_locations(cafile=str(termux_bundle))
    return ctx


def _safe_quit(server) -> None:
    """Best-effort SMTP quit/close without masking prior errors."""
    try:
        server.quit()
    except (smtplib.SMTPException, OSError):
        try:
            server.close()
        except OSError:
            pass


def _open_smtp(account: AccountConfig, password: Optional[str] = None, allow_prompt: bool = True):
    """Resolve credentials first, then connect, negotiate TLS, and authenticate."""
    sec = account.security.lower()
    if sec not in ("ssl", "starttls", "none"):
        raise MailError(f"Unsupported security mode '{account.security}'. Use ssl, starttls, or none.")

    # Resolve password BEFORE opening the socket so interactive prompts never cause server idle timeouts.
    username = account.effective_username()
    secret: Optional[str] = None
    if username and account.password_mode != "none":
        secret = password if password is not None else resolve_password(account, allow_prompt=allow_prompt)

    ctx = create_ssl_context() if sec in ("ssl", "starttls") else None
    try:
        if sec == "ssl":
            server = smtplib.SMTP_SSL(
                account.smtp_host,
                account.smtp_port,
                timeout=account.timeout,
                context=ctx,
            )
            server.ehlo()
        else:
            server = smtplib.SMTP(
                account.smtp_host,
                account.smtp_port,
                timeout=account.timeout,
            )
            server.ehlo()
            if sec == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(
            f"Could not connect to {account.smtp_host}:{account.smtp_port} ({sec}): {exc}"
        ) from exc

    if username and account.password_mode != "none" and secret is not None:
        try:
            server.login(username, secret)
        except smtplib.SMTPAuthenticationError as exc:
            _safe_quit(server)
            hint = ""
            if "gmail" in account.smtp_host.lower():
                hint = " Gmail requires a 16-character App Password (with 2-Step Verification enabled)."
            raise MailError(
                f"SMTP authentication failed for {username} on {account.smtp_host}. "
                f"Check your password / app password.{hint} ({exc.smtp_code})"
            ) from exc
        except smtplib.SMTPServerDisconnected as exc:
            _safe_quit(server)
            raise MailError(
                f"SMTP server {account.smtp_host} disconnected unexpectedly during login: {exc}"
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            _safe_quit(server)
            raise MailError(f"SMTP login error: {exc}") from exc

    return server


def send_email(
    *,
    account: AccountConfig,
    message: EmailMessage,
    envelope_recipients: Sequence[str],
    password: Optional[str] = None,
    allow_prompt: bool = True,
) -> None:
    """Send an EmailMessage through the configured SMTP server."""
    if not envelope_recipients:
        raise MailError("No envelope recipients specified.")

    server = _open_smtp(account, password=password, allow_prompt=allow_prompt)
    try:
        server.send_message(
            message,
            from_addr=account.email,
            to_addrs=list(envelope_recipients),
        )
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"SMTP send failed: {exc}") from exc
    finally:
        _safe_quit(server)


def test_smtp_connection(
    account: AccountConfig,
    password: Optional[str] = None,
    allow_prompt: bool = True,
) -> str:
    """Verify SMTP connectivity, TLS negotiation, and authentication."""
    server = _open_smtp(account, password=password, allow_prompt=allow_prompt)
    try:
        code, resp = server.noop()
        detail = resp.decode("utf-8", errors="replace") if isinstance(resp, bytes) else str(resp)
        return f"SMTP {code} {detail}".strip()
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"SMTP NOOP failed: {exc}") from exc
    finally:
        _safe_quit(server)


def format_preview(
    *,
    account: AccountConfig,
    to: Sequence[str],
    cc: Sequence[str],
    bcc: Sequence[str],
    subject: str,
    body: str,
    content_type: str,
    attachments: Sequence[AttachmentInfo],
    signature: str = "",
) -> str:
    """Render a clean terminal preview of an outgoing message."""
    lines = [
        bold("─── Message Preview ─────────────────────────────"),
        f"{cyan('Account:')}  {account.name} ({account.smtp_host}:{account.smtp_port} [{account.security}])",
        f"{cyan('From:')}     {account.formatted_from()}",
        f"{cyan('To:')}       {', '.join(to)}",
    ]
    if cc:
        lines.append(f"{cyan('Cc:')}       {', '.join(cc)}")
    if bcc:
        lines.append(f"{cyan('Bcc:')}      {', '.join(bcc)} {dim('(hidden in headers)')}")
    lines.append(f"{cyan('Subject:')}  {subject}")
    lines.append(f"{cyan('Format:')}   {content_type}")
    if attachments:
        total = sum(a.size for a in attachments)
        att_list = ", ".join(f"{a.filename} ({format_size(a.size)}, {a.mime_type})" for a in attachments)
        lines.append(f"{cyan('Attach:')}   {att_list} [total {format_size(total)}]")
    lines.append(dim("─────────────────────────────────────────────────"))
    lines.append(body.rstrip() if body.strip() else dim("(empty body)"))
    if signature.strip():
        lines.append(dim("\n-- \n" + signature.strip()))
    lines.append(bold("─────────────────────────────────────────────────"))
    return "\n".join(lines)
