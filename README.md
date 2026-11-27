# t-mail

A lightweight, **zero-dependency** CLI email client built for **terminal** and **Termux**.

Send emails with attachments, Markdown formatting, contact aliases, reusable templates, drafts, and signatures—without leaving your shell or wrestling with heavy TUI config files.

## Why t-mail?

- **Zero runtime dependencies** — pure Python 3.9+ standard library (`smtplib`, `email`, `ssl`). Installs cleanly in seconds on Termux, Linux, macOS, and Windows with no native compilation.
- **Zero-pain setup** — `tmail init` includes built-in presets for Gmail, Outlook/Microsoft 365, Fastmail, iCloud, Yahoo, Proton Bridge, and custom SMTP.
- **Ergonomic one-liners & pipes** — send inline text, files, Markdown, HTML, piped stdin, and multiple attachments from a single command.
- **Simple built-in tooling** — contact aliases & groups, `{{variable}}` templates, per-account signatures, local drafts, dry-run previews, `$EDITOR` compose, and send history.
- **Termux-ready** — automatically detects Termux CA certificates (`$PREFIX/etc/ssl/certs/ca-certificates.crt`) and locks config file permissions to `0600`.

---

## Installation

### Termux (Android)
```bash
pkg update && pkg install python git ca-certificates
pip install git+https://github.com/DustinS2S/t-mail.git
```

### Linux / macOS / Windows
With `pip` or `uv`:
```bash
pip install git+https://github.com/DustinS2S/t-mail.git
# or
uv tool install git+https://github.com/DustinS2S/t-mail.git
```

From a local clone:
```bash
git clone https://github.com/DustinS2S/t-mail.git
cd t-mail
pip install -e .
```

Both `tmail` and `t-mail` commands are installed.

---

## Quick Start

### 1. Configure an account
```bash
tmail init
```
Follow the prompts to choose a preset (e.g., `gmail`, `outlook`, `fastmail`, `custom`) and pick your password method:
- **`env`** *(recommended)*: reads from `TMAIL_PASSWORD_<ACCOUNT>` or `TMAIL_PASSWORD`
- **`cmd`**: runs a password manager command (e.g. `pass show email/gmail`)
- **`store`**: saves in `config.json` (`0600` permissions on POSIX/Termux)
- **`prompt`**: prompts interactively via `getpass` on each send

### 2. Send your first email
```bash
tmail send -t alice@example.com -s "Hello" -m "Sent straight from my terminal."
```

### 3. Send with attachments & Markdown
```bash
tmail send \
  -t alice@example.com \
  -s "Weekly Report" \
  --body-file ./status.md \
  --markdown \
  -a ./report.pdf \
  -a ./metrics.csv
```

### 4. Pipe command output into an email
```bash
git log -n 5 --oneline | tmail send -t team@example.com -s "Latest commits"
```

### 5. Preview without sending (`--dry-run`)
```bash
tmail send -t alice@example.com -s "Test" -m "Preview me" -a file.txt --dry-run
```

---

## Built-in Tooling

### Contact Aliases & Groups
```bash
# Single contact
tmail contact add alice alice@example.com --name "Alice Smith"

# Group alias
tmail contact add team alice@example.com bob@example.com

# Send using aliases
tmail send -t alice -c team -s "Sync" -m "See you at 10!"
```

### Reusable Templates
```bash
tmail template add invoice \
  -s "Invoice for {{month}}" \
  -m "Hi {{client}}, please find attached the invoice for {{month}}." \
  --markdown

tmail send -t alice --template invoice --var client=Alice --var month=November -a invoice.pdf
```

### Interactive & `$EDITOR` Compose
```bash
# Prompt-based compose (great on mobile/Termux)
tmail compose

# Full header + body compose in nano / vim / $EDITOR
tmail compose --edit
```

### Drafts, Signatures & History
```bash
# Save a draft and send later
tmail send -t alice -s "Draft idea" -m "Work in progress..." --draft
tmail draft list
tmail draft send <draft-id>

# Set a default (or --account specific) signature
tmail signature set --text "Best, Dustin"

# View recent sent log
tmail history -n 10

# Run diagnostics (Termux CA, permissions, SMTP test)
tmail doctor --test-smtp
```

---

## Configuration Directory

| Platform | Default Path |
| :--- | :--- |
| Linux / Termux / macOS | `~/.config/tmail/` (or `$XDG_CONFIG_HOME/tmail`) |
| Windows | `%APPDATA%\tmail\` |

Override anytime with `TMAIL_CONFIG_DIR=/custom/path`.

---

## Development & Tests

```bash
python -m unittest discover -s tests -v
```

## License

MIT
