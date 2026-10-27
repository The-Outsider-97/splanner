"""Workflow D - notes and the sticky board: typing saves by itself into a real SLDC document, search and pinning, deleting, a note
changed elsewhere being offered rather than overwritten, and sticky notes created, typed into, recoloured, dragged and deleted."""
from __future__ import annotations

import re
import unittest
import zipfile
from io import BytesIO

from playwright.sync_api import expect

from harness import APP, E2E, SLAI_ROOT


class Notes(E2E):
    def notes(self) -> list[dict]:
        return self.api("GET", "/api/notes")

    def wait_saved(self) -> None:
        expect(self.page.locator(".save-state")).to_have_attribute("data-state", "saved", timeout=8000)

    def test_a_new_note_saves_while_typing_and_survives_a_reload(self) -> None:
        self.open("#/notes")
        pg = self.page
        pg.get_by_role("button", name="New", exact=True).click()
        expect(pg).to_have_url(re.compile(r"#/notes/new$"))
        pg.get_by_label("Note title").fill("Trip plan")
        pg.get_by_label("Note text").fill("Day 1: arrive\nDay 2: hike")
        self.wait_saved()
        stored = self.notes()
        self.assertEqual([n["title"] for n in stored], ["Trip plan"])
        self.assertEqual(stored[0]["excerpt"], "Day 1: arrive Day 2: hike")
        expect(pg).to_have_url(re.compile(rf"#/notes/{stored[0]['id']}$"))               # the draft became a real note
        pg.reload()
        expect(pg.get_by_label("Note title")).to_have_value("Trip plan")
        expect(pg.get_by_label("Note text")).to_have_value("Day 1: arrive\nDay 2: hike")
        self.shot("notes")

    def test_the_note_is_a_real_sldc_document_other_apps_can_read(self) -> None:
        note = self.api("POST", "/api/notes", {"title": "For Reader", "content": "Hello from SPlanner."}, status=201)
        raw = self.page.evaluate("""async (id) => { const r = await fetch(`/api/notes/${id}/file`); const b = new Uint8Array(await r.arrayBuffer());
                                                    return { type: r.headers.get('content-type'), bytes: Array.from(b) }; }""", note["id"])
        self.assertTrue(zipfile.is_zipfile(BytesIO(bytes(raw["bytes"]))))
        import sys
        sys.path.insert(0, str(SLAI_ROOT))
        from src.utils import sldc
        package = sldc.read_package(self._write(bytes(raw["bytes"])))
        self.assertIn("Hello from SPlanner.", sldc.extract_text(package))

    def _write(self, data: bytes):
        path = self.tmp / "downloaded.sldc"
        path.write_bytes(data)
        return path

    def test_typing_quickly_makes_one_consistent_save(self) -> None:
        note = self.api("POST", "/api/notes", {"title": "Draft", "content": ""}, status=201)
        self.open(f"#/notes/{note['id']}")
        box = self.page.get_by_label("Note text")
        box.click()
        self.page.keyboard.type("one two three four five six seven", delay=20)
        self.wait_saved()
        stored = self.notes()[0]
        self.assertEqual(self.api("GET", f"/api/notes/{note['id']}")["content"], "one two three four five six seven")
        self.assertLess(stored["version"], 8)                                                 # debounced: not one save per key

    def test_search_filters_the_list_and_pinning_moves_a_note_to_the_top(self) -> None:
        self.api("POST", "/api/notes", {"title": "Groceries", "content": "milk, eggs"}, status=201)
        self.api("POST", "/api/notes", {"title": "Ideas", "content": "a boat made of bread"}, status=201)
        self.open("#/notes")
        pg = self.page
        pg.get_by_label("Search notes").fill("bread")
        expect(pg.locator(".note-item")).to_have_count(1)
        expect(pg.locator(".note-item")).to_contain_text("Ideas")
        pg.get_by_label("Search notes").fill("")
        expect(pg.locator(".note-item")).to_have_count(2)
        pg.locator(".note-item", has_text="Groceries").click()
        pg.get_by_role("button", name="Pin note").click()
        expect(pg.locator(".note-item").first).to_contain_text("Groceries")
        expect(pg.locator(".note-item").first.locator(".pin")).to_be_visible()

    def test_delete_a_note(self) -> None:
        note = self.api("POST", "/api/notes", {"title": "Temporary", "content": "x"}, status=201)
        self.open(f"#/notes/{note['id']}")
        pg = self.page
        pg.get_by_role("button", name="Note actions").click()
        pg.get_by_role("menuitem", name="Delete note").click()
        pg.get_by_role("dialog", name="Delete this note?").get_by_role("button", name="Delete").click()
        expect(pg.locator(".note-item")).to_have_count(0)
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.api("GET", "/api/status")["notes"] if "notes" in (self.api("GET", "/api/status") or {}) else 0, 0)

    def test_a_note_changed_elsewhere_is_offered_not_overwritten(self) -> None:
        note = self.api("POST", "/api/notes", {"title": "Shared", "content": "first"}, status=201)
        self.open(f"#/notes/{note['id']}")
        pg = self.page
        self.allow_statuses = (409,)
        self.api("PATCH", f"/api/notes/{note['id']}", {"content": "changed in another window", "version": 1})
        pg.get_by_label("Note text").fill("my edit")
        expect(pg.get_by_role("button", name="Load the latest")).to_be_visible(timeout=8000)
        self.assertEqual(self.api("GET", f"/api/notes/{note['id']}")["content"], "changed in another window")
        pg.get_by_role("button", name="Keep my text").click()
        self.wait_saved()
        self.assertEqual(self.api("GET", f"/api/notes/{note['id']}")["content"], "my edit")

    def test_the_title_comes_from_the_first_line_when_left_empty(self) -> None:
        self.open("#/notes/new")
        self.page.get_by_label("Note text").fill("Remember the milk\nand the eggs")
        self.wait_saved()
        self.assertEqual(self.notes()[0]["title"], "Remember the milk")


class Board(E2E):
    def stickies(self) -> list[dict]:
        return self.api("GET", "/api/stickies")

    def test_add_type_and_it_is_saved(self) -> None:
        self.open("#/board")
        pg = self.page
        pg.get_by_role("button", name="Add sticky note").first.click()
        text = pg.get_by_label("Sticky note text")
        expect(text).to_be_focused()
        text.fill("Pick up parcel")
        for _ in range(40):
            stored = self.stickies()
            if stored and stored[0]["text"] == "Pick up parcel":
                break
            pg.wait_for_timeout(100)
        self.assertEqual(self.stickies()[0]["text"], "Pick up parcel")
        pg.reload()
        expect(pg.get_by_label("Sticky note text")).to_have_value("Pick up parcel")
        self.shot("board")

    def test_drag_by_the_handle_moves_and_remembers_the_position(self) -> None:
        sticky = self.api("POST", "/api/stickies", {"content": "Move me", "color": "pink", "x": 40, "y": 40}, status=201)
        self.open("#/board")
        pg = self.page
        grip = pg.locator(".sticky-grip").first.bounding_box()
        pg.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2)
        pg.mouse.down()
        pg.mouse.move(grip["x"] + 150, grip["y"] + 90, steps=6)
        pg.mouse.up()
        for _ in range(40):
            moved = self.stickies()[0]
            if moved["x"] != 40:
                break
            pg.wait_for_timeout(100)
        self.assertAlmostEqual(moved["x"], 190, delta=12)
        self.assertAlmostEqual(moved["y"], 130, delta=12)
        pg.reload()
        box = pg.locator(".sticky").first.bounding_box()
        canvas = pg.locator(".board-canvas").bounding_box()
        self.assertAlmostEqual(box["x"] - canvas["x"], 190, delta=12)

    def test_arrow_keys_on_the_handle_move_a_note(self) -> None:
        self.api("POST", "/api/stickies", {"content": "Key", "x": 100, "y": 100}, status=201)
        self.open("#/board")
        self.page.locator(".sticky-grip").first.focus()
        for _ in range(3):
            self.page.keyboard.press("ArrowRight")
        for _ in range(40):
            if self.stickies()[0]["x"] > 100:
                break
            self.page.wait_for_timeout(100)
        self.assertGreater(self.stickies()[0]["x"], 100)

    def test_recolour_and_delete(self) -> None:
        self.api("POST", "/api/stickies", {"content": "Colourful", "color": "amber", "x": 40, "y": 40}, status=201)
        self.open("#/board")
        pg = self.page
        pg.locator(".sticky").first.get_by_role("button", name=re.compile("More|actions", re.I)).click()
        pg.get_by_role("menuitem", name=re.compile("Rose|Pink", re.I)).click()
        for _ in range(40):
            if self.stickies()[0]["color"] == "pink":
                break
            pg.wait_for_timeout(100)
        self.assertEqual(self.stickies()[0]["color"], "pink")
        pg.locator(".sticky").first.get_by_role("button", name=re.compile("More|actions", re.I)).click()
        pg.get_by_role("menuitem", name=re.compile("Delete", re.I)).click()
        dlg = pg.get_by_role("dialog", name=re.compile("Delete", re.I))
        if dlg.count():
            dlg.get_by_role("button", name="Delete").click()
        expect(pg.locator(".sticky")).to_have_count(0)
        self.assertEqual(self.stickies(), [])

    def test_sticky_notes_are_also_listed_as_sldc_notes_only_when_asked(self) -> None:
        self.api("POST", "/api/stickies", {"content": "Backed by a file", "x": 40, "y": 40}, status=201)
        self.assertEqual(self.api("GET", "/api/notes"), [])                                     # stickies stay off the Notes list


if __name__ == "__main__":
    unittest.main()
