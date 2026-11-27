"""End-to-end CLI tests for t-mail."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tmail.cli import main
from tmail.config import AccountConfig, ConfigStore


class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_dir = Path(self.tmp.name)
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "TMAIL_CONFIG_DIR": str(self.cfg_dir),
                "NO_COLOR": "1",
            },
            clear=False,
        )
        self.env_patch.start()

        store = ConfigStore(self.cfg_dir)
        store.save_account(
            AccountConfig(
                name="testacct",
                email="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                security="ssl",
                password_mode="store",
                password="secret",
            ),
            set_default=True,
        )

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tmp.cleanup()

    def _run(self, argv) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    def test_contact_template_and_dry_run_send(self) -> None:
        att = self.cfg_dir / "notes.txt"
        att.write_text("attachment body", encoding="utf-8")

        code, _ = self._run(["contact", "add", "alice", "alice@example.com", "--name", "Alice"])
        self.assertEqual(code, 0)

        code, _ = self._run(
            [
                "template",
                "add",
                "weekly",
                "-s",
                "Weekly {{week}}",
                "-m",
                "Hi {{name}}, report attached.",
                "--markdown",
            ]
        )
        self.assertEqual(code, 0)

        code, out = self._run(
            [
                "send",
                "-t",
                "alice",
                "--template",
                "weekly",
                "--var",
                "week=48",
                "--var",
                "name=Alice",
                "-a",
                str(att),
                "--dry-run",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("Alice <alice@example.com>", out)
        self.assertIn("Weekly 48", out)
        self.assertIn("notes.txt", out)
        self.assertIn("Dry run enabled", out)

    @mock.patch("tmail.cli.send_email")
    def test_draft_save_send_and_history(self, mock_send) -> None:
        code, out = self._run(
            ["send", "-t", "bob@example.com", "-s", "Later", "-m", "Draft text", "--draft"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Saved draft", out)

        code, list_out = self._run(["draft", "list"])
        self.assertEqual(code, 0)
        self.assertIn("Later", list_out)

        # Extract draft ID from drafts directory
        draft_files = list((self.cfg_dir / "drafts").glob("*.json"))
        self.assertEqual(len(draft_files), 1)
        draft_id = draft_files[0].stem

        code, _ = self._run(["draft", "send", draft_id])
        self.assertEqual(code, 0)
        mock_send.assert_called_once()

        # Draft removed after send
        self.assertEqual(list((self.cfg_dir / "drafts").glob("*.json")), [])

        # Logged to history
        code, hist_json = self._run(["history", "--json"])
        self.assertEqual(code, 0)
        entries = json.loads(hist_json)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["subject"], "Later")
        self.assertEqual(entries[0]["status"], "sent")

    def test_doctor_and_account_list(self) -> None:
        code, out = self._run(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("t-mail v", out)
        self.assertIn("1 configured", out)

        code, out = self._run(["account", "list"])
        self.assertEqual(code, 0)
        self.assertIn("*testacct", out)


if __name__ == "__main__":
    unittest.main()
