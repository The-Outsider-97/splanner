"""SPlanner notes and newer (or future) SLDC editions.

A note file can be replaced or changed by another SLAI program (Writer, Reader, Documaster) or by a newer edition of one. SPlanner must
show what it can, never flatten or damage what it cannot edit, and say why. Files here are real packages (see future_files.py).
"""
from __future__ import annotations

import unittest
from pathlib import Path

from applications.splanner.tests import future_files as ff
from applications.splanner.tests.test_store import StoreCase
from applications.tools import slai
from applications.tools.errors import Conflict, StorageError

slai.activate(slai.find_slai_root())


class NewerNotes(StoreCase):
    def make_note(self, content: str = "plain"):
        return self.store.create_note({"title": "N", "content": content})

    def put(self, note: dict, data: bytes) -> Path:
        path = self.dir / "notes" / f"{note['id']}.sldc"
        path.write_bytes(data)
        return path

    def test_a_note_that_a_newer_writer_made_rich_is_shown_as_text_and_stays_untouched(self) -> None:
        note = self.make_note()
        path = self.put(note, ff.modern_bytes())
        before = path.read_bytes()
        got = self.store.get_note(note["id"])
        self.assertFalse(got["editable"])
        self.assertIn("a^2+b^2=c^2", got["content"])
        self.assertIn("E=mc^2", got["content"])                                  # the inline equation's source is part of the text
        with self.assertRaises(Conflict):
            self.store.update_note(note["id"], {"content": "flatten", "version": 1})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.store.update_note(note["id"], {"pinned": True, "version": 1})["pinned"], True)   # metadata still works
        self.assertEqual(path.read_bytes(), before)

    def test_a_single_paragraph_with_an_inline_equation_is_not_editable(self) -> None:
        s = ff.sldc()
        note = self.make_note()
        block = s.make_block("paragraph", "x ￼ y", spans=[{"start": 2, "end": 3, "type": "equation", "equation": {"syntax": "latex", "source": "z"}}])
        package = s.new_package("N", [block], doc_id="splanner-inline")
        package.version = "1.1.0"
        s.write_package(self.dir / "notes" / f"{note['id']}.sldc", package, overwrite=True)
        self.assertFalse(self.store.get_note(note["id"])["editable"])
        with self.assertRaises(Conflict):
            self.store.update_note(note["id"], {"content": "x y", "version": 1})

    def test_a_plain_note_in_a_1_1_file_with_page_settings_keeps_them_when_edited(self) -> None:
        s = ff.sldc()
        note = self.make_note("one")
        package = s.new_package("N", [s.make_block("paragraph", "one")], doc_id="splanner-bands",
                                sections=[{"id": "sec-1", "page": {"size": "A4", "header_mm": 14, "footer_mm": 6}}])
        package.version = "1.1.0"
        path = self.dir / "notes" / f"{note['id']}.sldc"
        s.write_package(path, package, overwrite=True)
        self.assertTrue(self.store.get_note(note["id"])["editable"])
        self.store.update_note(note["id"], {"content": "two", "version": 1})
        again = s.read_package(path)
        self.assertEqual(again.version, "1.1.0")
        self.assertEqual(again.document["sections"][0]["page"]["header_mm"], 14)
        self.assertEqual(s.extract_text(again), "two")

    def test_a_note_from_the_future_is_read_only_text_with_a_reason_and_is_never_rewritten(self) -> None:
        note = self.make_note()
        path = self.put(note, ff.future_requiring_bytes(ff.add_unknown_content))
        before = path.read_bytes()
        got = self.store.get_note(note["id"])
        self.assertFalse(got["editable"])
        self.assertIn("newer program", got["notice"])
        self.assertIn("text of a block type from the future", got["content"])
        with self.assertRaises(Conflict):
            self.store.update_note(note["id"], {"title": "Renamed", "version": 1})
        self.assertEqual(path.read_bytes(), before)
        exported = self.store.export_all()
        self.assertIn("text of a block type from the future", exported["notes"][0]["content"])

    def test_a_newer_edition_that_only_adds_ignorable_things_is_read_normally(self) -> None:
        note = self.make_note()
        self.put(note, ff.future_additive_bytes())
        got = self.store.get_note(note["id"])
        self.assertIn("a^2+b^2=c^2", got["content"])
        self.assertNotIn("notice", got)
        self.assertFalse(got["editable"])                                         # it is read in full, but never rewritten by a program that does not know the edition
        with self.assertRaises(Conflict):
            self.store.update_note(note["id"], {"content": "x", "version": 1})

    def test_a_new_major_edition_is_reported_as_unreadable_for_what_it_is_and_can_still_be_deleted(self) -> None:
        note = self.make_note()
        self.put(note, ff.future_major_bytes())
        with self.assertRaises(StorageError) as caught:
            self.store.get_note(note["id"])
        self.assertIn("newer program", str(caught.exception))
        self.store.delete_note(note["id"])


if __name__ == "__main__":
    unittest.main()
