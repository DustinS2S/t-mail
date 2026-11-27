"""Command-line interface for t-mail."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from tmail import __version__
from tmail.config import (
    PROVIDER_PRESETS,
    AccountConfig,
    ConfigError,
    ConfigStore,
    check_file_permissions,
    get_termux_ca_bundle,
    is_termux,
)
from tmail.contacts import ContactStore
from tmail.drafts import DraftStore
from tmail.editor import compose_in_editor, compose_interactive, open_text_in_editor
from tmail.history import HistoryStore
from tmail.mailer import (
    MailError,
    build_message,
    format_preview,
    send_email,
    test_smtp_connection,
)
from tmail.templates import TemplateStore, parse_var_pairs
from tmail.ui import bold, cyan, dim, err, format_table, info, ok, warn


def _read_piped_stdin() -> Optional[str]:
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data:
            return data
    return None


def _prompt(label: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"{label}{suffix}: ").strip()
        if val:
            return val
        if default:
            return default
        if not required:
            return ""
        print(f"{label} is required.")


# ---------------------------------------------------------------------------
# init wizard
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    store = ConfigStore()
    print(bold("t-mail setup wizard"))
    print(dim(f"Config directory: {store.config_dir}"))
    print()

    preset_keys = list(PROVIDER_PRESETS.keys())
    print("Choose an email provider preset:")
    for idx, key in enumerate(preset_keys, start=1):
        preset = PROVIDER_PRESETS[key]
        print(f"  {idx}) {preset.key:<9} - {preset.label}")

    choice = _prompt("Provider (name or number)", default="gmail").lower()
    if choice.isdigit() and 1 <= int(choice) <= len(preset_keys):
        preset = PROVIDER_PRESETS[preset_keys[int(choice) - 1]]
    elif choice in PROVIDER_PRESETS:
        preset = PROVIDER_PRESETS[choice]
    else:
        preset = PROVIDER_PRESETS["custom"]

    if preset.note:
        info(preset.note)

    account_name = args.account or _prompt("Account profile name", default=preset.key if preset.key != "custom" else "default", required=True)
    email_addr = _prompt("Your email address", required=True)
    display_name = _prompt("Display name (optional)", default="")
    smtp_host = _prompt("SMTP host", default=preset.smtp_host, required=True)
    smtp_port = int(_prompt("SMTP port", default=str(preset.smtp_port), required=True))
    security = _prompt("Security (ssl / starttls / none)", default=preset.security).lower()
    username = _prompt("SMTP username", default=email_addr, required=True)

    print("\nHow should t-mail get your SMTP password?")
    print("  1) env    - Read from environment variable (recommended)")
    print("  2) cmd    - Run a command (e.g. `pass show email/gmail`)")
    print("  3) store  - Save in config.json (chmod 600 on Linux/Termux)")
    print("  4) prompt - Ask interactively every time you send")
    mode_map = {"1": "env", "2": "cmd", "3": "store", "4": "prompt"}
    while True:
        raw_mode = _prompt("Choose password mode (1-4 or env/cmd/store/prompt)", default="env").lower()
        password_mode = mode_map.get(raw_mode, raw_mode)
        if password_mode in ("env", "cmd", "store", "prompt"):
            break
        warn("Invalid choice. Enter 1, 2, 3, 4 or env / cmd / store / prompt (do not type your password here).")

    password_env = ""
    password_cmd = ""
    password = ""
    temp_acct = AccountConfig(name=account_name, email=email_addr, smtp_host=smtp_host)

    if password_mode == "env":
        password_env = _prompt("Environment variable name", default=temp_acct.effective_env_var())
        if sys.platform == "win32":
            info(f"Set it in PowerShell: $env:{password_env}='your-app-password'")
        else:
            info(f"Export it in your shell profile: export {password_env}='your-app-password'")
    elif password_mode == "cmd":
        password_cmd = _prompt("Command that prints password to stdout", required=True)
    elif password_mode == "store":
        password = getpass.getpass("SMTP password / app password: ")
        warn("Password saved in config.json. Keep this file private (0600 on Unix/Termux).")

    account = AccountConfig(
        name=account_name,
        email=email_addr,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        security=security,
        username=username,
        display_name=display_name,
        password_mode=password_mode,
        password_env=password_env,
        password_cmd=password_cmd,
        password=password,
    )

    make_default = args.default or not store.list_accounts()
    if not make_default:
        ans = _prompt("Set as default account? (y/N)", default="n").lower()
        make_default = ans in ("y", "yes")

    store.save_account(account, set_default=make_default)
    ok(f"Saved account '{account.name}' ({account.email}) to {store.config_file}")

    if not args.no_test:
        run_test = _prompt("Test SMTP connection now? (y/N)", default="n").lower()
        if run_test in ("y", "yes"):
            try:
                res = test_smtp_connection(account)
                ok(f"Connection succeeded: {res}")
            except (MailError, ConfigError) as exc:
                err(str(exc))
                return 1
    return 0


# ---------------------------------------------------------------------------
# send & compose
# ---------------------------------------------------------------------------

def _execute_send(
    *,
    account_name: Optional[str],
    to_raw: Sequence[str],
    cc_raw: Sequence[str],
    bcc_raw: Sequence[str],
    subject: str,
    body: str,
    attachments: Sequence[str],
    content_type: str,
    reply_to: str,
    no_signature: bool,
    dry_run: bool,
    save_draft: bool,
    max_size_mb: Optional[float],
    confirm: bool = False,
) -> int:
    cfg_store = ConfigStore()
    contact_store = ContactStore(cfg_store.config_dir)
    draft_store = DraftStore(cfg_store.config_dir)
    history_store = HistoryStore(cfg_store.config_dir)

    account = cfg_store.get_account(account_name)
    to = contact_store.resolve_recipients(to_raw)
    cc = contact_store.resolve_recipients(cc_raw)
    bcc = contact_store.resolve_recipients(bcc_raw)

    if save_draft:
        draft = draft_store.save(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            attachments=list(attachments),
            account=account.name,
            content_type=content_type,
            reply_to=reply_to,
            use_signature=not no_signature,
        )
        ok(f"Saved draft {bold(draft.id)} (send later with `tmail draft send {draft.id}`)")
        return 0

    signature = "" if no_signature else cfg_store.get_signature(account.name)
    max_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb is not None else None

    msg, att_infos = build_message(
        from_addr=account.formatted_from(),
        to=to,
        cc=cc,
        subject=subject,
        body=body,
        reply_to=reply_to,
        content_type=content_type,
        signature=signature,
        attachments=attachments,
        max_attachment_bytes=max_bytes,
    )

    if dry_run or confirm:
        print(
            format_preview(
                account=account,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body=body,
                content_type=content_type,
                attachments=att_infos,
                signature=signature,
            )
        )

    if dry_run:
        info("Dry run enabled — email was NOT sent.")
        return 0

    if confirm:
        choice = input("Send this message? [Y]es / [d]raft / [n]o: ").strip().lower()
        if choice in ("d", "draft"):
            draft = draft_store.save(
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body=body,
                attachments=list(attachments),
                account=account.name,
                content_type=content_type,
                reply_to=reply_to,
                use_signature=not no_signature,
            )
            ok(f"Saved draft {bold(draft.id)}")
            return 0
        if choice not in ("", "y", "yes"):
            info("Cancelled.")
            return 0

    envelope = [*to, *cc, *bcc]
    try:
        send_email(account=account, message=msg, envelope_recipients=envelope)
    except (MailError, ConfigError) as exc:
        history_store.append(
            account=account.name,
            from_addr=account.email,
            to=to,
            cc=cc,
            bcc_count=len(bcc),
            subject=subject,
            attachments=list(attachments),
            status="failed",
            error=str(exc),
        )
        raise

    history_store.append(
        account=account.name,
        from_addr=account.email,
        to=to,
        cc=cc,
        bcc_count=len(bcc),
        subject=subject,
        attachments=list(attachments),
        status="sent",
    )
    att_note = f" with {len(att_infos)} attachment(s)" if att_infos else ""
    ok(f"Sent to {', '.join(to)}{att_note} via {account.name}.")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    cfg_store = ConfigStore()
    tpl_store = TemplateStore(cfg_store.config_dir)

    to_raw: List[str] = list(args.to or [])
    cc_raw: List[str] = list(args.cc or [])
    bcc_raw: List[str] = list(args.bcc or [])
    subject: str = args.subject or ""
    body: str = ""
    content_type = "plain"
    attachments: List[str] = list(args.attach or [])

    if args.template:
        tpl = tpl_store.get(args.template)
        if tpl is None:
            raise ConfigError(f"Template '{args.template}' not found.")
        variables = parse_var_pairs(args.var or [])
        tpl_subject, tpl_body = tpl.render(variables)
        if not subject:
            subject = tpl_subject
        body = tpl_body
        content_type = tpl.content_type

    if args.body_file:
        p = Path(args.body_file).expanduser()
        if not p.is_file():
            raise MailError(f"Body file not found: {args.body_file}")
        body = p.read_text(encoding="utf-8")
    elif args.body is not None:
        body = args.body
    elif not body:
        piped = _read_piped_stdin()
        if piped is not None:
            body = piped

    if args.html:
        content_type = "html"
    elif args.markdown:
        content_type = "markdown"

    if args.edit:
        composed = compose_in_editor(
            to=to_raw,
            cc=cc_raw,
            bcc=bcc_raw,
            subject=subject,
            attachments=attachments,
            body=body,
        )
        to_raw = composed.to
        cc_raw = composed.cc
        bcc_raw = composed.bcc
        subject = composed.subject
        attachments = composed.attachments
        body = composed.body

    if not to_raw:
        raise MailError("Missing recipient. Provide -t/--to or use `tmail compose`.")
    if not subject and not args.draft:
        warn("Sending with an empty subject.")

    return _execute_send(
        account_name=args.account,
        to_raw=to_raw,
        cc_raw=cc_raw,
        bcc_raw=bcc_raw,
        subject=subject,
        body=body,
        attachments=attachments,
        content_type=content_type,
        reply_to=args.reply_to or "",
        no_signature=args.no_signature,
        dry_run=args.dry_run,
        save_draft=args.draft,
        max_size_mb=args.max_size_mb,
        confirm=args.confirm,
    )


def cmd_compose(args: argparse.Namespace) -> int:
    if args.edit:
        composed = compose_in_editor(
            to=args.to or (),
            cc=args.cc or (),
            bcc=args.bcc or (),
            subject=args.subject or "",
            attachments=args.attach or (),
        )
    else:
        composed = compose_interactive(
            to=args.to or (),
            cc=args.cc or (),
            bcc=args.bcc or (),
            subject=args.subject or "",
            attachments=args.attach or (),
        )

    content_type = "html" if args.html else ("markdown" if args.markdown else "plain")
    return _execute_send(
        account_name=args.account,
        to_raw=composed.to,
        cc_raw=composed.cc,
        bcc_raw=composed.bcc,
        subject=composed.subject,
        body=composed.body,
        attachments=composed.attachments,
        content_type=content_type,
        reply_to=args.reply_to or "",
        no_signature=args.no_signature,
        dry_run=args.dry_run,
        save_draft=args.draft,
        max_size_mb=args.max_size_mb,
        confirm=not args.yes and not args.dry_run and not args.draft,
    )


# ---------------------------------------------------------------------------
# account subcommands
# ---------------------------------------------------------------------------

def cmd_account(args: argparse.Namespace) -> int:
    store = ConfigStore()
    action = args.account_action

    if action == "list":
        accounts = store.list_accounts()
        if not accounts:
            info("No accounts configured. Run `tmail init`.")
            return 0
        default = store.get_default_account_name()
        rows = []
        for a in accounts:
            marker = "*" if a.name == default else ""
            rows.append([f"{marker}{a.name}", a.email, f"{a.smtp_host}:{a.smtp_port}", a.security, a.password_mode])
        print(format_table(["ACCOUNT", "EMAIL", "SMTP", "TLS", "AUTH"], rows))
        return 0

    if action == "use":
        store.set_default_account(args.name)
        ok(f"Default account set to '{args.name}'.")
        return 0

    if action == "show":
        acct = store.get_account(args.name)
        data = acct.to_dict()
        if data.get("password"):
            data["password"] = "********"
        print(json.dumps(data, indent=2))
        return 0

    if action == "remove":
        if store.remove_account(args.name):
            ok(f"Removed account '{args.name}'.")
            return 0
        err(f"Account '{args.name}' not found.")
        return 1

    if action == "test":
        acct = store.get_account(args.name)
        info(f"Testing {acct.name} ({acct.smtp_host}:{acct.smtp_port}, {acct.security})...")
        result = test_smtp_connection(acct)
        ok(f"Account '{acct.name}' OK: {result}")
        return 0

    return 1


# ---------------------------------------------------------------------------
# contact subcommands
# ---------------------------------------------------------------------------

def cmd_contact(args: argparse.Namespace) -> int:
    store = ContactStore()
    action = args.contact_action

    if action == "add":
        contact = store.add(args.alias, args.emails, name=args.name or "")
        ok(f"Saved contact '{contact.alias}' -> {', '.join(contact.formatted_addresses())}")
        return 0

    if action == "list":
        contacts = store.list_all()
        if not contacts:
            info("No contacts saved. Add one with `tmail contact add mom mom@example.com`.")
            return 0
        rows = [[c.alias, c.name or "-", ", ".join(c.emails)] for c in contacts]
        print(format_table(["ALIAS", "NAME", "EMAIL(S)"], rows))
        return 0

    if action == "search":
        contacts = store.search(args.query)
        if not contacts:
            info(f"No contacts matching '{args.query}'.")
            return 0
        rows = [[c.alias, c.name or "-", ", ".join(c.emails)] for c in contacts]
        print(format_table(["ALIAS", "NAME", "EMAIL(S)"], rows))
        return 0

    if action == "remove":
        if store.remove(args.alias):
            ok(f"Removed contact '{args.alias}'.")
            return 0
        err(f"Contact '{args.alias}' not found.")
        return 1

    return 1


# ---------------------------------------------------------------------------
# template subcommands
# ---------------------------------------------------------------------------

def cmd_template(args: argparse.Namespace) -> int:
    store = TemplateStore()
    action = args.template_action

    if action == "add":
        body = ""
        if args.file:
            p = Path(args.file).expanduser()
            if not p.is_file():
                raise MailError(f"Template body file not found: {args.file}")
            body = p.read_text(encoding="utf-8")
        elif args.body is not None:
            body = args.body
        elif args.edit:
            body = open_text_in_editor("", suffix=".txt")
        else:
            piped = _read_piped_stdin()
            if piped is not None:
                body = piped
            else:
                raise MailError("Provide --body, --file, --edit, or pipe body via stdin.")

        ctype = "html" if args.html else ("markdown" if args.markdown else "plain")
        tpl = store.add(args.name, subject=args.subject, body=body, content_type=ctype)
        vars_str = ", ".join(sorted(tpl.variables())) or "none"
        ok(f"Saved template '{tpl.name}' (variables: {vars_str})")
        return 0

    if action == "list":
        templates = store.list_all()
        if not templates:
            info("No templates saved. Add one with `tmail template add ...`.")
            return 0
        rows = [
            [t.name, t.content_type, t.subject, ", ".join(sorted(t.variables())) or "-"]
            for t in templates
        ]
        print(format_table(["NAME", "FORMAT", "SUBJECT", "VARIABLES"], rows))
        return 0

    if action == "show":
        tpl = store.get(args.name)
        if not tpl:
            err(f"Template '{args.name}' not found.")
            return 1
        print(f"{cyan('Name:')}      {tpl.name}")
        print(f"{cyan('Format:')}    {tpl.content_type}")
        print(f"{cyan('Subject:')}   {tpl.subject}")
        print(f"{cyan('Variables:')} {', '.join(sorted(tpl.variables())) or 'none'}")
        print(dim("─────────────────────────────────────────────────"))
        print(tpl.body)
        return 0

    if action == "remove":
        if store.remove(args.name):
            ok(f"Removed template '{args.name}'.")
            return 0
        err(f"Template '{args.name}' not found.")
        return 1

    return 1


# ---------------------------------------------------------------------------
# signature subcommands
# ---------------------------------------------------------------------------

def cmd_signature(args: argparse.Namespace) -> int:
    store = ConfigStore()
    action = args.signature_action

    if action == "set":
        if args.file:
            p = Path(args.file).expanduser()
            if not p.is_file():
                raise MailError(f"Signature file not found: {args.file}")
            text = p.read_text(encoding="utf-8")
        elif args.text is not None:
            text = args.text
        elif args.edit:
            current = store.get_signature(args.account)
            text = open_text_in_editor(current, suffix=".txt")
        else:
            piped = _read_piped_stdin()
            if piped is not None:
                text = piped
            else:
                raise MailError("Provide --text, --file, --edit, or pipe text via stdin.")
        store.set_signature(text.strip(), account_name=args.account)
        scope = f"account '{args.account}'" if args.account else "default"
        ok(f"Updated {scope} signature.")
        return 0

    if action == "show":
        sig = store.get_signature(args.account)
        if not sig:
            info("No signature configured.")
            return 0
        print(sig)
        return 0

    if action == "clear":
        store.set_signature("", account_name=args.account)
        scope = f"account '{args.account}'" if args.account else "default"
        ok(f"Cleared {scope} signature.")
        return 0

    return 1


# ---------------------------------------------------------------------------
# draft subcommands
# ---------------------------------------------------------------------------

def cmd_draft(args: argparse.Namespace) -> int:
    store = DraftStore()
    action = args.draft_action

    if action == "list":
        drafts = store.list_all()
        if not drafts:
            info("No saved drafts.")
            return 0
        rows = [
            [d.id, d.created_at, ", ".join(d.to), d.subject or "(no subject)", str(len(d.attachments))]
            for d in drafts
        ]
        print(format_table(["ID", "CREATED", "TO", "SUBJECT", "FILES"], rows))
        return 0

    if action == "show":
        draft = store.get(args.id)
        if not draft:
            err(f"Draft '{args.id}' not found.")
            return 1
        print(f"{cyan('ID:')}      {draft.id}")
        print(f"{cyan('Created:')} {draft.created_at}")
        print(f"{cyan('Account:')} {draft.account or '(default)'}")
        print(f"{cyan('To:')}      {', '.join(draft.to)}")
        if draft.cc:
            print(f"{cyan('Cc:')}      {', '.join(draft.cc)}")
        if draft.bcc:
            print(f"{cyan('Bcc:')}     {', '.join(draft.bcc)}")
        print(f"{cyan('Subject:')} {draft.subject}")
        if draft.attachments:
            print(f"{cyan('Attach:')}  {', '.join(draft.attachments)}")
        print(dim("─────────────────────────────────────────────────"))
        print(draft.body)
        return 0

    if action == "send":
        draft = store.get(args.id)
        if not draft:
            err(f"Draft '{args.id}' not found.")
            return 1
        code = _execute_send(
            account_name=args.account or draft.account or None,
            to_raw=draft.to,
            cc_raw=draft.cc,
            bcc_raw=draft.bcc,
            subject=draft.subject,
            body=draft.body,
            attachments=draft.attachments,
            content_type=draft.content_type,
            reply_to=draft.reply_to,
            no_signature=not draft.use_signature,
            dry_run=args.dry_run,
            save_draft=False,
            max_size_mb=args.max_size_mb,
        )
        if code == 0 and not args.dry_run and not args.keep:
            store.remove(draft.id)
        return code

    if action == "remove":
        if store.remove(args.id):
            ok(f"Removed draft '{args.id}'.")
            return 0
        err(f"Draft '{args.id}' not found.")
        return 1

    return 1


# ---------------------------------------------------------------------------
# history & doctor
# ---------------------------------------------------------------------------

def cmd_history(args: argparse.Namespace) -> int:
    store = HistoryStore()
    if args.clear:
        store.clear()
        ok("Cleared sent history.")
        return 0

    entries = store.recent(limit=args.limit)
    if args.json:
        print(json.dumps([e.to_dict() for e in entries], indent=2))
        return 0

    if not entries:
        info("No send history yet.")
        return 0

    rows = [
        [
            e.timestamp,
            e.status,
            e.account,
            ", ".join(e.to),
            e.subject or "(no subject)",
            str(len(e.attachments)),
        ]
        for e in entries
    ]
    print(format_table(["TIME (UTC)", "STATUS", "ACCOUNT", "TO", "SUBJECT", "FILES"], rows))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    store = ConfigStore()
    print(bold(f"t-mail v{__version__} diagnostics"))
    print(f"Python:      {sys.version.split()[0]} ({sys.executable})")
    print(f"Platform:    {sys.platform} (Termux: {'yes' if is_termux() else 'no'})")
    ca_bundle = get_termux_ca_bundle()
    if is_termux():
        print(f"Termux CA:   {ca_bundle or 'not found (install ca-certificates)'}")
    print(f"Config dir:  {store.config_dir}")
    print(f"Config file: {store.config_file} ({'exists' if store.config_file.exists() else 'not created yet'})")

    perm_warn = check_file_permissions(store.config_file)
    if perm_warn:
        warn(perm_warn)

    accounts = store.list_accounts()
    print(f"Accounts:    {len(accounts)} configured (default: {store.get_default_account_name() or 'none'})")

    if args.test_smtp:
        if not accounts:
            warn("No accounts to test. Run `tmail init`.")
            return 1
        failed = False
        for acct in accounts:
            try:
                res = test_smtp_connection(acct, allow_prompt=False)
                ok(f"{acct.name}: {res}")
            except (MailError, ConfigError) as exc:
                err(f"{acct.name}: {exc}")
                failed = True
        return 1 if failed else 0

    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmail",
        description="Lightweight, zero-dependency CLI email client for terminal and Termux.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"t-mail {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # init
    p_init = sub.add_parser("init", help="Interactive wizard to configure an email account")
    p_init.add_argument("--account", help="Profile name for this account")
    p_init.add_argument("--default", action="store_true", help="Set as default account")
    p_init.add_argument("--no-test", action="store_true", help="Skip SMTP connection test prompt")
    p_init.set_defaults(func=cmd_init)

    # send
    p_send = sub.add_parser("send", help="Send an email directly from the command line")
    p_send.add_argument("-t", "--to", action="append", help="Recipient email or contact alias (repeatable)")
    p_send.add_argument("-c", "--cc", action="append", help="CC recipient or alias (repeatable)")
    p_send.add_argument("-b", "--bcc", action="append", help="BCC recipient or alias (repeatable)")
    p_send.add_argument("-s", "--subject", help="Email subject")
    p_send.add_argument("-m", "--body", help="Message body text (or pipe via stdin)")
    p_send.add_argument("-f", "--body-file", help="Read message body from a file")
    p_send.add_argument("-a", "--attach", action="append", help="Attach file path (repeatable)")
    p_send.add_argument("--md", "--markdown", dest="markdown", action="store_true", help="Render body as Markdown (multipart plain+HTML)")
    p_send.add_argument("--html", action="store_true", help="Treat body as HTML with plain-text fallback")
    p_send.add_argument("--template", help="Use a saved template by name")
    p_send.add_argument("--var", action="append", help="Template variable in key=value format (repeatable)")
    p_send.add_argument("--account", help="Send from a specific configured account")
    p_send.add_argument("--reply-to", help="Set Reply-To header")
    p_send.add_argument("--no-signature", action="store_true", help="Do not append signature")
    p_send.add_argument("-e", "--edit", action="store_true", help="Open message in $EDITOR before sending")
    p_send.add_argument("--draft", action="store_true", help="Save as a local draft instead of sending")
    p_send.add_argument("--dry-run", action="store_true", help="Preview resolved message without sending")
    p_send.add_argument("--confirm", action="store_true", help="Preview and ask for confirmation before sending")
    p_send.add_argument("--max-size-mb", type=float, default=25.0, help="Max total attachment size in MB (default: 25)")
    p_send.set_defaults(func=cmd_send)

    # compose
    p_comp = sub.add_parser("compose", help="Interactively compose an email in terminal or $EDITOR")
    p_comp.add_argument("-t", "--to", action="append", help="Prefill recipient(s)")
    p_comp.add_argument("-c", "--cc", action="append", help="Prefill CC recipient(s)")
    p_comp.add_argument("-b", "--bcc", action="append", help="Prefill BCC recipient(s)")
    p_comp.add_argument("-s", "--subject", help="Prefill subject")
    p_comp.add_argument("-a", "--attach", action="append", help="Prefill attachment(s)")
    p_comp.add_argument("-e", "--edit", action="store_true", help="Compose in $EDITOR instead of line prompts")
    p_comp.add_argument("--md", "--markdown", dest="markdown", action="store_true", help="Render body as Markdown")
    p_comp.add_argument("--html", action="store_true", help="Treat body as HTML")
    p_comp.add_argument("--account", help="Account profile to send from")
    p_comp.add_argument("--reply-to", help="Set Reply-To header")
    p_comp.add_argument("--no-signature", action="store_true", help="Do not append signature")
    p_comp.add_argument("--draft", action="store_true", help="Save directly as a draft")
    p_comp.add_argument("--dry-run", action="store_true", help="Preview without sending")
    p_comp.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    p_comp.add_argument("--max-size-mb", type=float, default=25.0, help="Max total attachment size in MB (default: 25)")
    p_comp.set_defaults(func=cmd_compose)

    # account
    p_acct = sub.add_parser("account", help="Manage configured email accounts")
    sub_acct = p_acct.add_subparsers(dest="account_action", required=True)
    sub_acct.add_parser("list", help="List configured accounts")
    p_acct_use = sub_acct.add_parser("use", help="Set the default account")
    p_acct_use.add_argument("name", help="Account profile name")
    p_acct_show = sub_acct.add_parser("show", help="Show account settings (redacted)")
    p_acct_show.add_argument("name", nargs="?", help="Account name (default if omitted)")
    p_acct_rm = sub_acct.add_parser("remove", help="Remove an account")
    p_acct_rm.add_argument("name", help="Account profile name")
    p_acct_test = sub_acct.add_parser("test", help="Test SMTP connection and login")
    p_acct_test.add_argument("name", nargs="?", help="Account name (default if omitted)")
    p_acct.set_defaults(func=cmd_account)

    # contact
    p_con = sub.add_parser("contact", help="Manage contact aliases and recipient groups")
    sub_con = p_con.add_subparsers(dest="contact_action", required=True)
    p_con_add = sub_con.add_parser("add", help="Add or update a contact alias or group")
    p_con_add.add_argument("alias", help="Short alias (e.g. mom, team)")
    p_con_add.add_argument("emails", nargs="+", help="One or more email addresses")
    p_con_add.add_argument("--name", help="Display name (used for single-address contacts)")
    sub_con.add_parser("list", help="List all contacts")
    p_con_search = sub_con.add_parser("search", help="Search contacts")
    p_con_search.add_argument("query", help="Search query")
    p_con_rm = sub_con.add_parser("remove", help="Remove a contact alias")
    p_con_rm.add_argument("alias", help="Alias to remove")
    p_con.set_defaults(func=cmd_contact)

    # template
    p_tpl = sub.add_parser("template", help="Manage reusable email templates")
    sub_tpl = p_tpl.add_subparsers(dest="template_action", required=True)
    p_tpl_add = sub_tpl.add_parser("add", help="Create or update a template")
    p_tpl_add.add_argument("name", help="Template name")
    p_tpl_add.add_argument("-s", "--subject", required=True, help="Subject line (supports {{vars}})")
    p_tpl_add.add_argument("-m", "--body", help="Template body text")
    p_tpl_add.add_argument("-f", "--file", help="Load template body from file")
    p_tpl_add.add_argument("-e", "--edit", action="store_true", help="Write template body in $EDITOR")
    p_tpl_add.add_argument("--md", "--markdown", dest="markdown", action="store_true", help="Mark template as Markdown")
    p_tpl_add.add_argument("--html", action="store_true", help="Mark template as HTML")
    sub_tpl.add_parser("list", help="List saved templates")
    p_tpl_show = sub_tpl.add_parser("show", help="Show a template")
    p_tpl_show.add_argument("name", help="Template name")
    p_tpl_rm = sub_tpl.add_parser("remove", help="Remove a template")
    p_tpl_rm.add_argument("name", help="Template name")
    p_tpl.set_defaults(func=cmd_template)

    # signature
    p_sig = sub.add_parser("signature", help="Manage default or per-account signatures")
    sub_sig = p_sig.add_subparsers(dest="signature_action", required=True)
    p_sig_set = sub_sig.add_parser("set", help="Set signature text")
    p_sig_set.add_argument("--account", help="Set signature for a specific account")
    p_sig_set.add_argument("--text", help="Signature text")
    p_sig_set.add_argument("-f", "--file", help="Load signature from file")
    p_sig_set.add_argument("-e", "--edit", action="store_true", help="Edit signature in $EDITOR")
    p_sig_show = sub_sig.add_parser("show", help="Show current signature")
    p_sig_show.add_argument("--account", help="Account profile name")
    p_sig_clr = sub_sig.add_parser("clear", help="Clear signature")
    p_sig_clr.add_argument("--account", help="Account profile name")
    p_sig.set_defaults(func=cmd_signature)

    # draft
    p_dr = sub.add_parser("draft", help="Manage locally saved drafts")
    sub_dr = p_dr.add_subparsers(dest="draft_action", required=True)
    sub_dr.add_parser("list", help="List saved drafts")
    p_dr_show = sub_dr.add_parser("show", help="Show a draft")
    p_dr_show.add_argument("id", help="Draft ID")
    p_dr_send = sub_dr.add_parser("send", help="Send a saved draft")
    p_dr_send.add_argument("id", help="Draft ID")
    p_dr_send.add_argument("--account", help="Override account")
    p_dr_send.add_argument("--dry-run", action="store_true", help="Preview without sending")
    p_dr_send.add_argument("--keep", action="store_true", help="Keep draft after sending")
    p_dr_send.add_argument("--max-size-mb", type=float, default=25.0, help="Max total attachment size in MB")
    p_dr_rm = sub_dr.add_parser("remove", help="Delete a draft")
    p_dr_rm.add_argument("id", help="Draft ID")
    p_dr.set_defaults(func=cmd_draft)

    # history / log
    p_hist = sub.add_parser("history", aliases=["log"], help="View local send history")
    p_hist.add_argument("-n", "--limit", type=int, default=20, help="Number of recent entries (default: 20)")
    p_hist.add_argument("--json", action="store_true", help="Output history as JSON")
    p_hist.add_argument("--clear", action="store_true", help="Clear history log")
    p_hist.set_defaults(func=cmd_history)

    # doctor
    p_doc = sub.add_parser("doctor", help="Check environment, Termux CA bundle, and config health")
    p_doc.add_argument("--test-smtp", action="store_true", help="Test SMTP login for all configured accounts")
    p_doc.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return int(args.func(args))
    except (ConfigError, MailError, ValueError, RuntimeError) as exc:
        err(str(exc))
        return 1
    except KeyboardInterrupt:
        print()
        warn("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
