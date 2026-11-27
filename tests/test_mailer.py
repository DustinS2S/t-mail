"""Tests for MIME message building, attachments, and mocked SMTP delivery."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tmail.config import AccountConfig
from tmail.mailer import MailError, build_message, send_email, test_smtp_connection


class TestMailer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_build_plain_message_with_attachment_and_signature(self) -> None:
        att = self.tmp_path / "report.txt"
        att.write_text("hello attachment", encoding="utf-8")

        msg, infos = build_message(
            from_addr="Dustin <me@example.com>",
            to=["alice@example.com"],
            cc=["bob@example.com"],
            subject="Quarterly Report",
            body="See attached.",
            signature="Best,\nDustin",
            attachments=[str(att)],
        )

        self.assertEqual(msg["From"], "Dustin <me@example.com>")
        self.assertEqual(msg["To"], "alice@example.com")
        self.assertEqual(msg["Cc"], "bob@example.com")
        self.assertIsNone(msg["Bcc"])
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].filename, "report.txt")

        attachments = list(msg.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "report.txt")
        self.assertIn("-- \nBest,\nDustin", msg.get_body(preferencelist=("plain",)).get_content())

    def test_build_markdown_multipart_message(self) -> None:
        msg, _ = build_message(
            from_addr="me@example.com",
            to=["alice@example.com"],
            subject="Update",
            body="# Done\n\nAll **good**.",
            content_type="markdown",
        )
        plain = msg.get_body(preferencelist=("plain",)).get_content()
        html = msg.get_body(preferencelist=("html",)).get_content()
        self.assertIn("# Done", plain)
        self.assertIn("<h1>Done</h1>", html)
        self.assertIn("<strong>good</strong>", html)

    def test_attachment_missing_or_oversized_raises(self) -> None:
        with self.assertRaises(MailError):
            build_message(
                from_addr="me@ex.com",
                to=["a@ex.com"],
                subject="Hi",
                body="Body",
                attachments=[str(self.tmp_path / "missing.pdf")],
            )

        big = self.tmp_path / "big.bin"
        big.write_bytes(b"x" * 2048)
        with self.assertRaises(MailError):
            build_message(
                from_addr="me@ex.com",
                to=["a@ex.com"],
                subject="Hi",
                body="Body",
                attachments=[str(big)],
                max_attachment_bytes=1024,
            )

    @mock.patch("tmail.mailer.smtplib.SMTP_SSL")
    def test_send_email_ssl_includes_bcc_in_envelope_only(self, mock_ssl_cls) -> None:
        server = mock_ssl_cls.return_value
        acct = AccountConfig(
            name="gmail",
            email="me@example.com",
            smtp_host="smtp.gmail.com",
            smtp_port=465,
            security="ssl",
            password_mode="store",
            password="app-password",
        )
        msg, _ = build_message(
            from_addr=acct.formatted_from(),
            to=["alice@example.com"],
            subject="Secret BCC test",
            body="Hello",
        )
        send_email(
            account=acct,
            message=msg,
            envelope_recipients=["alice@example.com", "secret@example.com"],
        )

        server.login.assert_called_once_with("me@example.com", "app-password")
        server.send_message.assert_called_once()
        _, kwargs = server.send_message.call_args
        self.assertEqual(kwargs["to_addrs"], ["alice@example.com", "secret@example.com"])
        self.assertIsNone(msg["Bcc"])
        server.quit.assert_called_once()

    @mock.patch("tmail.mailer.smtplib.SMTP")
    def test_starttls_connection_test(self, mock_smtp_cls) -> None:
        server = mock_smtp_cls.return_value
        server.noop.return_value = (250, b"OK")
        acct = AccountConfig(
            name="outlook",
            email="me@outlook.com",
            smtp_host="smtp.office365.com",
            smtp_port=587,
            security="starttls",
            password_mode="store",
            password="secret",
        )
        res = test_smtp_connection(acct)
        self.assertIn("250", res)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("me@outlook.com", "secret")

    @mock.patch("tmail.mailer.smtplib.SMTP_SSL")
    def test_disconnected_during_login_raises_clean_mail_error(self, mock_ssl_cls) -> None:
        import smtplib

        server = mock_ssl_cls.return_value
        server.login.side_effect = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        server.quit.side_effect = smtplib.SMTPServerDisconnected("please run connect() first")
        acct = AccountConfig(
            name="gmail",
            email="me@gmail.com",
            smtp_host="smtp.gmail.com",
            smtp_port=465,
            security="ssl",
            password_mode="store",
            password="secret",
        )
        with self.assertRaises(MailError) as ctx:
            test_smtp_connection(acct)
        self.assertIn("disconnected unexpectedly", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
