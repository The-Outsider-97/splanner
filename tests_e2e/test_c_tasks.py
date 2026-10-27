"""Workflow C - tasks: add with Enter, tick and untick, edit, due dates and grouping, filter, clear completed, delete, and the
priorities card on Today staying in step."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect

from harness import E2E, iso


class Tasks(E2E):
    def tasks(self) -> list[dict]:
        return self.api("GET", "/api/tasks")

    def row(self, title: str):
        return self.page.locator(".task-row", has_text=title).first

    def test_quick_add_with_enter_keeps_focus_for_the_next_one(self) -> None:
        self.open("#/tasks")
        pg = self.page
        field = pg.get_by_placeholder("Add a task and press Enter")
        field.fill("Buy milk")
        field.press("Enter")
        expect(self.row("Buy milk")).to_be_visible()
        expect(field).to_be_focused()
        expect(field).to_have_value("")
        field.fill("Call the bank")
        field.press("Enter")
        expect(self.row("Call the bank")).to_be_visible()
        self.assertEqual(sorted(t["title"] for t in self.tasks()), ["Buy milk", "Call the bank"])
        self.assertEqual(pg.locator(".page-heading .subtitle").inner_text(), "2 open · 0 completed")

    def test_blank_quick_add_does_nothing(self) -> None:
        self.open("#/tasks")
        field = self.page.get_by_placeholder("Add a task and press Enter")
        field.fill("   ")
        field.press("Enter")
        self.assertEqual(self.tasks(), [])

    def test_ticking_moves_a_task_to_done_and_back(self) -> None:
        self.api("POST", "/api/tasks", {"title": "Write report", "priority": "high"}, status=201)
        self.open("#/tasks")
        pg = self.page
        pg.get_by_role("checkbox", name="Mark done: Write report").click()      # the row leaves the list at once, so no check() (it re-reads the box)
        for _ in range(30):
            if self.tasks()[0]["status"] == "done":
                break
            pg.wait_for_timeout(100)
        self.assertEqual(self.tasks()[0]["status"], "done")
        self.assertIsNotNone(self.tasks()[0]["completed_at"])
        expect(pg.locator(".task-row")).to_have_count(0)                       # the default filter shows open tasks
        pg.get_by_role("group", name="Show").get_by_role("button", name="Done").click() if pg.get_by_role("group", name="Show").count() else pg.locator(".seg button", has_text="Done").click()
        expect(self.row("Write report")).to_have_class(__import__("re").compile("is-done"))
        pg.get_by_role("checkbox", name="Mark open: Write report").click()
        for _ in range(30):
            if self.tasks()[0]["status"] == "open":
                break
            pg.wait_for_timeout(100)
        self.assertEqual(self.tasks()[0]["status"], "open")
        self.assertIsNone(self.tasks()[0]["completed_at"])

    def test_edit_a_task_with_a_due_date(self) -> None:
        self.api("POST", "/api/tasks", {"title": "Draft", "priority": "normal"}, status=201)
        self.open("#/tasks")
        pg = self.page
        self.row("Draft").get_by_role("button", name="Draft", exact=True).click()
        dialog = pg.get_by_role("dialog", name="Edit task")
        dialog.get_by_role("textbox", name="Title").fill("Draft the plan")
        dialog.get_by_label("Priority").select_option("high")
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        dialog.get_by_label("Due date").fill(tomorrow)
        dialog.get_by_role("textbox", name="Details").fill("Outline first.")
        dialog.get_by_role("button", name="Save changes").click()
        expect(self.row("Draft the plan")).to_contain_text("Tomorrow")
        stored = self.tasks()[0]
        self.assertEqual((stored["title"], stored["priority"], stored["description"]), ("Draft the plan", "high", "Outline first."))
        self.assertTrue(stored["due_at"].startswith(tomorrow))                   # a date alone means the end of that day
        self.assertTrue(stored["due_at"].endswith("23:59:00+00:00"))

    def test_groups_follow_the_due_date(self) -> None:
        today = datetime.now(timezone.utc).replace(hour=23, minute=59, second=0, microsecond=0)       # "today" means by the end of today
        for title, due in (("Late", today - timedelta(days=3)), ("Now", today), ("Soon", today + timedelta(days=2)), ("Whenever", None)):
            self.api("POST", "/api/tasks", {"title": title, "due_at": iso(due) if due else None}, status=201)
        self.open("#/tasks")
        titles = [t.strip() for t in self.page.locator(".group .group-title, .group .group-toggle").all_inner_texts()]
        self.assertEqual([t.split(" · ")[0] for t in titles], ["Overdue", "Today", "Upcoming", "No due date"])
        self.shot("tasks")

    def test_priority_filter_and_text_filter(self) -> None:
        for title, priority in (("Alpha plan", "high"), ("Beta plan", "normal"), ("Gamma errand", "low")):
            self.api("POST", "/api/tasks", {"title": title, "priority": priority}, status=201)
        self.open("#/tasks")
        pg = self.page
        pg.locator(".filters .chip", has_text="High").click()
        expect(pg.locator(".task-row")).to_have_count(1)
        pg.locator(".filters .chip", has_text="Any priority").click()
        expect(pg.locator(".task-row")).to_have_count(3)
        pg.get_by_placeholder("Filter tasks").fill("plan")
        expect(pg.locator(".task-row")).to_have_count(2)
        pg.get_by_placeholder("Filter tasks").fill("nothing like this")
        expect(pg.locator(".task-row")).to_have_count(0)
        expect(pg.locator(".empty-state")).to_be_visible()

    def test_row_menu_edits_and_deletes(self) -> None:
        self.api("POST", "/api/tasks", {"title": "Throw away"}, status=201)
        self.open("#/tasks")
        pg = self.page
        self.row("Throw away").get_by_role("button", name="Actions for Throw away").click()
        pg.get_by_role("menuitem", name="Delete").click()
        pg.get_by_role("dialog", name="Delete this task?").get_by_role("button", name="Delete").click()
        expect(pg.locator(".task-row")).to_have_count(0)
        self.assertEqual(self.tasks(), [])

    def test_clear_completed_removes_only_done_tasks(self) -> None:
        keep = self.api("POST", "/api/tasks", {"title": "Keep"}, status=201)
        gone = self.api("POST", "/api/tasks", {"title": "Gone"}, status=201)
        self.api("PATCH", f"/api/tasks/{gone['id']}", {"status": "done"})
        self.open("#/tasks")
        pg = self.page
        pg.locator(".seg button", has_text="Done").click()
        pg.get_by_role("button", name="Clear completed").click()
        pg.get_by_role("dialog", name="Clear completed tasks?").get_by_role("button", name="Clear", exact=True).click()
        for _ in range(30):
            if len(self.tasks()) == 1:
                break
            pg.wait_for_timeout(100)
        self.assertEqual([t["id"] for t in self.tasks()], [keep["id"]])

    def test_today_priorities_card_ticks_a_task_and_the_counter_follows(self) -> None:
        self.api("POST", "/api/tasks", {"title": "Ship it", "priority": "high"}, status=201)
        self.api("POST", "/api/tasks", {"title": "Tidy desk"}, status=201)
        self.open("#/today")
        pg = self.page
        expect(pg.locator(".stat").nth(1)).to_contain_text("2")
        pg.get_by_role("checkbox", name="Mark done: Ship it").click()
        expect(pg.locator(".stat").nth(1)).to_contain_text("1")
        expect(pg.locator(".task-row", has_text="Ship it")).to_have_count(0)

    def test_view_all_goes_to_the_task_list(self) -> None:
        self.api("POST", "/api/tasks", {"title": "Anything"}, status=201)
        self.open("#/today")
        self.page.get_by_role("button", name="View all").click() if self.page.get_by_role("button", name="View all").count() else self.page.get_by_role("link", name="View all").click()
        expect(self.page.locator(".page-heading h1")).to_have_text("My tasks")


if __name__ == "__main__":
    unittest.main()
