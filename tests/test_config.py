"""Tests for tmail.config."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tmail.config import (
    AccountConfig,
    ConfigError,
    ConfigStore,
    resolve_password,
)


class TestConfigStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_and_get_default_account(self) -> None:
        acct = AccountConfig(
            name="work",
            email="me@work.example",
            smtp_host="smtp.work.example",
            smtp_port=465,
            security="ssl",
            display_name="Dustin",
        )
        self.store.save_account(acct)

        loaded = self.store.get_account()
        self.assertEqual(loaded.name, "work")
        self.assertEqual(loaded.email, "me@work.example")
        self.assertEqual(loaded.formatted_from(), "Dustin <me@work.example>")
        self.assertEqual(self.store.get_default_account_name(), "work")

    def test_multiple_accounts_switch_and_remove(self) -> None:
        a1 = AccountConfig(name="personal", email="a@ex.com", smtp_host="smtp.a.com")
        a2 = AccountConfig(name="work", email="b@ex.com", smtp_host="smtp.b.com")
        self.store.save_account(a1)
        self.store.save_account(a2)

        self.assertEqual(self.store.get_default_account_name(), "personal")
        self.store.set_default_account("work")
        self.assertEqual(self.store.get_default_account_name(), "work")

        self.assertTrue(self.store.remove_account("work"))
        self.assertEqual(self.store.get_default_account_name(), "personal")
        self.assertFalse(self.store.remove_account("nonexistent"))

    def test_missing_account_raises(self) -> None:
        with self.assertRaises(ConfigError):
            self.store.get_account()

    def test_signatures_default_and_per_account(self) -> None:
        acct = AccountConfig(name="work", email="b@ex.com", smtp_host="smtp.b.com")
        self.store.save_account(acct)

        self.store.set_signature("Global Sig")
        self.assertEqual(self.store.get_signature("work"), "Global Sig")

        self.store.set_signature("Work Sig", account_name="work")
        self.assertEqual(self.store.get_signature("work"), "Work Sig")
        self.assertEqual(self.store.get_signature(), "Global Sig")


class TestPasswordResolution(unittest.TestCase):
    def test_env_var_takes_precedence(self) -> None:
        acct = AccountConfig(
            name="work",
            email="u@ex.com",
            smtp_host="smtp.ex.com",
            password_mode="store",
            password="stored-password",
        )
        with mock.patch.dict(os.environ, {"TMAIL_PASSWORD_WORK": "env-secret"}, clear=False):
            self.assertEqual(resolve_password(acct, allow_prompt=False), "env-secret")

    def test_password_cmd(self) -> None:
        acct = AccountConfig(
            name="cmdacct",
            email="u@ex.com",
            smtp_host="smtp.ex.com",
            password_mode="cmd",
            password_cmd=f'"{sys.executable}" -c "print(\'cmd-secret\')"',
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMAIL_PASSWORD", None)
            os.environ.pop("TMAIL_PASSWORD_CMDACCT", None)
            self.assertEqual(resolve_password(acct, allow_prompt=False), "cmd-secret")

    def test_stored_password(self) -> None:
        acct = AccountConfig(
            name="stored",
            email="u@ex.com",
            smtp_host="smtp.ex.com",
            password_mode="store",
            password="file-secret",
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMAIL_PASSWORD", None)
            os.environ.pop("TMAIL_PASSWORD_STORED", None)
            self.assertEqual(resolve_password(acct, allow_prompt=False), "file-secret")

    def test_missing_password_non_interactive_raises(self) -> None:
        acct = AccountConfig(name="empty", email="u@ex.com", smtp_host="smtp.ex.com", password_mode="env")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TMAIL_PASSWORD", None)
            os.environ.pop("TMAIL_PASSWORD_EMPTY", None)
            with self.assertRaises(ConfigError):
                resolve_password(acct, allow_prompt=False)


if __name__ == "__main__":
    unittest.main()
