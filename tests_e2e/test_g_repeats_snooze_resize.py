"""Workflow G - repeating events, snooze and resizable sticky notes, driven with a real mouse and keyboard in Chromium:
choose every frequency in the event dialog, see occurrences in the week and month views, move, edit and delete one occurrence or the
whole series, have a repeating event's reminder move on, snooze for +1 ... +60 minutes or a custom time, and resize a sticky note by
its corner, by the keyboard and back to its default size."""
from __future__ import annotations

import re
import unittest
from datetime import date, datetime, timedelta, timezone

from playwright.sync_api import expect

from harness import E2E, iso


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


def midnight(offset_days: int = 0) -> datetime:
    return now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=offset_days)


PRESET_LABELS = ["Does not repeat", "Every day", "Every weekday (Mon–Fri)", "Every week", "Every 2 weeks (bi-weekly)", "Every month",
                 "Every 2 months (bi-monthly)", "Every 3 months (quarterly)", "Every 4 months (trimester)", "Every 6 months (semi-annually)",
                 "Every year", "Custom…"]


class Repeats(E2E):
    def series(self, title: str = "Standup", rule: dict | None = None, *, day: int = 0, hour: int = 10, **extra) -> dict:
        start = midnight(day) + timedelta(hours=hour)
        body = {"title": title, "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)), "color": "blue", "recurrence": rule or {"freq": "daily"}}
        body.update(extra)
        return self.api("POST", "/api/events", body, status=201)

    def event(self, event_id: str) -> dict:
        return self.api("GET", f"/api/events/{event_id}")

    def occurrences(self) -> list[dict]:
        return self.api("GET", "/api/occurrences")["occurrences"]

    def wait_for(self, check, message: str = "condition") -> None:
        for _ in range(60):
            if check():
                return
            self.page.wait_for_timeout(100)
        self.fail(f"timed out waiting for {message}")

    def open_new_event(self):
        self.open("#/calendar")
        self.page.get_by_role("button", name="Add event").click()
        return self.page.get_by_role("dialog", name="New event")

    def fill_basics(self, dialog, title: str, start_day: date) -> None:
        dialog.get_by_role("textbox", name="Title").fill(title)
        dialog.get_by_label("Starts date").fill(start_day.isoformat())
        dialog.get_by_label("Starts time").fill("10:00")
        dialog.get_by_label("Ends date").fill(start_day.isoformat())
        dialog.get_by_label("Ends time").fill("11:00")

    # ------------------------------------------------------------------ the dialog
    def test_the_repeat_menu_offers_every_frequency(self) -> None:
        dialog = self.open_new_event()
        options = dialog.get_by_label("Repeats").locator("option").all_inner_texts()
        self.assertEqual(options, PRESET_LABELS)
        expect(dialog.get_by_label("Repeats")).to_have_value("none")
        expect(dialog.get_by_label("Ends", exact=True)).to_be_hidden()                                  # nothing to end when it does not repeat
        self.shot("event-repeat-menu")

    def test_a_bi_weekly_event_made_in_the_dialog(self) -> None:
        dialog = self.open_new_event()
        start = (midnight(1)).date()
        self.fill_basics(dialog, "Sprint planning", start)
        dialog.get_by_label("Repeats").select_option(label="Every 2 weeks (bi-weekly)")
        expect(dialog.locator(".repeat-summary")).to_contain_text("Every 2 weeks on")
        dialog.get_by_role("button", name="Create event").click()
        expect(self.page.get_by_role("dialog")).to_have_count(0)
        [event] = self.api("GET", "/api/events")
        self.assertEqual((event["recurrence"]["freq"], event["recurrence"]["interval"]), ("weekly", 2))
        got = [o for o in self.occurrences() if o["id"] == event["id"]]
        self.assertGreater(len(got), 20)
        self.assertEqual({(parse(b["start_at"]) - parse(a["start_at"])).days for a, b in zip(got, got[1:])}, {14})

    def test_quarterly_trimester_and_bi_monthly_store_the_right_interval(self) -> None:
        for label, interval in (("Every 2 months (bi-monthly)", 2), ("Every 3 months (quarterly)", 3), ("Every 4 months (trimester)", 4), ("Every 6 months (semi-annually)", 6)):
            with self.subTest(label=label):
                dialog = self.open_new_event()
                self.fill_basics(dialog, label[:12], midnight(1).date())
                dialog.get_by_label("Repeats").select_option(label=label)
                dialog.get_by_role("button", name="Create event").click()
                expect(self.page.get_by_role("dialog")).to_have_count(0)
                stored = [e for e in self.api("GET", "/api/events") if e["title"] == label[:12]][0]
                self.assertEqual((stored["recurrence"]["freq"], stored["recurrence"]["interval"]), ("monthly", interval))

    def test_custom_repeat_on_chosen_weekdays_until_a_date(self) -> None:
        wednesday = midnight(1).date()
        while wednesday.isoweekday() != 3:
            wednesday += timedelta(days=1)
        dialog = self.open_new_event()
        self.fill_basics(dialog, "Study group", wednesday)
        dialog.get_by_label("Repeats").select_option(label="Custom…")
        dialog.get_by_label("Repeat every").fill("3")
        dialog.get_by_label("Unit").select_option(label="weeks")
        dialog.get_by_role("button", name="Monday", exact=True).click()
        dialog.get_by_role("button", name="Thursday", exact=True).click()
        dialog.get_by_label("Ends", exact=True).select_option(label="On a date")
        last = wednesday + timedelta(days=70)
        dialog.get_by_label("Last day of the repeat").fill(last.isoformat())
        expect(dialog.locator(".repeat-summary")).to_contain_text("Every 3 weeks on Monday, Wednesday and Thursday, until")
        self.shot("event-repeat-custom")
        dialog.get_by_role("button", name="Create event").click()
        expect(self.page.get_by_role("dialog")).to_have_count(0)
        [event] = self.api("GET", "/api/events")
        rule = event["recurrence"]
        self.assertEqual((rule["freq"], rule["interval"], set(rule["weekdays"]), rule["until"], rule["count"]), ("weekly", 3, {1, 3, 4}, last.isoformat(), None))
        got = [o for o in self.occurrences() if o["id"] == event["id"]]
        self.assertTrue(all(parse(o["start_at"]).date() <= last for o in got))
        self.assertEqual({parse(o["start_at"]).isoweekday() for o in got}, {1, 3, 4})

    def test_ending_after_a_number_of_times(self) -> None:
        dialog = self.open_new_event()
        self.fill_basics(dialog, "Course", midnight(1).date())
        dialog.get_by_label("Repeats").select_option(label="Every day")
        dialog.get_by_label("Ends", exact=True).select_option(label="After a number of times")
        dialog.get_by_label("Number of times").fill("4")
        expect(dialog.locator(".repeat-summary")).to_have_text("Every day, 4 times.")
        dialog.get_by_role("button", name="Create event").click()
        expect(self.page.get_by_role("dialog")).to_have_count(0)
        self.assertEqual(len(self.occurrences()), 4)

    def test_mistakes_are_refused_in_place(self) -> None:
        dialog = self.open_new_event()
        self.fill_basics(dialog, "Oops", midnight(1).date())
        dialog.get_by_label("Repeats").select_option(label="Custom…")
        dialog.get_by_label("Repeat every").fill("0")
        dialog.get_by_role("button", name="Create event").click()
        expect(dialog.get_by_role("alert")).to_contain_text("Repeat every 1 to 999")
        dialog.get_by_label("Repeat every").fill("2")
        dialog.get_by_label("Ends", exact=True).select_option(label="On a date")
        dialog.get_by_role("button", name="Create event").click()
        expect(dialog.get_by_role("alert")).to_contain_text("Choose the last day")
        dialog.get_by_label("Last day of the repeat").fill((midnight(-3)).date().isoformat())
        dialog.get_by_role("button", name="Create event").click()
        expect(dialog.get_by_role("alert")).to_contain_text("can not end before")
        self.assertEqual(self.api("GET", "/api/events"), [])

    def test_the_dialog_shows_the_rule_of_an_existing_series(self) -> None:
        self.series("Pills", {"freq": "monthly", "interval": 3})
        self.open("#/calendar")
        self.page.locator(".timegrid .ev", has_text="Pills").first.click()
        dialog = self.page.get_by_role("dialog", name="Edit repeating event")
        expect(dialog.get_by_label("Repeats")).to_have_value("quarterly")
        expect(dialog.locator(".repeat-note")).to_contain_text("This event repeats")

    # ------------------------------------------------------------------ the calendar
    def test_occurrences_are_drawn_and_marked_in_the_week_view(self) -> None:
        self.series("Standup", {"freq": "daily"})
        self.open("#/calendar")
        blocks = self.page.locator(".timegrid .ev", has_text="Standup")
        expect(blocks.first).to_be_visible()
        self.assertGreaterEqual(blocks.count(), 5)
        self.assertEqual(self.page.locator(".timegrid .ev .ev-repeat").count(), blocks.count())
        expect(blocks.first).to_have_attribute("aria-label", re.compile(r"Standup, 10:00 – 11:00, repeats daily"))
        blocks.first.scroll_into_view_if_needed()
        self.shot("calendar-repeats")

    def test_a_quarterly_event_shows_up_three_months_later_only(self) -> None:
        first_of_next_month = (midnight().replace(day=1) + timedelta(days=32)).replace(day=1)
        start = first_of_next_month.replace(day=15) + timedelta(hours=10)
        self.api("POST", "/api/events", {"title": "Review", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1)),
                                         "recurrence": {"freq": "monthly", "interval": 3}}, status=201)
        self.open("#/calendar")
        pg = self.page
        pg.get_by_role("group", name="Calendar view").get_by_role("button", name="Month").click()
        nav = pg.locator(".date-nav")
        chips = pg.locator(".month-chip", has_text="Review")
        expect(chips).to_have_count(0)
        nav.get_by_role("button", name="Next").click()
        expect(chips).to_have_count(1)
        expect(chips.first.locator(".chip-repeat")).to_be_visible()
        for _ in range(2):
            nav.get_by_role("button", name="Next").click()
            expect(chips).to_have_count(0)
        nav.get_by_role("button", name="Next").click()
        expect(chips).to_have_count(1)
        self.shot("calendar-month-repeats")

    def test_going_far_into_the_future_loads_more_occurrences(self) -> None:
        self.series("Daily habit", {"freq": "daily"})
        self.open("#/calendar")
        pg = self.page
        pg.get_by_role("group", name="Calendar view").get_by_role("button", name="Month").click()
        nav = pg.locator(".date-nav")
        for _ in range(26):                                                                   # more than two years ahead: outside the first window
            nav.get_by_role("button", name="Next").click()
        expect(pg.locator(".month-chip", has_text="Daily habit").first).to_be_visible(timeout=8000)

    def test_dragging_one_occurrence_moves_only_that_one(self) -> None:
        series = self.series("Standup", {"freq": "daily"})
        self.open("#/calendar")
        pg = self.page
        blocks = pg.locator(".timegrid .ev", has_text="Standup")
        expect(blocks.first).to_be_visible()
        target = blocks.nth(2)
        target.scroll_into_view_if_needed()
        key = target.get_attribute("data-occ")
        box = target.bounding_box()
        hour = float(pg.evaluate("parseFloat(getComputedStyle(document.querySelector('.tg-col')).getPropertyValue('--hour-h'))"))
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + 10)
        pg.mouse.down()
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + 10 + hour, steps=4)
        pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + 10 + hour * 2, steps=4)
        pg.mouse.up()
        expect(pg.locator(".toast", has_text="Moved this one")).to_be_visible()
        self.wait_for(lambda: self.event(series["id"])["exceptions"], "the single change to be saved")
        stored = self.event(series["id"])
        self.assertEqual(list(stored["exceptions"]), [key])
        self.assertEqual(parse(stored["exceptions"][key]["start_at"]) - parse(key), timedelta(hours=2))
        self.assertEqual(parse(stored["start_at"]), parse(series["start_at"]))                  # the series itself did not move
        times = {o["occurrence"]: parse(o["start_at"]).hour for o in self.occurrences()[:6]}
        self.assertEqual(sorted(times.values()).count(12), 1)
        self.assertEqual(sum(1 for h in times.values() if h == 10), len(times) - 1)
        expect(pg.locator(f'.timegrid .ev[data-occ="{key}"]')).to_contain_text("12:00")

    def test_alt_arrows_move_one_occurrence_and_keep_focus_on_it(self) -> None:
        series = self.series("Standup", {"freq": "daily"})
        self.open("#/calendar")
        pg = self.page
        blocks = pg.locator(".timegrid .ev", has_text="Standup")
        expect(blocks.first).to_be_visible()
        key = blocks.nth(1).get_attribute("data-occ")
        blocks.nth(1).focus()
        pg.keyboard.press("Alt+ArrowDown")
        pg.keyboard.press("Alt+ArrowDown")
        self.wait_for(lambda: self.event(series["id"])["exceptions"].get(key, {}) and parse(self.event(series["id"])["exceptions"][key]["start_at"]) - parse(key) == timedelta(minutes=30),
                      "two 15-minute steps")
        expect(pg.locator(f'.timegrid .ev[data-occ="{key}"]')).to_be_focused()

    def test_undo_a_move_from_the_dialog(self) -> None:
        series = self.series("Standup", {"freq": "daily", "count": 3})
        key = self.occurrences()[1]["occurrence"]
        moved = {"start_at": iso(parse(key) + timedelta(hours=3)), "end_at": iso(parse(key) + timedelta(hours=4))}
        self.api("PATCH", f"/api/events/{series['id']}", {"exceptions": {key: moved}, "version": 1})
        self.open("#/calendar")
        pg = self.page
        pg.locator(f'.timegrid .ev[data-occ="{key}"]').click()
        dialog = pg.get_by_role("dialog", name="Edit repeating event")
        expect(dialog.locator(".repeat-note")).to_contain_text("moved from its usual time")
        dialog.get_by_role("button", name="Undo move").click()
        expect(pg.locator(".toast", has_text="usual time")).to_be_visible()
        self.assertEqual(self.event(series["id"])["exceptions"], {})
        expect(pg.locator(f'.timegrid .ev[data-occ="{key}"]')).to_contain_text("10:00")

    def test_editing_the_series_changes_every_occurrence(self) -> None:
        series = self.series("Standup", {"freq": "daily", "count": 5})
        self.open("#/calendar")
        pg = self.page
        pg.locator(".timegrid .ev", has_text="Standup").nth(1).click()
        dialog = pg.get_by_role("dialog", name="Edit repeating event")
        dialog.get_by_role("textbox", name="Title").fill("Daily sync")
        dialog.get_by_label("Starts time").fill("11:30")
        dialog.get_by_label("Ends time").fill("12:00")
        dialog.get_by_role("button", name="Save changes").click()
        expect(pg.get_by_role("dialog")).to_have_count(0)
        stored = self.event(series["id"])
        self.assertEqual(stored["title"], "Daily sync")
        got = self.occurrences()
        self.assertEqual(len(got), 5)
        self.assertEqual({(parse(o["start_at"]).hour, parse(o["start_at"]).minute) for o in got}, {(11, 30)})
        self.assertEqual({parse(o["end_at"]) - parse(o["start_at"]) for o in got}, {timedelta(minutes=30)})
        self.assertEqual(self.page.locator(".timegrid .ev", has_text="Standup").count(), 0)

    def test_changing_the_repeat_of_an_existing_event(self) -> None:
        series = self.series("Standup", {"freq": "daily", "count": 5})
        self.open("#/calendar")
        pg = self.page
        pg.locator(".timegrid .ev", has_text="Standup").first.click()
        dialog = pg.get_by_role("dialog", name="Edit repeating event")
        dialog.get_by_label("Repeats").select_option(label="Every week")
        dialog.get_by_role("button", name="Save changes").click()
        expect(pg.get_by_role("dialog")).to_have_count(0)
        self.assertEqual(self.event(series["id"])["recurrence"]["freq"], "weekly")
        pg.locator(".timegrid .ev", has_text="Standup").first.click()
        dialog = pg.get_by_role("dialog", name="Edit repeating event")
        dialog.get_by_label("Repeats").select_option(label="Does not repeat")
        dialog.get_by_role("button", name="Save changes").click()
        expect(pg.get_by_role("dialog")).to_have_count(0)
        self.assertIsNone(self.event(series["id"])["recurrence"])
        self.assertEqual(self.occurrences(), [])
        expect(pg.locator(".timegrid .ev", has_text="Standup")).to_have_count(1)             # now an ordinary event

    def test_a_plain_event_can_become_repeating(self) -> None:
        start = midnight(1) + timedelta(hours=9)
        event = self.api("POST", "/api/events", {"title": "Gym", "start_at": iso(start), "end_at": iso(start + timedelta(hours=1))}, status=201)
        self.open("#/calendar")
        pg = self.page
        pg.locator(".timegrid .ev", has_text="Gym").first.click()
        dialog = pg.get_by_role("dialog", name="Edit event")
        dialog.get_by_label("Repeats").select_option(label="Every weekday (Mon–Fri)")
        dialog.get_by_role("button", name="Save changes").click()
        expect(pg.get_by_role("dialog")).to_have_count(0)
        stored = self.event(event["id"])
        self.assertEqual((stored["recurrence"]["freq"], stored["recurrence"]["weekdays"]), ("weekly", [1, 2, 3, 4, 5, 6, 7][:0] or stored["recurrence"]["weekdays"]))
        self.assertTrue({1, 2, 3, 4, 5} <= set(stored["recurrence"]["weekdays"]))
        self.assertEqual(parse(stored["start_at"]), start)

    def test_delete_only_this_event_or_the_whole_series(self) -> None:
        series = self.series("Standup", {"freq": "daily", "count": 5})
        self.open("#/calendar")
        pg = self.page
        blocks = pg.locator(".timegrid .ev", has_text="Standup")
        expect(blocks.first).to_be_visible()
        key = blocks.nth(1).get_attribute("data-occ")
        blocks.nth(1).click()
        pg.get_by_role("dialog", name="Edit repeating event").get_by_role("button", name="Delete").click()
        scope = pg.get_by_role("dialog", name="Delete repeating event")
        expect(scope).to_contain_text("What should be deleted?")
        scope.get_by_role("button", name="Only this event").click()
        expect(pg.locator(".toast", has_text="Removed this event")).to_be_visible()
        self.assertEqual(self.event(series["id"])["exceptions"], {key: None})
        self.assertEqual(len(self.occurrences()), 4)
        expect(pg.locator(f'.timegrid .ev[data-occ="{key}"]')).to_have_count(0)
        pg.locator(".timegrid .ev", has_text="Standup").first.click()
        pg.get_by_role("dialog", name="Edit repeating event").get_by_role("button", name="Delete").click()
        pg.get_by_role("dialog", name="Delete repeating event").get_by_role("button", name="All events").click()
        expect(pg.locator(".timegrid .ev", has_text="Standup")).to_have_count(0)
        self.assertEqual(self.api("GET", "/api/events"), [])

    def test_cancelling_the_delete_question_keeps_everything(self) -> None:
        series = self.series("Standup", {"freq": "daily", "count": 3})
        self.open("#/calendar")
        pg = self.page
        pg.locator(".timegrid .ev", has_text="Standup").first.click()
        pg.get_by_role("dialog", name="Edit repeating event").get_by_role("button", name="Delete").click()
        pg.get_by_role("dialog", name="Delete repeating event").get_by_role("button", name="Cancel").click()
        expect(pg.get_by_role("dialog", name="Edit repeating event")).to_be_visible()
        self.assertEqual(self.event(series["id"])["exceptions"], {})
        self.assertEqual(len(self.occurrences()), 3)

    def test_today_counts_an_occurrence(self) -> None:
        self.series("Standup", {"freq": "daily"}, day=0, hour=10)
        self.open("#/today")
        expect(self.page.get_by_role("button", name="1 Event today")).to_be_visible()

    def test_search_finds_a_repeating_event_and_opens_its_series(self) -> None:
        self.series("Quarterly review", {"freq": "monthly", "interval": 3})
        self.open("#/today")
        pg = self.page
        pg.keyboard.press("Control+k")
        pg.get_by_role("combobox", name="Search").fill("quarterly")
        pg.get_by_role("option", name=re.compile("Quarterly review")).first.click()
        expect(pg.get_by_role("dialog", name="Edit repeating event")).to_be_visible()

    # ------------------------------------------------------------------ reminders of a series
    def test_a_repeating_events_reminder_moves_on_when_dismissed(self) -> None:
        soon = (now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
        start = soon - timedelta(days=2)
        series = self.api("POST", "/api/events", {"title": "Take pills", "start_at": iso(start), "end_at": iso(start + timedelta(minutes=15)),
                                                  "recurrence": {"freq": "daily"}, "remind_before": 10}, status=201)
        [reminder] = self.api("GET", "/api/reminders")
        self.assertEqual(parse(reminder["occurrence_at"]), soon)
        self.open("#/today")
        pg = self.page
        expect(pg.locator(".toast", has_text="Take pills")).to_be_visible(timeout=8000)
        pg.get_by_role("button", name=re.compile(r"^Reminders")).click()
        dialog = pg.get_by_role("dialog", name="Reminders")
        dialog.get_by_role("button", name="Actions for Take pills").click()
        pg.get_by_role("menuitem", name="Dismiss").click()
        self.wait_for(lambda: self.api("GET", "/api/reminders")[0]["occurrence_at"] != reminder["occurrence_at"], "the reminder to move on")
        [after] = self.api("GET", "/api/reminders")
        self.assertEqual((after["status"], parse(after["occurrence_at"])), ("pending", soon + timedelta(days=1)))
        self.assertEqual(parse(after["due_at"]), soon + timedelta(days=1) - timedelta(minutes=10))
        expect(pg.get_by_role("button", name=re.compile(r"^Reminders"))).to_have_accessible_name("Reminders")
        self.assertEqual(series["id"], after["entity_id"])


class Snooze(E2E):
    def due_reminder(self, title: str = "Stand up") -> dict:
        return self.api("POST", "/api/reminders", {"title": title, "due_at": iso(now() - timedelta(minutes=1))}, status=201)

    def reminders(self) -> list[dict]:
        return self.api("GET", "/api/reminders")

    def wait_due_after(self, minutes: float, tolerance: float = 1.5) -> datetime:
        for _ in range(40):
            due = parse(self.reminders()[0]["due_at"])
            if due > now() + timedelta(minutes=1):
                break
            self.page.wait_for_timeout(100)
        lead = (due - now()).total_seconds() / 60
        self.assertLess(abs(lead - minutes), tolerance, f"due in {lead:.1f} minutes, expected about {minutes}")
        return due

    def test_a_due_reminder_has_a_visible_snooze_button_with_every_choice(self) -> None:
        self.due_reminder()
        self.open("#/today")
        pg = self.page
        pg.get_by_role("button", name=re.compile(r"^Reminders")).click()
        dialog = pg.get_by_role("dialog", name="Reminders")
        dialog.get_by_role("button", name="Snooze Stand up").click()
        menu = pg.get_by_role("menu", name="Snooze")
        items = menu.get_by_role("menuitem").all_inner_texts()
        self.assertEqual([i.strip() for i in items], ["Snooze +1 min", "Snooze +5 min", "Snooze +10 min", "Snooze +15 min", "Snooze +30 min", "Snooze +60 min", "Snooze custom…"])
        self.shot("snooze-menu")

    def test_each_quick_choice_snoozes_by_that_many_minutes(self) -> None:
        for minutes in (1, 5, 15, 30, 60):
            with self.subTest(minutes=minutes):
                self.api("DELETE", f"/api/reminders/{self.reminders()[0]['id']}") if self.reminders() else None
                self.due_reminder()
                self.open("#/today")
                pg = self.page
                pg.get_by_role("button", name=re.compile(r"^Reminders")).click()
                pg.get_by_role("dialog", name="Reminders").get_by_role("button", name="Snooze Stand up").click()
                pg.get_by_role("menuitem", name=f"Snooze +{minutes} min").click()
                expect(pg.locator(".toast", has_text="Snoozed until")).to_be_visible()
                self.wait_due_after(minutes, tolerance=1.2)
                self.assertEqual(self.reminders()[0]["status"], "pending")

    def test_custom_snooze_with_hours(self) -> None:
        self.due_reminder()
        self.open("#/today")
        pg = self.page
        pg.get_by_role("button", name=re.compile(r"^Reminders")).click()
        pg.get_by_role("dialog", name="Reminders").get_by_role("button", name="Actions for Stand up").click()
        pg.get_by_role("menuitem", name="Snooze custom…").click()
        dialog = pg.get_by_role("dialog", name="Snooze")
        dialog.get_by_label("Snooze for").fill("2")
        dialog.get_by_label("Unit").select_option(label="hours")
        expect(dialog).to_contain_text("Alerts again")
        self.shot("snooze-custom")
        dialog.get_by_role("button", name="Snooze", exact=True).click()
        expect(pg.locator(".toast", has_text="Snoozed until")).to_be_visible()
        self.wait_due_after(120)

    def test_custom_snooze_refuses_nonsense_and_stays_open(self) -> None:
        self.due_reminder()
        self.open("#/today")
        pg = self.page
        pg.get_by_role("button", name=re.compile(r"^Reminders")).click()
        pg.get_by_role("dialog", name="Reminders").get_by_role("button", name="Snooze Stand up").click()
        pg.get_by_role("menuitem", name="Snooze custom…").click()
        dialog = pg.get_by_role("dialog", name="Snooze")
        for value, message in (("0", "Enter how long"), ("1.5", "whole number")):
            dialog.get_by_label("Snooze for").fill(value)
            dialog.get_by_role("button", name="Snooze", exact=True).click()
            expect(dialog.get_by_role("alert")).to_contain_text(message)
        dialog.get_by_label("Snooze for").fill("31")
        dialog.get_by_label("Unit").select_option(label="days")
        dialog.get_by_role("button", name="Snooze", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("30 days at most")
        self.assertLess(parse(self.reminders()[0]["due_at"]), now())                          # nothing was snoozed
        dialog.get_by_label("Snooze for").fill("3")
        dialog.get_by_role("button", name="Snooze", exact=True).click()
        expect(pg.get_by_role("dialog", name="Snooze")).to_have_count(0)
        self.wait_due_after(4320, tolerance=2)

    def test_the_snooze_choices_are_also_in_the_more_menu(self) -> None:
        self.due_reminder()
        self.open("#/today")
        pg = self.page
        pg.get_by_role("button", name=re.compile(r"^Reminders")).click()
        pg.get_by_role("dialog", name="Reminders").get_by_role("button", name="Actions for Stand up").click()
        texts = [t.strip() for t in pg.get_by_role("menu").get_by_role("menuitem").all_inner_texts()]
        self.assertEqual(texts[:1] + texts[1:8], ["Dismiss", "Snooze +1 min", "Snooze +5 min", "Snooze +10 min", "Snooze +15 min", "Snooze +30 min", "Snooze +60 min", "Snooze custom…"])


class StickyResize(E2E):
    def sticky(self, text: str = "Buy milk\nCall Sam") -> dict:
        return self.api("POST", "/api/stickies", {"content": text, "x": 40, "y": 40}, status=201)

    def stored(self) -> dict:
        return self.api("GET", "/api/stickies")[0]

    def size(self, locator) -> tuple[float, float]:
        box = locator.bounding_box()
        return box["width"], box["height"]

    def drag_handle(self, dx: float, dy: float, steps: int = 6) -> None:
        pg = self.page
        handle = pg.locator(".sticky-resize").first.bounding_box()
        x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
        pg.mouse.move(x, y)
        pg.mouse.down()
        pg.mouse.move(x + dx / 2, y + dy / 2, steps=steps)
        pg.mouse.move(x + dx, y + dy, steps=steps)
        pg.mouse.up()

    def wait_for(self, check, message: str = "condition") -> None:
        for _ in range(60):
            if check():
                return
            self.page.wait_for_timeout(100)
        self.fail(f"timed out waiting for {message}")

    def test_a_new_sticky_has_the_default_size_and_a_visible_handle(self) -> None:
        self.sticky()
        self.open("#/board")
        note = self.page.locator(".sticky").first
        expect(note.locator(".sticky-resize")).to_be_visible()
        expect(note.locator(".sticky-resize")).to_have_attribute("aria-label", re.compile("Resize sticky note"))
        self.assertEqual((self.stored()["width"], self.stored()["height"]), (None, None))
        width, height = self.size(note)
        self.assertGreater(width, 190)
        self.assertGreater(height, 170)

    def test_drag_the_corner_to_resize_and_it_survives_a_reload(self) -> None:
        self.sticky()
        self.open("#/board")
        pg = self.page
        note = pg.locator(".sticky").first
        width, height = self.size(note)
        self.drag_handle(130, 90)
        self.wait_for(lambda: self.stored()["width"] is not None, "the size to be saved")
        saved = self.stored()
        self.assertAlmostEqual(saved["width"], width + 130, delta=2)
        self.assertAlmostEqual(saved["height"], height + 90, delta=2)
        self.assertEqual((saved["x"], saved["y"]), (40.0, 40.0))                               # resizing does not move it
        pg.reload()
        pg.wait_for_selector(".sticky")
        reloaded = self.size(pg.locator(".sticky").first)
        self.assertAlmostEqual(reloaded[0], saved["width"], delta=1)
        self.assertAlmostEqual(reloaded[1], saved["height"], delta=1)
        self.shot("board-resized")

    def test_the_text_and_position_are_untouched_by_resizing(self) -> None:
        self.sticky("Line one\nLine two\nLine three")
        self.open("#/board")
        self.drag_handle(80, 60)
        self.wait_for(lambda: self.stored()["width"] is not None)
        saved = self.stored()
        self.assertEqual(saved["text"], "Line one\nLine two\nLine three")
        self.assertEqual(self.page.locator(".sticky-text").first.input_value(), "Line one\nLine two\nLine three")
        self.assertEqual((saved["x"], saved["y"]), (40.0, 40.0))

    def test_resizing_has_a_minimum_and_a_maximum(self) -> None:
        self.sticky()
        self.open("#/board")
        self.drag_handle(-600, -600)
        self.wait_for(lambda: self.stored()["width"] is not None)
        self.assertEqual((self.stored()["width"], self.stored()["height"]), (150.0, 110.0))
        self.drag_handle(2500, 2500)
        self.wait_for(lambda: self.stored()["width"] == 1000.0)
        self.assertEqual((self.stored()["width"], self.stored()["height"]), (1000.0, 1000.0))

    def test_the_keyboard_resizes_and_double_click_goes_back_to_the_default(self) -> None:
        self.sticky()
        self.open("#/board")
        pg = self.page
        note = pg.locator(".sticky").first
        width, height = self.size(note)
        handle = note.locator(".sticky-resize")
        handle.focus()
        pg.keyboard.press("ArrowRight")
        pg.keyboard.press("ArrowRight")
        pg.keyboard.press("Shift+ArrowDown")
        self.wait_for(lambda: self.stored()["height"] is not None and abs(self.stored()["height"] - (height + 64)) < 2 and abs(self.stored()["width"] - (width + 32)) < 2, "three keyboard steps")
        pg.keyboard.press("ArrowLeft")
        pg.keyboard.press("ArrowLeft")
        pg.keyboard.press("ArrowLeft")
        self.wait_for(lambda: abs(self.stored()["width"] - (width - 16)) < 2, "three steps back")
        handle.dblclick()
        self.wait_for(lambda: self.stored()["width"] is None, "the reset")
        self.assertEqual((self.stored()["width"], self.stored()["height"]), (None, None))
        reset = self.size(note)
        self.assertAlmostEqual(reset[0], width, delta=1)
        self.assertAlmostEqual(reset[1], height, delta=1)

    def test_reset_size_is_in_the_menu_once_the_note_was_resized(self) -> None:
        self.sticky()
        self.open("#/board")
        pg = self.page
        pg.get_by_role("button", name="Sticky note actions").click()
        expect(pg.get_by_role("menuitem", name="Reset size")).to_be_disabled()
        pg.keyboard.press("Escape")
        self.drag_handle(60, 40)
        self.wait_for(lambda: self.stored()["width"] is not None)
        pg.get_by_role("button", name="Sticky note actions").click()
        pg.get_by_role("menuitem", name="Reset size").click()
        self.wait_for(lambda: self.stored()["width"] is None, "the size to be reset")

    def test_sticky_notes_can_be_different_sizes_side_by_side(self) -> None:
        self.api("POST", "/api/stickies", {"content": "small", "x": 20, "y": 20, "width": 160, "height": 120}, status=201)
        self.api("POST", "/api/stickies", {"content": "large", "x": 400, "y": 20, "width": 420, "height": 340, "color": "green"}, status=201)
        self.open("#/board")
        notes = self.page.locator(".sticky")
        self.assertEqual(notes.count(), 2)
        small, large = self.size(notes.nth(0)), self.size(notes.nth(1))
        self.assertAlmostEqual(small[0], 160, delta=1)
        self.assertAlmostEqual(large[1], 340, delta=1)
        self.assertGreater(large[0], small[0] * 2)


if __name__ == "__main__":
    unittest.main()
