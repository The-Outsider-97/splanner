"""Workflow E - reminders and alerts: a due reminder is announced once, can be dismissed or snoozed, an event's reminder follows the
event, and creating one by hand works from Today and from the bell."""
from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect

from harness import E2E, iso


def now() -> datetime:
    return datetime.now(timezone.utc)


class Reminders(E2E):
    def reminders(self) -> list[dict]:
        return self.api("GET", "/api/reminders")

    def bell(self):
        return self.page.get_by_role("button", name=re.compile(r"^Reminders"))

    def test_a_due_reminder_is_announced_once_with_the_bell_count(self) -> None:
        self.api("POST", "/api/reminders", {"title": "Call mum", "due_at": iso(now() - timedelta(minutes=1))}, status=201)
        self.open("#/today")
        pg = self.page
        expect(pg.locator(".toast", has_text="Call mum")).to_be_visible(timeout=8000)
        expect(self.bell()).to_have_accessible_name("Reminders, 1 due")
        expect(pg.locator(".bubble")).to_have_text("1")
        pg.wait_for_timeout(2500)                                                           # the poll runs again; no second toast
        self.assertEqual(pg.locator(".toast", has_text="Call mum").count(), 1)
        self.shot("reminders-due")

    def test_dismiss_from_the_dialog(self) -> None:
        self.api("POST", "/api/reminders", {"title": "Call mum", "due_at": iso(now() - timedelta(minutes=1))}, status=201)
        self.open("#/today")
        pg = self.page
        self.bell().click()
        dialog = pg.get_by_role("dialog", name="Reminders")
        dialog.get_by_role("button", name="Actions for Call mum").click()
        pg.get_by_role("menuitem", name="Dismiss").click()
        expect(self.bell()).to_have_accessible_name("Reminders")
        self.assertEqual(self.reminders()[0]["status"], "dismissed")

    def test_snooze_moves_the_time_forward(self) -> None:
        self.api("POST", "/api/reminders", {"title": "Stand up", "due_at": iso(now() - timedelta(minutes=1))}, status=201)
        self.open("#/today")
        pg = self.page
        self.bell().click()
        pg.get_by_role("dialog", name="Reminders").get_by_role("button", name="Actions for Stand up").click()
        pg.get_by_role("menuitem", name="Snooze +10 min").click()
        for _ in range(30):
            due = datetime.fromisoformat(self.reminders()[0]["due_at"])
            if due > now():
                break
            pg.wait_for_timeout(100)
        self.assertGreater(due, now() + timedelta(minutes=8))
        self.assertEqual(self.reminders()[0]["status"], "pending")
        expect(self.bell()).to_have_accessible_name("Reminders")

    def test_add_an_alert_from_today(self) -> None:
        self.open("#/today")
        pg = self.page
        pg.get_by_role("button", name="Add alert").click()
        dialog = pg.get_by_role("dialog", name=re.compile("reminder|alert", re.I))
        dialog.get_by_role("textbox", name="Title").fill("Water the plants")
        dialog.get_by_role("button", name=re.compile("^(Add|Save|Create)")).last.click()
        expect(pg.locator(".reminder-row", has_text="Water the plants")).to_be_visible()
        self.assertEqual([r["title"] for r in self.reminders()], ["Water the plants"])

    def test_an_events_reminder_follows_the_event(self) -> None:
        start = (now() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        event = self.api("POST", "/api/events", {"title": "Workshop", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)),
                                                 "remind_before": 30}, status=201)
        [reminder] = self.reminders()
        self.assertEqual(datetime.fromisoformat(reminder["due_at"]), start - timedelta(minutes=30))
        self.api("PATCH", f"/api/events/{event['id']}", {"start_at": iso(start + timedelta(hours=2)), "end_at": iso(start + timedelta(hours=3)), "version": 1})
        [reminder] = self.reminders()
        self.assertEqual(datetime.fromisoformat(reminder["due_at"]), start + timedelta(hours=1, minutes=30))
        self.api("DELETE", f"/api/events/{event['id']}")
        self.assertEqual(self.reminders(), [])

    def test_a_reminder_made_in_the_event_dialog_is_listed_on_today(self) -> None:
        self.open("#/calendar")
        pg = self.page
        pg.get_by_role("button", name="Add event").click()
        dialog = pg.get_by_role("dialog", name="New event")
        dialog.get_by_role("textbox", name="Title").fill("Project check-in")
        dialog.get_by_label("Starts date").fill((now() + timedelta(days=1)).strftime("%Y-%m-%d"))
        dialog.get_by_label("Starts time").fill("14:00")
        dialog.get_by_label("Ends date").fill((now() + timedelta(days=1)).strftime("%Y-%m-%d"))
        dialog.get_by_label("Ends time").fill("15:00")
        dialog.get_by_label("Reminder").select_option("15")
        dialog.get_by_role("button", name="Create event").click()
        expect(pg.locator(".timegrid .ev", has_text="Project check-in")).to_be_visible()
        [reminder] = self.reminders()
        self.assertTrue(reminder["due_at"].endswith("13:45:00+00:00"), reminder["due_at"])


if __name__ == "__main__":
    unittest.main()
