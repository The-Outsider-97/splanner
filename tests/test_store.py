"""Tests for SPlanner's store: real SQLite, real SLDC files, real migrations."""
from __future__ import annotations

import errno
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from applications.splanner import store as store_module
from applications.splanner.store import PlannerStore, events_to_ics
from applications.tools import slai, validation as v
from applications.tools.errors import BadRequest, Conflict, LimitExceeded, NotFound, StorageError

slai.activate(slai.find_slai_root())


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


NOW = datetime.now(timezone.utc).replace(microsecond=0)


class StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = PlannerStore(self.dir)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def event(self, **extra):
        data = {"title": "Standup", "start_at": iso(NOW + timedelta(hours=2)), "end_at": iso(NOW + timedelta(hours=3)), "time_zone": "Europe/Amsterdam"}
        data.update(extra)
        return self.store.create_event(data)


class EventTests(StoreCase):
    def test_fresh_database_is_current(self) -> None:
        self.assertEqual((self.store.db.schema_version, self.store.db.migrated), (3, [1, 2, 3]))

    def test_create_read_update_delete(self) -> None:
        e = self.event(description="Daily\nsync", location="Room 4", color="green", all_day=False)
        self.assertEqual((e["version"], e["all_day"], e["location"], e["color"], e["remind_before"]), (1, False, "Room 4", "green", None))
        self.assertEqual(self.store.list_events()[0]["id"], e["id"])
        changed = self.store.update_event(e["id"], {"title": "Standup (moved)", "version": 1})
        self.assertEqual((changed["title"], changed["version"]), ("Standup (moved)", 2))
        with self.assertRaises(Conflict):
            self.store.update_event(e["id"], {"title": "stale", "version": 1})
        self.store.delete_event(e["id"])
        with self.assertRaises(NotFound):
            self.store.get_event(e["id"])

    def test_validation_names_the_problem(self) -> None:
        bad = [({"title": ""}, "title"), ({"title": "x", "start_at": "tomorrow"}, "start_at"),
               ({"title": "x", "color": "red"}, "color"), ({"title": "x", "all_day": 1}, "all_day"),
               ({"title": "x", "remind_before": -1}, "remind_before"), ({"title": "x", "time_zone": "Mars/Base"}, "time_zone"),
               ({"title": "x", "surprise": 1}, "surprise")]
        for data, word in bad:
            full = {"start_at": iso(NOW), "end_at": iso(NOW + timedelta(hours=1)), **data}
            with self.assertRaises(BadRequest, msg=str(data)) as caught:
                self.store.create_event(full)
            self.assertIn(word, caught.exception.message)
        with self.assertRaises(BadRequest) as caught:
            self.store.create_event({"title": "x", "start_at": iso(NOW), "end_at": iso(NOW)})
        self.assertIn("end after", caught.exception.message)
        e = self.event()
        with self.assertRaises(BadRequest):                       # moving only the start past the stored end is still caught
            self.store.update_event(e["id"], {"start_at": iso(NOW + timedelta(hours=9))})

    def test_range_query_returns_overlaps(self) -> None:
        a = self.event(title="A", start_at=iso(NOW), end_at=iso(NOW + timedelta(hours=1)))
        b = self.event(title="B", start_at=iso(NOW + timedelta(days=3)), end_at=iso(NOW + timedelta(days=3, hours=1)))
        got = [e["title"] for e in self.store.list_events(iso(NOW - timedelta(hours=1)), iso(NOW + timedelta(days=1)))]
        self.assertEqual(got, ["A"])
        got = [e["title"] for e in self.store.list_events(iso(NOW + timedelta(hours=1)), iso(NOW + timedelta(days=4)))]
        self.assertEqual(got, ["B"])                              # A ends exactly at the range start: no overlap
        self.assertEqual(len(self.store.list_events()), 2)
        self.assertIsNotNone(a and b)

    def test_event_reminder_follows_the_event(self) -> None:
        e = self.event(remind_before=15)
        [r] = self.store.list_reminders()
        self.assertEqual((r["entity_type"], r["entity_id"], r["status"]), ("event", e["id"], "pending"))
        self.assertEqual(r["due_at"], iso(NOW + timedelta(hours=2) - timedelta(minutes=15)))
        self.store.update_event(e["id"], {"title": "Renamed", "start_at": iso(NOW + timedelta(hours=5)), "end_at": iso(NOW + timedelta(hours=6))})
        [r] = self.store.list_reminders()
        self.assertEqual((r["title"], r["due_at"]), ("Renamed", iso(NOW + timedelta(hours=5) - timedelta(minutes=15))))
        self.store.update_event(e["id"], {"remind_before": None})
        self.assertEqual(self.store.list_reminders(), [])
        self.store.update_event(e["id"], {"remind_before": 5})
        self.assertEqual(len(self.store.list_reminders()), 1)
        self.store.delete_event(e["id"])
        self.assertEqual(self.store.list_reminders(), [])

    def test_a_reminder_for_an_event_already_under_way_is_not_fired(self) -> None:
        self.event(start_at=iso(NOW - timedelta(hours=1)), end_at=iso(NOW + timedelta(hours=1)), remind_before=10)
        self.assertEqual(self.store.due_reminders(), [])
        self.assertEqual(self.store.list_reminders()[0]["status"], "dismissed")

    def test_dismissed_event_reminder_stays_dismissed_until_the_time_moves(self) -> None:
        e = self.event(remind_before=10)
        [r] = self.store.list_reminders()
        self.store.change_reminder(r["id"], {"action": "dismiss"})
        self.store.update_event(e["id"], {"title": "Same time, new title"})
        self.assertEqual(self.store.list_reminders()[0]["status"], "dismissed")
        self.store.update_event(e["id"], {"start_at": iso(NOW + timedelta(hours=8)), "end_at": iso(NOW + timedelta(hours=9))})
        self.assertEqual(self.store.list_reminders()[0]["status"], "pending")

    def test_ics_export(self) -> None:
        self.event(title="Lunch, with; friends", description="Line one\nLine two " + "x" * 120, location="Café", remind_before=30)
        day = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)          # midnight on 4 Nov in Amsterdam
        self.event(title="Holiday", start_at=iso(day), end_at=iso(day + timedelta(days=2)), all_day=True)
        text = self.store.export_ics()
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n"))
        self.assertIn("SUMMARY:Lunch\\, with\\; friends", text)
        self.assertIn("DESCRIPTION:Line one\\nLine two", text)
        self.assertIn("TRIGGER:-PT30M", text)
        self.assertIn("DTSTART;VALUE=DATE:20261104", text)
        self.assertIn("DTEND;VALUE=DATE:20261106", text)
        for line in text.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        self.assertEqual(events_to_ics([]).count("VEVENT"), 0)


class TaskTests(StoreCase):
    def test_lifecycle_and_completion_time(self) -> None:
        t = self.store.create_task({"title": "Write report", "priority": "high", "due_at": iso(NOW + timedelta(days=1)), "description": "Draft\nthen send"})
        self.assertEqual((t["status"], t["priority"], t["completed_at"], t["version"]), ("open", "high", None, 1))
        done = self.store.update_task(t["id"], {"status": "done"})
        self.assertEqual(done["status"], "done")
        self.assertIsNotNone(done["completed_at"])
        again = self.store.update_task(done["id"], {"status": "open"})
        self.assertIsNone(again["completed_at"])
        with self.assertRaises(BadRequest):
            self.store.update_task(t["id"], {"priority": "urgent"})
        with self.assertRaises(Conflict):
            self.store.update_task(t["id"], {"title": "x", "version": 1})
        self.store.delete_task(t["id"])
        with self.assertRaises(NotFound):
            self.store.delete_task(t["id"])

    def test_clear_completed(self) -> None:
        for i in range(3):
            self.store.create_task({"title": f"t{i}", "status": "done" if i else "open"})
        self.assertEqual(self.store.clear_completed_tasks(), 2)
        self.assertEqual([t["title"] for t in self.store.list_tasks()], ["t0"])


class NoteTests(StoreCase):
    def test_sldc_round_trip(self) -> None:
        n = self.store.create_note({"title": "Ideas", "content": "First line\n\nSecond paragraph — with ünïcode"})
        self.assertEqual((n["version"], n["kind"], n["editable"]), (1, "note", True))
        self.assertTrue((self.dir / "notes" / f"{n['id']}.sldc").is_file())
        module = slai.sldc()
        package = module.read_package(self.dir / "notes" / f"{n['id']}.sldc")                 # the canonical reader accepts our file
        self.assertEqual(package.document["title"], "Ideas")
        self.assertIn("ünïcode", module.extract_text(package))
        self.assertEqual(self.store.get_note(n["id"])["content"], "First line\n\nSecond paragraph — with ünïcode")
        saved = self.store.update_note(n["id"], {"content": "changed", "title": "Ideas 2", "version": 1})
        self.assertEqual((saved["version"], saved["content"], saved["title"]), (2, "changed", "Ideas 2"))
        self.assertEqual(self.store.list_notes()[0]["excerpt"], "changed")

    def test_notes_are_saved_where_the_system_refuses_to_rename_files(self) -> None:
        """Windows reports WinError 17 / errno 18 for a rename inside a redirected AppData folder (seen with a Store Python)."""
        def refuse(*_args, **_kwargs):
            raise OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")
        with mock.patch.object(os, "replace", refuse), mock.patch.object(os, "link", refuse), mock.patch("time.sleep"):
            n = self.store.create_note({"title": "bobby", "content": "typed text"})
            saved = self.store.update_note(n["id"], {"content": "typed text, edited", "version": 1})
        self.assertEqual(saved["content"], "typed text, edited")
        module = slai.sldc()
        package = module.read_package(self.dir / "notes" / f"{n['id']}.sldc")                 # a valid SLDC file, readable by SLAI
        self.assertEqual(module.extract_text(package), "typed text, edited")
        self.assertEqual([f.name for f in (self.dir / "notes").iterdir()], [f"{n['id']}.sldc"])   # no temporary file left behind

    def test_versions_protect_against_lost_edits_and_are_required(self) -> None:
        n = self.store.create_note({"title": "A", "content": "x"})
        self.store.update_note(n["id"], {"content": "y", "version": 1})
        with self.assertRaises(Conflict):
            self.store.update_note(n["id"], {"content": "stale", "version": 1})
        with self.assertRaises(BadRequest):
            self.store.update_note(n["id"], {"content": "no version"})
        self.assertEqual(self.store.get_note(n["id"])["content"], "y")

    def test_pin_orders_first_and_does_not_touch_the_file(self) -> None:
        a = self.store.create_note({"title": "A"})
        b = self.store.create_note({"title": "B"})
        before = (self.dir / "notes" / f"{a['id']}.sldc").read_bytes()
        self.store.update_note(a["id"], {"pinned": True, "version": 1})
        self.assertEqual([n["title"] for n in self.store.list_notes()], ["A", "B"])
        self.assertEqual((self.dir / "notes" / f"{a['id']}.sldc").read_bytes(), before)
        self.assertIsNotNone(b)

    def test_advanced_and_signed_notes_are_refused_not_flattened(self) -> None:
        module = slai.sldc()
        n = self.store.create_note({"title": "Rich", "content": "one"})
        package = module.new_package("Rich", [module.make_block("heading", "H"), module.make_block("paragraph", "body")], doc_id="x")
        module.write_package(self.dir / "notes" / f"{n['id']}.sldc", package, overwrite=True)
        got = self.store.get_note(n["id"])
        self.assertFalse(got["editable"])
        with self.assertRaises(Conflict):
            self.store.update_note(n["id"], {"content": "flatten", "version": 1})
        self.assertEqual(self.store.update_note(n["id"], {"pinned": True, "version": 1})["pinned"], True)   # metadata still works

    def test_damaged_and_missing_files_are_reported(self) -> None:
        n = self.store.create_note({"title": "Hurt", "content": "x"})
        path = self.dir / "notes" / f"{n['id']}.sldc"
        path.write_bytes(b"not a zip")
        with self.assertRaises(StorageError) as caught:
            self.store.get_note(n["id"])
        self.assertEqual(caught.exception.reason, "corrupt")
        path.unlink()
        with self.assertRaises(StorageError):
            self.store.get_note(n["id"])
        self.store.delete_note(n["id"])                          # a broken note can still be deleted
        self.assertEqual(self.store.list_notes(), [])

    def test_delete_removes_the_file_and_unlinks_references(self) -> None:
        n = self.store.create_note({"title": "Linked", "content": "x"})
        e = self.event()
        self.store.db.execute("UPDATE events SET note_id=? WHERE id=?", (n["id"], e["id"]))
        self.store.delete_note(n["id"])
        self.assertFalse((self.dir / "notes" / f"{n['id']}.sldc").exists())
        self.assertIsNone(self.store.get_event(e["id"])["note_id"])

    def test_database_failure_leaves_no_orphan_file(self) -> None:
        self.store.db.execute("CREATE TRIGGER no_notes BEFORE INSERT ON notes BEGIN SELECT RAISE(ABORT, 'nope'); END")
        with self.assertRaises(sqlite3.Error):
            self.store.create_note({"title": "x"})
        self.assertEqual(list((self.dir / "notes").iterdir()), [])

    def test_limits(self) -> None:
        old = store_module.LIMITS["notes"]
        store_module.LIMITS["notes"] = 1
        try:
            self.store.create_note({"title": "one"})
            with self.assertRaises(LimitExceeded):
                self.store.create_note({"title": "two"})
        finally:
            store_module.LIMITS["notes"] = old

    def test_download_gives_a_valid_sldc(self) -> None:
        n = self.store.create_note({"title": "Plan: Q4/2026?", "content": "x"})
        body, name = self.store.note_file(n["id"])
        self.assertTrue(name.endswith(".sldc") and "/" not in name and ":" not in name and "?" not in name)
        self.assertEqual(body[:2], b"PK")


class ReminderTests(StoreCase):
    def test_standalone_reminders_snooze_and_dismiss(self) -> None:
        r = self.store.create_reminder({"title": "Call", "due_at": iso(NOW - timedelta(minutes=1))})
        self.assertEqual([x["id"] for x in self.store.due_reminders()], [r["id"]])
        with self.assertRaises(BadRequest):
            self.store.change_reminder(r["id"], {"action": "snooze", "due_at": iso(NOW - timedelta(minutes=5))})
        with self.assertRaises(BadRequest):
            self.store.change_reminder(r["id"], {"action": "snooze"})
        snoozed = self.store.change_reminder(r["id"], {"action": "snooze", "minutes": 10})
        self.assertEqual(snoozed["status"], "pending")
        self.assertEqual(self.store.due_reminders(), [])
        self.assertEqual(self.store.change_reminder(r["id"], {"action": "dismiss"})["status"], "dismissed")
        self.assertEqual(self.store.change_reminder(r["id"], {"action": "restore"})["status"], "pending")
        self.assertEqual(self.store.change_reminder(r["id"], {"title": "Call Sam"})["title"], "Call Sam")
        self.store.delete_reminder(r["id"])
        self.assertEqual(self.store.list_reminders(), [])

    def test_deleting_an_events_alert_turns_it_off_on_the_event(self) -> None:
        e = self.event(remind_before=10)
        self.store.delete_reminder(self.store.list_reminders()[0]["id"])
        self.assertIsNone(self.store.get_event(e["id"])["remind_before"])


class StickyTests(StoreCase):
    def test_lifecycle(self) -> None:
        s = self.store.create_sticky({"content": "Buy milk\nand eggs", "color": "pink", "x": 120, "y": 80.5})
        self.assertEqual((s["x"], s["y"], s["color"], s["text"], s["version"]), (120.0, 80.5, "pink", "Buy milk\nand eggs", 1))
        self.assertEqual(self.store.list_notes(), [])                         # sticky text is not a note in the notes list
        self.assertEqual(self.store.counts()["notes"], 0)
        moved = self.store.update_sticky(s["id"], {"x": 10, "y": 20, "version": 1})
        self.assertEqual((moved["x"], moved["y"], moved["version"]), (10.0, 20.0, 2))
        edited = self.store.update_sticky(s["id"], {"content": "Changed", "version": 2})
        self.assertEqual((edited["text"], edited["version"]), ("Changed", 3))
        self.assertEqual(self.store.list_stickies()[0]["text"], "Changed")
        with self.assertRaises(Conflict):
            self.store.update_sticky(s["id"], {"x": 1, "version": 1})
        with self.assertRaises(BadRequest):
            self.store.update_sticky(s["id"], {"x": 10 ** 6})
        note_file = self.dir / "notes" / f"{s['note_id']}.sldc"
        self.assertTrue(note_file.exists())
        self.store.delete_sticky(s["id"])
        self.assertFalse(note_file.exists())
        self.assertEqual(self.store.db.scalar("SELECT COUNT(*) FROM notes"), 0)

    def test_a_sticky_keeps_its_line_breaks_and_whole_text(self) -> None:
        text = "First line\n\n  indented second\n" + "long " * 120 + "end"                    # over the 160-character excerpt, with blank lines
        s = self.store.create_sticky({"content": text})
        self.assertEqual(self.store.list_stickies()[0]["text"], text)
        self.assertEqual(self.store.update_sticky(s["id"], {"x": 5, "version": 1})["text"], text)
        again = PlannerStore(self.store.data_dir)
        self.addCleanup(again.db.close)
        self.assertEqual(again.list_stickies()[0]["text"], text)

    def test_a_sticky_cannot_be_deleted_as_a_note(self) -> None:
        s = self.store.create_sticky({"content": "x"})
        with self.assertRaises(BadRequest):
            self.store.delete_note(s["note_id"])


class SearchAndExportTests(StoreCase):
    def test_search_across_everything_case_and_accent_insensitive(self) -> None:
        self.event(title="Budget review", location="Café Noir")
        self.store.create_task({"title": "Send BUDGET numbers"})
        self.store.create_note({"title": "Meeting", "content": "the budget is tight"})
        self.store.create_sticky({"content": "budget idea"})
        self.store.create_reminder({"title": "Budget call", "due_at": iso(NOW + timedelta(days=1))})
        kinds = sorted(hit["kind"] for hit in self.store.search("Budget"))
        self.assertEqual(kinds, ["event", "note", "reminder", "sticky", "task"])
        self.assertEqual([h["kind"] for h in self.store.search("CAFÉ")], ["event"])
        self.assertEqual(self.store.search("   "), [])
        self.assertEqual(self.store.search("zzzz"), [])

    def test_export_contains_everything_and_is_json(self) -> None:
        self.event()
        self.store.create_task({"title": "t"})
        n = self.store.create_note({"title": "n", "content": "body"})
        self.store.create_sticky({"content": "s"})
        data = json.loads(json.dumps(self.store.export_all()))
        self.assertEqual(data["format"], "splanner-export/1")
        self.assertEqual((len(data["events"]), len(data["tasks"]), len(data["stickies"])), (1, 1, 1))
        self.assertIn({"body"}, [{x["content"]} for x in data["notes"] if x["id"] == n["id"]])

    def test_backup_is_a_coherent_database_copy(self) -> None:
        self.event(title="Kept")
        path = self.store.backup("manual")
        copy = sqlite3.connect(str(path))
        self.assertEqual(copy.execute("SELECT title FROM events").fetchall(), [("Kept",)])
        copy.close()


class LegacyUpgradeTests(unittest.TestCase):
    """A database created by SPlanner 0.1 (no version marker, no new columns) opens, keeps every row and gains the new features."""

    def test_v01_database_is_upgraded_in_place_after_a_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "notes").mkdir()
            module = slai.sldc()
            for note_id, text in (("a" * 32, "Legacy note text"), ("b" * 32, "Legacy sticky text")):
                module.write_package(data / "notes" / f"{note_id}.sldc",
                                     module.new_package("T" + note_id[:1], [module.make_block("paragraph", text)], doc_id=f"splanner-{note_id}"))
            conn = sqlite3.connect(data / "planner.sqlite3")
            for statement in store_module.V1:
                conn.execute(statement)
            now = "2026-10-01T10:00:00+00:00"
            conn.execute("INSERT INTO notes VALUES (?,?,?,?,?)", ("a" * 32, "Ta", 3, now, now))
            conn.execute("INSERT INTO notes VALUES (?,?,?,?,?)", ("b" * 32, "Tb", 1, now, now))
            conn.execute("INSERT INTO stickies VALUES (?,?,?,?,?,?)", ("c" * 32, "b" * 32, 5, 6, "green", 1))
            conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("d" * 32, "Old event", now, "2026-10-01T11:00:00+00:00", "UTC", 0, "desc", "violet", None, 4, now, now))
            conn.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", ("e" * 32, "Old task", "done", "high", None, None, 2, now, "2026-10-02T09:00:00+00:00"))
            conn.execute("INSERT INTO reminders VALUES (?,?,?,?,?,?,?,?)", ("f" * 32, "Old reminder", now, "pending", None, None, now, now))
            conn.commit()
            conn.close()

            store = PlannerStore(data)
            try:
                self.assertEqual(store.db.schema_version, 3)
                self.assertIsNotNone(store.db.migration_backup)
                assert store.db.migration_backup is not None
                self.assertTrue(store.db.migration_backup.is_file())
                [event] = store.list_events()
                self.assertEqual((event["title"], event["version"], event["location"], event["remind_before"]), ("Old event", 4, "", None))
                [task] = store.list_tasks()
                self.assertEqual((task["status"], task["completed_at"]), ("done", "2026-10-02T09:00:00+00:00"))
                [note] = store.list_notes()                                       # the sticky-backed note is not listed as a note
                self.assertEqual((note["id"], note["excerpt"], note["version"]), ("a" * 32, "Legacy note text", 3))
                self.assertEqual(store.get_note("a" * 32)["content"], "Legacy note text")
                [sticky] = store.list_stickies()
                self.assertEqual((sticky["text"], sticky["color"], sticky["x"]), ("Legacy sticky text", "green", 5.0))
                self.assertEqual([h["kind"] for h in store.search("legacy note")], ["note"])
                self.assertEqual(store.list_reminders()[0]["title"], "Old reminder")
                self.assertEqual(store.update_event("d" * 32, {"title": "Edited", "version": 4})["version"], 5)
            finally:
                store.close()
            again = PlannerStore(data)                                            # opening twice is harmless
            self.assertEqual(again.db.migrated, [])
            again.close()


if __name__ == "__main__":
    unittest.main()
