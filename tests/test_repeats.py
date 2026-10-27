"""Repeating events, their reminders, snooze, the iCalendar export and resizable sticky notes, through the store and the real server."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from applications.splanner import store as store_module
from applications.splanner.store import PlannerStore
from applications.tools.database import Database
from applications.tools.errors import BadRequest, NotFound

from .test_api import ApiCase
from .test_store import StoreCase, iso

TODAY = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
WEEKLY = {"freq": "weekly"}


class RepeatStoreTests(StoreCase):
    def series(self, rule=None, **extra):
        start = TODAY + timedelta(days=1)
        data = {"title": "Standup", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)), "time_zone": "UTC", "recurrence": rule or WEEKLY}
        data.update(extra)
        return self.store.create_event(data)

    def window(self, days: int = 60):
        found = self.store.occurrences(iso(TODAY - timedelta(days=1)), iso(TODAY + timedelta(days=days)))
        return found["occurrences"]

    def test_a_repeating_event_keeps_its_rule_and_expands_into_occurrences(self) -> None:
        e = self.series({"freq": "weekly", "interval": 2})
        self.assertEqual(e["recurrence"], {"freq": "weekly", "interval": 2, "weekdays": [], "until": None, "count": None})
        self.assertEqual(e["exceptions"], {})
        got = self.window(60)
        self.assertEqual([o["id"] for o in got], [e["id"]] * len(got))
        gaps = {(datetime.fromisoformat(b["start_at"]) - datetime.fromisoformat(a["start_at"])).days for a, b in zip(got, got[1:])}
        self.assertEqual(gaps, {14})
        self.assertTrue(all(o["recurrence"]["interval"] == 2 and "exceptions" not in o for o in got))

    def test_events_that_do_not_repeat_are_not_expanded(self) -> None:
        self.event()
        self.assertEqual(self.window(), [])
        self.assertIsNone(self.event()["recurrence"])

    def test_the_snapshot_carries_the_occurrences_and_their_window(self) -> None:
        self.series({"freq": "daily"})
        snap = self.store.snapshot()
        self.assertGreater(len(snap["occurrences"]), 300)
        self.assertLess(snap["window"]["from"], snap["window"]["to"])

    def test_the_window_is_bounded(self) -> None:
        with self.assertRaises(BadRequest):
            self.store.occurrences(iso(TODAY), iso(TODAY + timedelta(days=2000)))
        with self.assertRaises(BadRequest):
            self.store.occurrences(iso(TODAY), iso(TODAY - timedelta(days=1)))

    def test_cancel_and_move_single_occurrences(self) -> None:
        e = self.series({"freq": "daily", "count": 5})
        first, second, third = (o["occurrence"] for o in self.window()[:3])
        moved = {"start_at": iso(TODAY + timedelta(days=9)), "end_at": iso(TODAY + timedelta(days=9, hours=2))}
        changed = self.store.update_event(e["id"], {"exceptions": {first: None, second: moved}, "version": 1})
        self.assertEqual(set(changed["exceptions"]), {first, second})
        got = self.window()
        self.assertEqual(len(got), 4)                                              # five, minus the cancelled one
        self.assertEqual([o["occurrence"] for o in got if o["moved"]], [second])
        self.assertNotIn(first, [o["occurrence"] for o in got])
        self.assertIn(third, [o["occurrence"] for o in got])
        self.assertEqual(self.store.update_event(e["id"], {"exceptions": {}, "version": 2})["exceptions"], {})
        self.assertEqual(len(self.window()), 5)

    def test_unrelated_edits_keep_single_changes_and_a_new_rule_drops_the_ones_that_no_longer_fit(self) -> None:
        e = self.series({"freq": "daily", "count": 6})
        key = self.window()[1]["occurrence"]
        e = self.store.update_event(e["id"], {"exceptions": {key: None}, "version": 1})
        e = self.store.update_event(e["id"], {"title": "Renamed", "version": e["version"]})
        self.assertEqual(set(e["exceptions"]), {key})
        e = self.store.update_event(e["id"], {"recurrence": {"freq": "weekly", "count": 3}, "version": e["version"]})        # the day is no longer part of it
        self.assertEqual(e["exceptions"], {})
        e = self.store.update_event(e["id"], {"recurrence": None, "version": e["version"]})
        self.assertIsNone(e["recurrence"])
        self.assertEqual(self.window(), [])

    def test_moving_a_series_re_checks_its_rule(self) -> None:
        e = self.series({"freq": "weekly", "weekdays": [1, 3]})
        start = datetime.fromisoformat(e["start_at"])
        self.assertIn(start.isoweekday(), e["recurrence"]["weekdays"])
        later = start + timedelta(days=1)
        moved = self.store.update_event(e["id"], {"start_at": iso(later), "end_at": iso(later + timedelta(hours=1)), "version": 1})
        self.assertIn(later.isoweekday(), moved["recurrence"]["weekdays"])
        ended = self.series({"freq": "daily", "until": (TODAY + timedelta(days=3)).date().isoformat()})
        far = datetime.fromisoformat(ended["start_at"]) + timedelta(days=10)
        with self.assertRaises(BadRequest):
            self.store.update_event(ended["id"], {"start_at": iso(far), "end_at": iso(far + timedelta(hours=1)), "version": 1})

    def test_bad_rules_are_refused_with_a_clear_message(self) -> None:
        with self.assertRaises(BadRequest) as caught:
            self.series({"freq": "fortnightly"})
        self.assertIn("recurrence.freq", str(caught.exception))
        with self.assertRaises(BadRequest):
            self.series({"freq": "daily", "until": "2000-01-01"})
        self.assertEqual(self.store.list_events(), [])

    def test_list_events_still_includes_a_repeating_event_for_a_later_window(self) -> None:
        e = self.series()
        later = TODAY + timedelta(days=400)
        self.assertEqual([x["id"] for x in self.store.list_events(iso(later), iso(later + timedelta(days=1)))], [e["id"]])

    def test_deleting_a_series_removes_its_reminder(self) -> None:
        e = self.series(remind_before=10)
        self.assertEqual(len(self.store.list_reminders()), 1)
        self.store.delete_event(e["id"])
        self.assertEqual(self.store.list_reminders(), [])


class RepeatReminderTests(StoreCase):
    def daily(self, remind_before: int, **extra):
        start = TODAY - timedelta(days=5) + timedelta(hours=3)
        data = {"title": "Pills", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)), "time_zone": "UTC",
                "recurrence": {"freq": "daily"}, "remind_before": remind_before}
        data.update(extra)
        return self.store.create_event(data)

    def reminder(self):
        [r] = self.store.list_reminders()
        return r

    def test_the_reminder_points_at_the_next_occurrence(self) -> None:
        e = self.daily(10)
        r = self.reminder()
        nxt = store_module.recurrence.next_occurrence(self.store.get_event(e["id"]), iso(datetime.now(timezone.utc)))
        assert nxt is not None
        self.assertEqual((r["status"], r["occurrence_at"]), ("pending", nxt["occurrence"]))
        self.assertEqual(r["due_at"], iso(datetime.fromisoformat(nxt["start_at"]) - timedelta(minutes=10)))

    def test_dismissing_it_moves_on_to_the_next_occurrence_instead_of_ending(self) -> None:
        self.daily(10)
        before = self.reminder()
        after = self.store.change_reminder(before["id"], {"action": "dismiss"})
        self.assertEqual(after["status"], "pending")
        self.assertEqual(datetime.fromisoformat(after["occurrence_at"]) - datetime.fromisoformat(before["occurrence_at"]), timedelta(days=1))
        self.assertGreater(after["due_at"], iso(datetime.now(timezone.utc)))

    def test_a_long_lead_time_does_not_bring_an_alert_straight_back(self) -> None:
        self.daily(1440)                                                       # a day ahead: the next occurrence's alert time is already past
        r = self.reminder()
        self.assertLessEqual(r["due_at"], iso(datetime.now(timezone.utc)))
        after = self.store.change_reminder(r["id"], {"action": "dismiss"})
        self.assertGreater(after["due_at"], iso(datetime.now(timezone.utc)))
        self.assertEqual(self.store.due_reminders(), [])

    def test_snooze_keeps_the_occurrence(self) -> None:
        self.daily(10)
        r = self.reminder()
        snoozed = self.store.change_reminder(r["id"], {"action": "snooze", "minutes": 5})
        self.assertEqual(snoozed["occurrence_at"], r["occurrence_at"])
        self.assertGreater(snoozed["due_at"], iso(datetime.now(timezone.utc)))

    def test_an_alert_nobody_dealt_with_gives_way_to_the_next_occurrence(self) -> None:
        self.daily(1440)
        r = self.reminder()
        stale = iso(datetime.fromisoformat(r["occurrence_at"]) - timedelta(days=1))
        self.store.db.execute("UPDATE reminders SET occurrence_at=?, due_at=? WHERE id=?", (stale, iso(datetime.fromisoformat(stale) - timedelta(days=1)), r["id"]))
        [due] = self.store.due_reminders()
        self.assertEqual((due["id"], due["occurrence_at"]), (r["id"], r["occurrence_at"]))        # back on the next occurrence
        self.assertEqual(due["due_at"], r["due_at"])

    def test_a_series_that_has_ended_has_nothing_left_to_alert_about(self) -> None:
        start = TODAY - timedelta(days=5)
        e = self.store.create_event({"title": "Done", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)), "time_zone": "UTC",
                                     "recurrence": {"freq": "daily", "count": 3}, "remind_before": 10})
        self.assertEqual(self.store.list_reminders(), [])
        self.store.update_event(e["id"], {"recurrence": {"freq": "daily"}, "version": 1})
        self.assertEqual(self.reminder()["status"], "pending")
        self.store.update_event(e["id"], {"recurrence": {"freq": "daily", "count": 2}, "version": 2})
        self.assertEqual(self.reminder()["status"], "dismissed")

    def test_cancelling_the_next_occurrence_moves_the_alert_to_the_one_after(self) -> None:
        e = self.daily(10)
        before = self.reminder()
        self.store.update_event(e["id"], {"exceptions": {before["occurrence_at"]: None}, "version": 1})
        after = self.reminder()
        self.assertEqual(datetime.fromisoformat(after["occurrence_at"]) - datetime.fromisoformat(before["occurrence_at"]), timedelta(days=1))

    def test_one_off_events_behave_as_before(self) -> None:
        start = TODAY + timedelta(days=2)
        self.store.create_event({"title": "Once", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)), "time_zone": "UTC", "remind_before": 10})
        r = self.reminder()
        self.assertIsNone(r["occurrence_at"])
        self.assertEqual(self.store.change_reminder(r["id"], {"action": "dismiss"})["status"], "dismissed")


class SnoozeStoreTests(StoreCase):
    def test_every_quick_choice_and_a_custom_length_work(self) -> None:
        for minutes in (1, 5, 10, 15, 30, 60, 45, 1440, 43_200):
            r = self.store.create_reminder({"title": "Ping", "due_at": iso(datetime.now(timezone.utc) - timedelta(minutes=1))})
            snoozed = self.store.change_reminder(r["id"], {"action": "snooze", "minutes": minutes})
            lead = datetime.fromisoformat(snoozed["due_at"]) - datetime.now(timezone.utc)
            with self.subTest(minutes=minutes):
                self.assertEqual(snoozed["status"], "pending")
                self.assertLess(abs(lead - timedelta(minutes=minutes)), timedelta(seconds=5))

    def test_unreasonable_lengths_are_refused(self) -> None:
        r = self.store.create_reminder({"title": "Ping", "due_at": iso(datetime.now(timezone.utc) + timedelta(hours=1))})
        for bad in (0, -5, 43_201, 1.5, "10", True):
            with self.subTest(minutes=bad), self.assertRaises(BadRequest):
                self.store.change_reminder(r["id"], {"action": "snooze", "minutes": bad})


class IcsRepeatTests(StoreCase):
    def test_a_repeating_event_is_exported_as_a_rule_with_its_single_changes(self) -> None:
        start = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)                                  # 09:00 in Amsterdam
        e = self.store.create_event({"title": "Sync, weekly", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)), "time_zone": "Europe/Amsterdam",
                                     "recurrence": {"freq": "weekly", "interval": 2, "count": 6}})
        keys = [o["occurrence"] for o in self.store.occurrences(iso(start), iso(start + timedelta(days=120)))["occurrences"]]
        moved = {"start_at": "2026-11-18T10:00:00+00:00", "end_at": "2026-11-18T11:00:00+00:00"}
        self.store.update_event(e["id"], {"exceptions": {keys[1]: None, keys[2]: moved}, "version": 1})
        text = self.store.export_ics().replace("\r\n ", "")
        lines = text.split("\r\n")
        self.assertIn("DTSTART;TZID=Europe/Amsterdam:20261103T090000", lines)
        self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=6", lines)
        self.assertIn("EXDATE:" + keys[1][:19].replace("-", "").replace(":", "") + "Z", lines)
        self.assertNotIn("EXDATE:" + keys[2][:19].replace("-", "").replace(":", "") + "Z", lines)    # a moved one is replaced, not removed
        self.assertIn("RECURRENCE-ID:" + keys[2][:19].replace("-", "").replace(":", "") + "Z", lines)
        self.assertIn("DTSTART:20261118T100000Z", lines)
        self.assertEqual(lines.count("BEGIN:VEVENT"), 2)
        self.assertEqual(len({line for line in lines if line.startswith("UID:")}), 1)

    def test_an_all_day_repeat_uses_dates(self) -> None:
        self.store.create_event({"title": "Trash", "start_at": "2026-11-02T00:00:00+00:00", "end_at": "2026-11-03T00:00:00+00:00", "time_zone": "UTC", "all_day": True,
                                 "recurrence": {"freq": "monthly", "interval": 3, "until": "2027-12-31"}})
        lines = self.store.export_ics().split("\r\n")
        self.assertIn("DTSTART;VALUE=DATE:20261102", lines)
        self.assertIn("RRULE:FREQ=MONTHLY;INTERVAL=3;UNTIL=20271231", lines)


class StickySizeStoreTests(StoreCase):
    def test_a_sticky_can_be_resized_and_reset(self) -> None:
        s = self.store.create_sticky({"content": "hello"})
        self.assertEqual((s["width"], s["height"]), (None, None))
        s = self.store.update_sticky(s["id"], {"width": 320, "height": 260.5, "version": 1})
        self.assertEqual((s["width"], s["height"], s["version"]), (320.0, 260.5, 2))
        self.assertEqual((self.store.list_stickies()[0]["width"], self.store.list_stickies()[0]["height"]), (320.0, 260.5))
        s = self.store.update_sticky(s["id"], {"x": 10, "version": 2})                              # moving keeps the size
        self.assertEqual((s["width"], s["height"]), (320.0, 260.5))
        s = self.store.update_sticky(s["id"], {"width": None, "height": None, "version": 3})
        self.assertEqual((s["width"], s["height"]), (None, None))
        self.assertEqual(self.store.create_sticky({"width": 400, "height": 300})["width"], 400.0)

    def test_sizes_have_limits(self) -> None:
        s = self.store.create_sticky({})
        for bad in ({"width": 10}, {"height": 10}, {"width": 5000}, {"height": -1}, {"width": "wide"}, {"width": float("nan")}):
            with self.subTest(bad=bad), self.assertRaises(BadRequest):
                self.store.update_sticky(s["id"], {**bad, "version": 1})

    def test_the_text_keeps_its_line_breaks_when_only_the_size_changes(self) -> None:
        s = self.store.create_sticky({"content": "one\ntwo"})
        s = self.store.update_sticky(s["id"], {"width": 300, "version": 1})
        self.assertEqual(self.store.get_sticky(s["id"])["text"], "one\ntwo")
        self.assertEqual(self.store.export_all()["stickies"][0]["width"], 300.0)


class UpgradeFromVersionTwoTests(unittest.TestCase):
    def test_a_1_0_database_gains_the_new_columns_and_keeps_its_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "notes").mkdir()
            db = Database(data / "planner.sqlite3", store_module.migrations(data / "notes")[:2], backups_dir=data / "backups")
            now = "2026-10-01T10:00:00+00:00"
            db.execute("INSERT INTO events (id,title,start_at,end_at,time_zone,all_day,description,color,note_id,version,created_at,updated_at,location,remind_before) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("a" * 32, "Old", now, "2026-10-01T11:00:00+00:00", "UTC", 0, "", "blue", None, 2, now, now, "", 15))
            db.execute("INSERT INTO reminders (id,title,due_at,status,entity_type,entity_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       ("b" * 32, "Old", "2026-09-30T23:45:00+00:00", "dismissed", "event", "a" * 32, now, now))
            db.close()
            store = PlannerStore(data)
            try:
                self.assertEqual((store.db.schema_version, store.db.migrated), (3, [3]))
                self.assertIsNotNone(store.db.migration_backup)
                [event] = store.list_events()
                self.assertEqual((event["title"], event["version"], event["recurrence"], event["exceptions"]), ("Old", 2, None, {}))
                self.assertIsNone(store.list_reminders()[0]["occurrence_at"])
                s = store.create_sticky({"content": "x", "width": 200})
                self.assertEqual(s["width"], 200.0)
            finally:
                store.close()
            conn = sqlite3.connect(data / "planner.sqlite3")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            conn.close()


class RepeatApiTests(ApiCase):
    def test_the_whole_flow_over_http(self) -> None:
        start = TODAY + timedelta(days=1)
        body = {"title": "Sprint review", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)), "time_zone": "Europe/Amsterdam",
                "recurrence": {"freq": "monthly", "interval": 3, "month_mode": "weekday"}, "remind_before": 15}
        event = self.data("POST", "/api/events", body, 201)
        self.assertEqual(event["recurrence"]["interval"], 3)
        got = self.data("GET", f"/api/occurrences?from={iso(start - timedelta(days=1)).replace('+', '%2B')}&to={iso(start + timedelta(days=400)).replace('+', '%2B')}")
        self.assertEqual(len(got["occurrences"]), 5)
        self.assertEqual(got["window"]["from"], iso(start - timedelta(days=1)))
        key = got["occurrences"][1]["occurrence"]
        patched = self.data("PATCH", f"/api/events/{event['id']}", {"exceptions": {key: None}, "version": 1})
        self.assertEqual(list(patched["exceptions"]), [key])
        self.assertEqual(len(self.data("GET", "/api/snapshot")["data"]["occurrences"]), 4)
        self.assertEqual(self.error("PATCH", f"/api/events/{event['id']}", {"recurrence": {"freq": "never"}, "version": 2})["code"], "bad_request")
        self.assertEqual(self.error("GET", "/api/occurrences?from=nonsense")["code"], "bad_request")
        self.assertIn("RRULE:FREQ=MONTHLY;INTERVAL=3;BYDAY=", self.call("GET", "/api/export/ics", raw=True)[1].decode().replace("\r\n ", ""))
        self.data("DELETE", f"/api/events/{event['id']}")

    def test_snooze_and_sticky_size_over_http(self) -> None:
        reminder = self.data("POST", "/api/reminders", {"title": "Ping", "due_at": iso(datetime.now(timezone.utc) - timedelta(minutes=1))}, 201)
        snoozed = self.data("PATCH", f"/api/reminders/{reminder['id']}", {"action": "snooze", "minutes": 45})
        self.assertGreater(snoozed["due_at"], iso(datetime.now(timezone.utc) + timedelta(minutes=44)))
        sticky = self.data("POST", "/api/stickies", {"content": "wide"}, 201)
        sticky = self.data("PATCH", f"/api/stickies/{sticky['id']}", {"width": 333, "height": 222, "version": 1})
        self.assertEqual((sticky["width"], sticky["height"]), (333.0, 222.0))
        self.assertEqual(self.error("PATCH", f"/api/stickies/{sticky['id']}", {"width": 1, "version": 2})["code"], "bad_request")
        with self.assertRaises(NotFound):
            self.app.store.get_event("0" * 32)


if __name__ == "__main__":
    unittest.main()
