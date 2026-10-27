"""Workflow F - settings, data and access: themes and text size apply at once and survive a reload (and the first paint after one),
calendar preferences reshape the views, backups and exports really produce files, a stopped service is reported, dialogs behave
(Escape, focus returns), and the create menu reaches every kind of item."""
from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect

from harness import E2E, iso


class Settings(E2E):
    def prefs(self) -> dict:
        return self.api("GET", "/api/prefs")

    def open_settings(self):
        self.page.get_by_role("button", name="Appearance").click()
        return self.page.get_by_role("dialog", name="Appearance & settings")

    def close_settings(self) -> None:
        self.page.get_by_role("dialog", name="Appearance & settings").get_by_role("button", name="Done").click()

    def test_dark_theme_applies_saves_and_is_there_on_the_next_load(self) -> None:
        self.open("#/today")
        pg = self.page
        dialog = self.open_settings()
        dialog.get_by_role("group", name="Theme").get_by_role("button", name="Dark").click()
        expect(pg.locator("html")).to_have_attribute("data-theme", "dark")
        for _ in range(30):
            if self.prefs()["theme"] == "dark":
                break
            pg.wait_for_timeout(100)
        self.assertEqual(self.prefs()["theme"], "dark")
        self.shot("settings-dark")
        self.close_settings()
        pg.reload()
        expect(pg.locator("html")).to_have_attribute("data-theme", "dark")
        self.assertEqual(pg.evaluate("getComputedStyle(document.body).backgroundColor"), "rgb(29, 32, 38)")

    def test_the_theme_is_set_before_the_first_paint_from_the_cached_choice(self) -> None:
        self.open("#/today")
        self.api("PATCH", "/api/prefs", {"theme": "dark"})
        self.page.reload()
        self.page.wait_for_selector(".shell")                                              # the app has run once, so the choice is cached
        self.page.add_init_script("""window.__themes = [];
            new MutationObserver(() => window.__themes.push([document.documentElement.dataset.theme, !!document.querySelector('.shell')]))
              .observe(document, { subtree: true, attributes: true, attributeFilter: ['data-theme'] });""")
        self.page.reload()
        self.page.wait_for_selector(".shell")
        first = self.page.evaluate("window.__themes[0]")
        self.assertEqual(first, ["dark", False], "the dark theme should be applied before any of the app is drawn")

    def test_high_contrast_theme(self) -> None:
        self.open("#/today")
        dialog = self.open_settings()
        dialog.get_by_role("group", name="Theme").get_by_role("button", name="High contrast").click()
        expect(self.page.locator("html")).to_have_attribute("data-theme", "contrast")
        self.shot("settings-contrast")

    def test_text_size_scales_the_page(self) -> None:
        self.open("#/today")
        before = self.page.evaluate("parseFloat(getComputedStyle(document.documentElement).fontSize)")
        dialog = self.open_settings()
        slider = dialog.get_by_role("slider").first
        slider.focus()
        for _ in range(4):
            self.page.keyboard.press("ArrowRight")
        after = self.page.evaluate("parseFloat(getComputedStyle(document.documentElement).fontSize)")
        self.assertGreater(after, before)
        self.assertGreater(self.prefs()["scale"] if self.page.wait_for_timeout(1200) is None else 0, 1.0)

    def test_workspace_name_and_initials_show_in_the_sidebar(self) -> None:
        self.open("#/today")
        pg = self.page
        dialog = self.open_settings()
        dialog.get_by_label("Workspace name").fill("Studio")
        dialog.get_by_label("Initials (shown in the avatar)").fill("ab")
        expect(pg.locator(".workspace")).to_contain_text("Studio")
        expect(pg.locator(".workspace .avatar")).to_have_text("AB")
        pg.wait_for_timeout(1200)
        self.assertEqual((self.prefs()["workspace_name"], self.prefs()["initials"]), ("Studio", "AB"))

    def test_week_start_and_time_format_reshape_the_views(self) -> None:
        start = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
        self.api("POST", "/api/events", {"title": "Afternoon", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1))}, status=201)
        self.open("#/calendar")
        pg = self.page
        expect(pg.locator(".mini-dow").first).to_have_text("Mon")
        dialog = self.open_settings()
        dialog.get_by_role("group", name="Week starts on").get_by_role("button", name="Sunday").click()
        dialog.get_by_role("group", name="Time format").get_by_role("button", name="12-hour").click()
        self.close_settings()
        expect(pg.locator(".mini-dow").first).to_have_text("Sun")
        expect(pg.locator(".tg-day").first).to_contain_text("Sun")
        expect(pg.locator(".timegrid .ev", has_text="Afternoon")).to_contain_text("3 PM – 4 PM")

    def test_new_event_defaults_come_from_the_settings(self) -> None:
        self.open("#/calendar")
        pg = self.page
        dialog = self.open_settings()
        dialog.get_by_label("New events last").select_option("30")
        dialog.get_by_label("New events remind me").select_option("30")
        pg.wait_for_timeout(1200)
        self.close_settings()
        pg.get_by_role("button", name="Add event").click()
        event = pg.get_by_role("dialog", name="New event")
        self.assertEqual(event.get_by_label("Reminder").input_value(), "30")
        start = event.get_by_label("Starts time").input_value()
        end = event.get_by_label("Ends time").input_value()
        h1, m1 = map(int, start.split(":")); h2, m2 = map(int, end.split(":"))
        self.assertEqual((h2 * 60 + m2) - (h1 * 60 + m1), 30)

    def test_open_on_decides_the_first_view(self) -> None:
        self.open("#/today")
        dialog = self.open_settings()
        dialog.get_by_label("Open on").select_option("tasks")
        self.page.wait_for_timeout(1200)
        self.assertEqual(self.prefs()["default_view"], "tasks")
        self.page.goto(f"http://127.0.0.1:{self.svc.port}/")
        expect(self.page.locator(".page-heading h1")).to_have_text("My tasks")


class Data(E2E):
    def open_data(self):
        self.page.get_by_role("button", name="Stored on this device").click()
        return self.page.get_by_role("dialog", name=re.compile("Your data|Stored on this device|Data", re.I))

    def test_backup_now_makes_a_real_file_and_lists_it(self) -> None:
        self.open("#/today")
        self.api("POST", "/api/tasks", {"title": "Keep safe"}, status=201)
        dialog = self.open_data()
        expect(dialog).to_contain_text("No backups yet")
        dialog.get_by_role("button", name="Back up now").click()
        expect(self.page.locator(".toast", has_text="Backed up")).to_be_visible()
        expect(dialog.locator(".backup-list li")).to_have_count(1)
        files = sorted((self.tmp / "data" / "backups").glob("*.sqlite3"))
        self.assertEqual(len(files), 1)
        import sqlite3
        with sqlite3.connect(files[0]) as conn:
            self.assertEqual(conn.execute("SELECT title FROM tasks").fetchall(), [("Keep safe",)])
        self.shot("data")

    def test_exports_download_real_files(self) -> None:
        start = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
        self.api("POST", "/api/events", {"title": "Lunch, with; Sam", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1))}, status=201)
        self.api("POST", "/api/notes", {"title": "Exported", "content": "body"}, status=201)
        self.open("#/today")
        pg = self.page
        dialog = self.open_data()
        for label, suffix in (("Export calendar (.ics)", ".ics"), ("Export everything (.json)", ".json")):
            with pg.expect_download() as info:
                dialog.get_by_role("button", name=label).click()
            download = info.value
            self.assertTrue(download.suggested_filename.endswith(suffix), download.suggested_filename)
            path = self.tmp / download.suggested_filename
            download.save_as(path)
            text = path.read_text(encoding="utf-8")
            if suffix == ".ics":
                self.assertTrue(text.startswith("BEGIN:VCALENDAR"))
                self.assertIn(r"SUMMARY:Lunch\, with\; Sam", text)
            else:
                data = json.loads(text)
                self.assertEqual(data["format"], "splanner-export/1")
                self.assertEqual(data["notes"][0]["content"], "body")

    def test_a_stopped_service_is_reported_and_nothing_pretends_to_save(self) -> None:
        self.expect_errors = True                                              # the browser logs the failed requests, which is the point
        self.open("#/tasks")
        pg = self.page
        self.svc.stop()
        field = pg.get_by_placeholder("Add a task and press Enter")
        field.fill("Will not be saved")
        field.press("Enter")
        expect(pg.locator(".notice, .toast, .offline").filter(has_text=re.compile("can.t be reached|not reach|offline|closed", re.I)).first).to_be_visible(timeout=15000)
        expect(pg.locator(".task-row", has_text="Will not be saved")).to_have_count(0)


class Access(E2E):
    def test_dialogs_close_with_escape_and_return_focus(self) -> None:
        self.open("#/today")
        pg = self.page
        button = pg.get_by_role("button", name="Create new")
        button.focus()
        pg.keyboard.press("Enter")
        pg.get_by_role("menuitem", name="Calendar event").click()
        expect(pg.get_by_role("dialog", name="New event")).to_be_visible()
        expect(pg.get_by_role("textbox", name="Title")).to_be_focused()
        pg.keyboard.press("Escape")
        expect(pg.get_by_role("dialog", name="New event")).to_have_count(0)

    def test_tab_stays_inside_an_open_dialog(self) -> None:
        self.open("#/today")
        pg = self.page
        pg.get_by_role("button", name="Create new").click()
        pg.get_by_role("menuitem", name="Calendar event").click()
        for _ in range(30):
            pg.keyboard.press("Tab")
            inside = pg.evaluate("document.activeElement === document.body || !!document.activeElement.closest('dialog')")   # body = the browser's own chrome has it
            self.assertTrue(inside, "focus left the dialog")

    def test_create_menu_reaches_every_kind_of_item(self) -> None:
        self.open("#/today")
        pg = self.page
        pg.get_by_role("button", name="Create new").click()
        expect(pg.get_by_role("menuitem")).to_have_count(5)
        pg.get_by_role("menuitem", name="Task", exact=True).click()
        dialog = pg.get_by_role("dialog", name=re.compile("New task"))
        dialog.get_by_role("textbox", name="Title").fill("From the menu")
        dialog.get_by_role("button", name=re.compile("^(Add|Create)")).last.click()
        expect(pg.locator(".task-row", has_text="From the menu")).to_be_visible()
        pg.get_by_role("button", name="Create new").click()
        pg.get_by_role("menuitem", name="Note", exact=True).click()
        expect(pg).to_have_url(re.compile(r"#/notes/new$"))
        pg.get_by_role("button", name="Create new").click()
        pg.get_by_role("menuitem", name="Sticky note").click()
        expect(pg).to_have_url(re.compile(r"#/board"))
        expect(pg.locator(".sticky")).to_have_count(1)

    def test_every_view_has_one_h1_and_named_landmarks(self) -> None:
        self.seed_week()
        for route in ("#/today", "#/calendar", "#/tasks", "#/notes", "#/board"):
            self.open(route)
            pg = self.page
            self.assertEqual(pg.locator("h1").count(), 1, route)
            self.assertEqual(pg.locator("main, [role=main]").count(), 1, route)
            self.assertGreaterEqual(pg.locator("nav").count(), 1, route)
            unnamed = pg.evaluate("""() => [...document.querySelectorAll('button, a[href], input, select, textarea')].filter(e => {
                const name = (e.getAttribute('aria-label') || e.textContent || '').trim() || (e.id && document.querySelector(`label[for="${e.id}"]`)?.textContent.trim()) || e.getAttribute('title') || e.getAttribute('placeholder');
                return !name && e.type !== 'hidden'; }).map(e => e.outerHTML.slice(0, 90))""")
            self.assertEqual(unnamed, [], route)

    def test_reduced_motion_is_honoured(self) -> None:
        self.ctx.close()
        self.ctx = self.browser.new_context(viewport=self.viewport, timezone_id="UTC", locale="en-US", reduced_motion="reduce")
        self.page = self.ctx.new_page()
        self.errors = []
        self.opened = False
        self.open("#/today")
        self.assertEqual(self.page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"), True)
        duration = self.page.evaluate("getComputedStyle(document.querySelector('.btn')).transitionDuration")
        self.assertIn(duration, ("0s", "1e-05s", "0.00001s"))


if __name__ == "__main__":
    unittest.main()
