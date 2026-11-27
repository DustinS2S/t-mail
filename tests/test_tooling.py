"""Tests for contacts, templates, drafts, history, and markdown_lite."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tmail.contacts import ContactStore
from tmail.drafts import DraftStore
from tmail.editor import format_editor_buffer, parse_editor_buffer
from tmail.history import HistoryStore
from tmail.markdown_lite import html_to_plain, markdown_to_html
from tmail.templates import TemplateStore, parse_var_pairs


class TestContacts(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ContactStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_add_search_and_resolve_group(self) -> None:
        self.store.add("alice", ["alice@example.com"], name="Alice")
        self.store.add("team", ["alice@example.com", "bob@example.com"])

        resolved = self.store.resolve_recipients(["alice", "team", "carol@example.com"])
        self.assertEqual(
            resolved,
            ["Alice <alice@example.com>", "alice@example.com", "bob@example.com", "carol@example.com"],
        )

        matches = self.store.search("bob")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].alias, "team")

    def test_invalid_email_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add("bad", ["not-an-email"])

        with self.assertRaises(ValueError):
            self.store.resolve_recipients(["unknown-alias"])


class TestTemplates(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TemplateStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_render_and_missing_vars(self) -> None:
        tpl = self.store.add(
            "invoice",
            subject="Invoice {{month}}",
            body="Hi {{client}}, total is {{amount}}.",
            content_type="markdown",
        )
        self.assertEqual(tpl.variables(), {"month", "client", "amount"})

        vars_dict = parse_var_pairs(["month=Nov", "client=Alice", "amount=$200"])
        subj, body = tpl.render(vars_dict)
        self.assertEqual(subj, "Invoice Nov")
        self.assertEqual(body, "Hi Alice, total is $200.")

        with self.assertRaises(ValueError):
            tpl.render({"month": "Nov"})


class TestDraftsAndHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.drafts = DraftStore(Path(self.tmp.name))
        self.history = HistoryStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_draft_lifecycle(self) -> None:
        d = self.drafts.save(to=["a@ex.com"], subject="Hello", body="World")
        self.assertIsNotNone(self.drafts.get(d.id))
        self.assertEqual(len(self.drafts.list_all()), 1)
        self.assertTrue(self.drafts.remove(d.id))
        self.assertIsNone(self.drafts.get(d.id))

    def test_history_append_recent_clear(self) -> None:
        self.history.append(account="work", from_addr="me@ex.com", to=["a@ex.com"], subject="One")
        self.history.append(account="work", from_addr="me@ex.com", to=["b@ex.com"], subject="Two")
        recent = self.history.recent(limit=10)
        self.assertEqual([e.subject for e in recent], ["Two", "One"])
        self.history.clear()
        self.assertEqual(self.history.recent(), [])


class TestMarkdownAndEditor(unittest.TestCase):
    def test_markdown_to_html(self) -> None:
        md = "# Hello\n\nThis is **bold**, *italic*, and `code`.\n\n- Item 1\n- [Link](https://example.com)"
        rendered = markdown_to_html(md)
        self.assertIn("<h1>Hello</h1>", rendered)
        self.assertIn("<strong>bold</strong>", rendered)
        self.assertIn("<em>italic</em>", rendered)
        self.assertIn("<code", rendered)
        self.assertIn('<a href="https://example.com">Link</a>', rendered)
        self.assertIn("<ul>", rendered)

    def test_html_to_plain(self) -> None:
        plain = html_to_plain("<h1>Hi</h1><p>Hello <b>world</b><br>Next line</p>")
        self.assertIn("Hi", plain)
        self.assertIn("Hello world", plain)
        self.assertIn("Next line", plain)

    def test_editor_buffer_roundtrip(self) -> None:
        buf = format_editor_buffer(
            to=["a@ex.com", "b@ex.com"],
            cc=["c@ex.com"],
            subject="Sync",
            attachments=["/tmp/file.pdf"],
            body="Line 1\nLine 2",
        )
        parsed = parse_editor_buffer("# comment line\n" + buf)
        self.assertEqual(parsed.to, ["a@ex.com", "b@ex.com"])
        self.assertEqual(parsed.cc, ["c@ex.com"])
        self.assertEqual(parsed.subject, "Sync")
        self.assertEqual(parsed.attachments, ["/tmp/file.pdf"])
        self.assertEqual(parsed.body, "Line 1\nLine 2")


if __name__ == "__main__":
    unittest.main()
