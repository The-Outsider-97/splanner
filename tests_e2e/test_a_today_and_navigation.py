"""Workflow A - the Today dashboard and moving around: the numbers are real, every view opens from the sidebar, the address bar follows,
Back works, and a phone-sized window turns the sidebar into a drawer."""
from __future__ import annotations

import unittest

from playwright.sync_api import expect

from harness import E2E


class Today(E2E):
    def test_dashboard_shows_the_real_numbers(self) -> None:
        self.seed_week()
        self.open("#/today")
        pg = self.page
        expect(pg.locator(".page-heading h1")).to_have_text("Good to see you.")
        expect(pg.locator(".stat").nth(0)).to_contain_text("2")             # events today: Team sync, Study session
        expect(pg.locator(".stat").nth(0)).to_contain_text("Events today")
        expect(pg.locator(".stat").nth(1)).to_contain_text("3")
        expect(pg.locator(".stat").nth(2)).to_contain_text("1")
        expect(pg.locator(".week-card")).to_contain_text("Team sync")
        expect(pg.locator(".task-row")).to_have_count(3)
        self.shot("today")

    def test_empty_workspace_is_friendly_not_broken(self) -> None:
        self.open("#/today")
        expect(self.page.locator(".stat").nth(0)).to_contain_text("0")
        expect(self.page.locator(".page-heading h1")).to_be_visible()

    def test_every_view_opens_from_the_sidebar(self) -> None:
        self.open("#/today")
        pg = self.page
        for label, route, heading in (("Calendar", "#/calendar", "Calendar"), ("My tasks", "#/tasks", "My tasks"),
                                      ("Notes", "#/notes", "Notes"), ("Sticky board", "#/board", "Sticky board"), ("Today", "#/today", "Good to see you.")):
            pg.locator(".sidebar .nav-item", has_text=label).click()
            expect(pg).to_have_url(f"http://127.0.0.1:{self.svc.port}/{route}")
            expect(pg.locator(".page-heading h1")).to_have_text(heading)
            expect(pg.locator(".nav-item[aria-current=page]")).to_have_text(label)
        pg.go_back()
        expect(pg.locator(".page-heading h1")).to_have_text("Sticky board")

    def test_unknown_route_falls_back_to_today(self) -> None:
        self.open("#/nonsense")
        expect(self.page.locator(".page-heading h1")).to_have_text("Good to see you.")

    def test_mini_calendar_jumps_to_a_day(self) -> None:
        self.open("#/calendar")
        pg = self.page
        pg.locator(".mini-day:not(.out)", has_text="15").first.click()
        expect(pg.locator(".page-heading .subtitle")).to_contain_text("October 12")     # the week that holds the 15th

    def test_search_button_and_shortcut(self) -> None:
        self.seed_week()
        self.open("#/today")
        pg = self.page
        pg.keyboard.press("Control+k")
        pg.get_by_placeholder("Search events, tasks, notes…").fill("portfolio")
        expect(pg.locator(".search-hit")).to_have_count(1)
        pg.keyboard.press("Enter")                                       # a task opens in its editor
        expect(pg.get_by_role("dialog", name="Edit task")).to_be_visible()
        expect(pg.get_by_role("textbox", name="Title")).to_have_value("Finish the portfolio")

    def test_title_follows_the_view(self) -> None:
        self.open("#/notes")
        self.assertIn("Notes", self.page.title())
        self.assertIn("SPlanner", self.page.title())


class Phone(E2E):
    viewport = {"width": 390, "height": 800}

    def test_sidebar_becomes_a_drawer_and_nothing_scrolls_sideways(self) -> None:
        self.seed_week()
        self.open("#/today")
        pg = self.page
        self.assertLessEqual(pg.evaluate("document.documentElement.scrollWidth"), 390)
        expect(pg.locator(".sidebar")).not_to_be_in_viewport()
        pg.get_by_role("button", name="Open the sidebar").click()
        expect(pg.locator(".sidebar")).to_be_in_viewport()
        pg.locator(".sidebar .nav-item", has_text="My tasks").click()
        expect(pg.locator(".page-heading h1")).to_have_text("My tasks")
        expect(pg.locator(".sidebar")).not_to_be_in_viewport()
        self.assertLessEqual(pg.evaluate("document.documentElement.scrollWidth"), 390)
        self.shot("phone-tasks")


if __name__ == "__main__":
    unittest.main()
