"""Workflow B - events and the calendar with real mouse and keyboard: create, edit, delete, drag to move, drag to resize, the keyboard
equivalents, the month view, creating by clicking a free slot, and a stale edit being refused instead of overwriting."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect

from harness import E2E, iso


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Calendar(E2E):
    def one_event(self, title: str = "Planning", day: int = 0, hour: int = 10, minutes: int = 60) -> dict:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = today + timedelta(days=day, hours=hour)
        return self.api("POST", "/api/events", {"title": title, "start_at": iso(start), "end_at": iso(start + timedelta(minutes=minutes)), "color": "blue"}, status=201)

    def block(self, title: str):
        return self.page.locator(".timegrid .ev", has_text=title).first

    def grid_hour_height(self) -> float:
        return float(self.page.evaluate("parseFloat(getComputedStyle(document.querySelector('.tg-col')).getPropertyValue('--hour-h') || getComputedStyle(document.querySelector('.tg-col')).backgroundSize.split(' ')[1])"))

    def events(self) -> list[dict]:
        return self.api("GET", "/api/events")

    # ------------------------------------------------------------------ create / edit / delete
    def test_create_edit_and_delete_an_event(self) -> None:
        self.open("#/calendar")
        pg = self.page
        pg.get_by_role("button", name="Add event").click()
        dialog = pg.get_by_role("dialog", name="New event")
        dialog.get_by_role("textbox", name="Title").fill("Dentist")
        dialog.get_by_role("textbox", name="Location").fill("Main street 4")
        dialog.get_by_role("button", name="Create event").click()
        expect(self.block("Dentist")).to_be_visible()
        stored = self.events()
        self.assertEqual([e["title"] for e in stored], ["Dentist"])
        self.assertEqual(stored[0]["location"], "Main street 4")
        self.shot("calendar-week")

        self.block("Dentist").click()
        dialog = pg.get_by_role("dialog", name="Edit event")
        dialog.get_by_role("textbox", name="Title").fill("Dentist (moved)")
        dialog.get_by_role("button", name="Save changes").click()
        expect(self.block("Dentist (moved)")).to_be_visible()
        self.assertEqual(self.events()[0]["version"], 2)

        self.block("Dentist (moved)").click()
        pg.get_by_role("dialog", name="Edit event").get_by_role("button", name="Delete").click()
        pg.get_by_role("dialog", name="Delete this event?").get_by_role("button", name="Delete").click()
        expect(pg.locator(".timegrid .ev")).to_have_count(0)
        self.assertEqual(self.events(), [])

    def test_an_empty_title_is_refused_in_place(self) -> None:
        self.open("#/calendar")
        pg = self.page
        pg.get_by_role("button", name="Add event").click()
        dialog = pg.get_by_role("dialog", name="New event")
        dialog.get_by_role("button", name="Create event").click()
        expect(dialog).to_be_visible()
        expect(dialog.locator(".notice.err")).to_be_visible()
        self.assertEqual(self.events(), [])

    def test_end_before_start_is_refused(self) -> None:
        self.open("#/calendar")
        pg = self.page
        pg.get_by_role("button", name="Add event").click()
        dialog = pg.get_by_role("dialog", name="New event")
        dialog.get_by_role("textbox", name="Title").fill("Backwards")
        dialog.get_by_label("Ends time").fill("00:30")
        dialog.get_by_role("button", name="Create event").click()
        expect(dialog.locator(".notice.err")).to_be_visible()
        self.assertEqual(self.events(), [])

    def test_all_day_event_lands_in_the_all_day_strip(self) -> None:
        self.open("#/calendar")
        pg = self.page
        pg.get_by_role("button", name="Add event").click()
        dialog = pg.get_by_role("dialog", name="New event")
        dialog.get_by_role("textbox", name="Title").fill("Conference")
        dialog.get_by_text("All day").click()
        dialog.get_by_role("button", name="Create event").click()
        expect(pg.locator(".tg-allday .tg-chip", has_text="Conference")).to_be_visible()
        self.assertTrue(self.events()[0]["all_day"])

    # ------------------------------------------------------------------ mouse
    def test_drag_moves_an_event_to_another_day_and_time(self) -> None:
        event = self.one_event("Planning", day=0, hour=10)
        self.open("#/calendar")
        pg = self.page
        block = self.block("Planning")
        block.scroll_into_view_if_needed()
        box = block.bounding_box()
        hour = self.grid_hour_height()
        column = pg.locator(".tg-col").first.bounding_box()["width"]
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + 10)
        pg.mouse.down()
        pg.mouse.move(box["x"] + box["width"] / 2 + column / 2, box["y"] + 10 + hour / 2, steps=4)
        pg.mouse.move(box["x"] + box["width"] / 2 + column, box["y"] + 10 + hour * 2, steps=6)
        pg.mouse.up()
        pg.wait_for_function("(id) => true", arg=event["id"])
        for _ in range(40):
            moved = self.events()[0]
            if moved["version"] == 2:
                break
            pg.wait_for_timeout(100)
        before, after = parse(event["start_at"]), parse(moved["start_at"])
        self.assertEqual(moved["version"], 2)
        self.assertEqual((after - before), timedelta(days=1, hours=2))
        self.assertEqual(parse(moved["end_at"]) - after, timedelta(hours=1))        # the length is untouched

    def test_drag_the_bottom_edge_resizes(self) -> None:
        event = self.one_event("Planning", hour=10, minutes=60)
        self.open("#/calendar")
        pg = self.page
        block = self.block("Planning")
        block.scroll_into_view_if_needed()
        block.hover()
        handle = block.locator(".ev-resize").bounding_box()
        hour = self.grid_hour_height()
        pg.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
        pg.mouse.down()
        pg.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + hour / 2, steps=5)
        pg.mouse.up()
        for _ in range(40):
            changed = self.events()[0]
            if changed["version"] == 2:
                break
            pg.wait_for_timeout(100)
        self.assertEqual(parse(changed["start_at"]), parse(event["start_at"]))
        self.assertEqual(parse(changed["end_at"]) - parse(changed["start_at"]), timedelta(minutes=90))

    def test_a_click_without_moving_opens_the_editor_and_changes_nothing(self) -> None:
        self.one_event("Planning")
        self.open("#/calendar")
        self.block("Planning").click()
        expect(self.page.get_by_role("dialog", name="Edit event")).to_be_visible()
        self.assertEqual(self.events()[0]["version"], 1)

    def test_clicking_a_free_slot_starts_an_event_there(self) -> None:
        self.open("#/calendar")
        pg = self.page
        box = pg.locator(".tg-col").nth(2).bounding_box()
        visible = pg.locator(".tg-body").bounding_box()
        pg.mouse.click(box["x"] + box["width"] / 2, visible["y"] + visible["height"] / 2)
        dialog = pg.get_by_role("dialog", name="New event")
        expect(dialog).to_be_visible()
        self.assertIn(dialog.get_by_label("Starts time").input_value()[-2:], ("00", "30"))

    # ------------------------------------------------------------------ keyboard
    def test_alt_arrows_move_and_alt_shift_arrows_resize(self) -> None:
        event = self.one_event("Planning", hour=10, minutes=60)
        self.open("#/calendar")
        pg = self.page
        block = self.block("Planning")
        block.focus()
        pg.keyboard.press("Alt+ArrowDown")
        for _ in range(40):
            stored = self.events()[0]
            if stored["version"] == 2:
                break
            pg.wait_for_timeout(100)
        self.assertEqual(parse(stored["start_at"]) - parse(event["start_at"]), timedelta(minutes=15))
        expect(self.block("Planning")).to_have_attribute("aria-label", "Planning, 10:15 – 11:15")     # the grid has redrawn
        self.block("Planning").focus()
        pg.keyboard.press("Alt+Shift+ArrowDown")
        for _ in range(40):
            stored = self.events()[0]
            if stored["version"] == 3:
                break
            pg.wait_for_timeout(100)
        self.assertEqual(parse(stored["end_at"]) - parse(stored["start_at"]), timedelta(minutes=75))

    def test_holding_an_arrow_key_never_conflicts_with_itself(self) -> None:
        event = self.one_event("Planning", hour=10, minutes=60)
        self.open("#/calendar")
        pg = self.page
        self.block("Planning").focus()
        for _ in range(6):                                                    # key repeat: faster than the server answers
            pg.keyboard.press("Alt+ArrowDown")
        for _ in range(60):
            stored = self.events()[0]
            if parse(stored["start_at"]) - parse(event["start_at"]) == timedelta(minutes=90) and stored["version"] == 7:
                break
            pg.wait_for_timeout(100)
        self.assertEqual(parse(stored["start_at"]) - parse(event["start_at"]), timedelta(minutes=90))
        self.assertEqual(parse(stored["end_at"]) - parse(stored["start_at"]), timedelta(hours=1))

    def test_enter_on_a_focused_event_opens_it(self) -> None:
        self.one_event("Planning")
        self.open("#/calendar")
        self.block("Planning").focus()
        self.page.keyboard.press("Enter")
        expect(self.page.get_by_role("dialog", name="Edit event")).to_be_visible()

    # ------------------------------------------------------------------ views
    def test_week_day_and_month_views(self) -> None:
        self.one_event("Planning")
        self.one_event("Retro", day=3, hour=15)
        self.open("#/calendar")
        pg = self.page
        modes = pg.get_by_role("group", name="Calendar view")
        modes.get_by_role("button", name="Day").click()
        expect(pg.locator(".tg-day")).to_have_count(1)
        expect(self.block("Planning")).to_be_visible()
        modes.get_by_role("button", name="Month").click()
        expect(pg.locator(".month-chip", has_text="Planning")).to_be_visible()
        expect(pg.locator(".month-chip", has_text="Retro")).to_be_visible()
        self.shot("calendar-month")
        pg.locator(".month-chip", has_text="Planning").click()
        expect(pg.get_by_role("dialog", name="Edit event")).to_be_visible()

    def test_previous_next_and_today_move_the_visible_range(self) -> None:
        self.open("#/calendar")
        pg = self.page
        first = pg.locator(".page-heading .subtitle").inner_text()
        nav = pg.locator(".date-nav")
        nav.get_by_role("button", name="Next").click()
        self.assertNotEqual(pg.locator(".page-heading .subtitle").inner_text(), first)
        nav.get_by_role("button", name="Today", exact=True).click()
        expect(pg.locator(".page-heading .subtitle")).to_have_text(first)

    # ------------------------------------------------------------------ safety
    def test_a_stale_edit_does_not_overwrite_a_newer_change(self) -> None:
        event = self.one_event("Planning")
        self.open("#/calendar")
        pg = self.page
        self.block("Planning").click()
        dialog = pg.get_by_role("dialog", name="Edit event")
        self.allow_statuses = (409,)
        self.api("PATCH", f"/api/events/{event['id']}", {"title": "Changed elsewhere", "version": 1})
        dialog.get_by_role("textbox", name="Title").fill("My stale edit")
        dialog.get_by_role("button", name="Save changes").click()
        expect(pg.locator(".toast, .notice, .error").filter(has_text="changed")).to_be_visible()
        self.assertEqual(self.events()[0]["title"], "Changed elsewhere")


if __name__ == "__main__":
    unittest.main()
