"""The repeat rule: which dates it produces, how it keeps wall-clock time, single changes, validation and the iCalendar form."""
from __future__ import annotations

import unittest
from typing import Any

from applications.splanner import recurrence as rec
from applications.tools.errors import BadRequest


def event(start: str = "2026-10-27T08:00:00+00:00", end: str = "2026-10-27T09:00:00+00:00", zone: str = "UTC", all_day: bool = False) -> dict[str, Any]:
    return {"id": "e" * 32, "title": "T", "start_at": start, "end_at": end, "time_zone": zone, "all_day": int(all_day), "remind_before": None}


def repeating(rule: dict[str, Any], exceptions: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    base = event(**kw)
    canonical = rec.validate_rule(rule, base)
    return {**base, "recurrence": canonical, "exceptions": rec.validate_exceptions(exceptions or {}, base, canonical)}


def days(item: dict[str, Any], start: str = "2026-01-01T00:00:00+00:00", end: str = "2031-01-01T00:00:00+00:00") -> list[str]:
    return [o["start_at"][:10] for o in rec.occurrences(item, start, end)]


class RuleTests(unittest.TestCase):
    def test_daily_and_every_n_days(self) -> None:
        self.assertEqual(days(repeating({"freq": "daily", "count": 4})), ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"])
        self.assertEqual(days(repeating({"freq": "daily", "interval": 3, "count": 3})), ["2026-10-27", "2026-10-30", "2026-11-02"])

    def test_weekly_and_bi_weekly(self) -> None:
        self.assertEqual(days(repeating({"freq": "weekly", "count": 3})), ["2026-10-27", "2026-11-03", "2026-11-10"])
        self.assertEqual(days(repeating({"freq": "weekly", "interval": 2, "count": 3})), ["2026-10-27", "2026-11-10", "2026-11-24"])

    def test_weekly_on_chosen_weekdays_starts_with_the_event_itself(self) -> None:
        # 2026-10-27 is a Tuesday. Monday, Wednesday, Friday are chosen: Tuesday is added so the first occurrence is the event.
        item = repeating({"freq": "weekly", "weekdays": [1, 3, 5], "count": 6})
        self.assertEqual(item["recurrence"]["weekdays"], [1, 2, 3, 5])
        self.assertEqual(days(item), ["2026-10-27", "2026-10-28", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"])

    def test_every_other_week_on_two_weekdays_skips_the_odd_weeks(self) -> None:
        item = repeating({"freq": "weekly", "interval": 2, "weekdays": [2, 4], "count": 5})
        self.assertEqual(days(item), ["2026-10-27", "2026-10-29", "2026-11-10", "2026-11-12", "2026-11-24"])

    def test_a_single_chosen_weekday_that_is_the_start_is_stored_as_the_default(self) -> None:
        self.assertEqual(repeating({"freq": "weekly", "weekdays": [2]})["recurrence"]["weekdays"], [])

    def test_monthly_every_two_three_four_and_six_months(self) -> None:
        for interval, expected in ((1, ["2026-10-27", "2026-11-27", "2026-12-27"]), (2, ["2026-10-27", "2026-12-27", "2027-02-27"]),
                                   (3, ["2026-10-27", "2027-01-27", "2027-04-27"]), (4, ["2026-10-27", "2027-02-27", "2027-06-27"]),
                                   (6, ["2026-10-27", "2027-04-27", "2027-10-27"])):
            with self.subTest(interval=interval):
                self.assertEqual(days(repeating({"freq": "monthly", "interval": interval, "count": 3})), expected)

    def test_monthly_on_the_31st_falls_on_the_last_day_of_shorter_months(self) -> None:
        item = repeating({"freq": "monthly", "count": 5}, start="2027-01-31T08:00:00+00:00", end="2027-01-31T09:00:00+00:00")
        self.assertEqual(days(item), ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"])
        leap = repeating({"freq": "monthly", "count": 2}, start="2028-01-30T08:00:00+00:00", end="2028-01-30T09:00:00+00:00")
        self.assertEqual(days(leap), ["2028-01-30", "2028-02-29"])

    def test_monthly_on_the_nth_weekday(self) -> None:
        second_tuesday = repeating({"freq": "monthly", "month_mode": "weekday", "count": 4}, start="2026-10-13T08:00:00+00:00", end="2026-10-13T09:00:00+00:00")
        self.assertEqual(days(second_tuesday), ["2026-10-13", "2026-11-10", "2026-12-08", "2027-01-12"])
        last_friday = repeating({"freq": "monthly", "month_mode": "weekday", "count": 4}, start="2026-10-30T08:00:00+00:00", end="2026-10-30T09:00:00+00:00")
        self.assertEqual(days(last_friday), ["2026-10-30", "2026-11-27", "2026-12-25", "2027-01-29"])

    def test_yearly_and_leap_day(self) -> None:
        self.assertEqual(days(repeating({"freq": "yearly", "count": 3})), ["2026-10-27", "2027-10-27", "2028-10-27"])
        leap = repeating({"freq": "yearly", "count": 3}, start="2028-02-29T08:00:00+00:00", end="2028-02-29T09:00:00+00:00")
        self.assertEqual(days(leap), ["2028-02-29", "2029-02-28", "2030-02-28"])

    def test_until_is_inclusive_and_count_is_total(self) -> None:
        self.assertEqual(days(repeating({"freq": "daily", "until": "2026-10-29"})), ["2026-10-27", "2026-10-28", "2026-10-29"])
        self.assertEqual(len(days(repeating({"freq": "weekly", "count": 7}))), 7)

    def test_an_unbounded_rule_is_cut_by_the_window_only(self) -> None:
        item = repeating({"freq": "daily"})
        got = rec.occurrences(item, "2030-01-01T00:00:00+00:00", "2030-01-04T00:00:00+00:00")
        self.assertEqual([o["start_at"][:10] for o in got], ["2030-01-01", "2030-01-02", "2030-01-03"])

    def test_wall_clock_time_survives_a_clock_change_and_duration_is_kept(self) -> None:
        # 09:00-10:00 in Amsterdam; the clocks go back on 2026-10-25, so the UTC time moves by an hour.
        item = repeating({"freq": "weekly", "count": 3}, start="2026-10-20T07:00:00+00:00", end="2026-10-20T08:30:00+00:00", zone="Europe/Amsterdam")
        got = rec.occurrences(item, "2026-10-01T00:00:00+00:00", "2026-12-01T00:00:00+00:00")
        self.assertEqual([(o["start_at"][:16], o["end_at"][:16]) for o in got],
                         [("2026-10-20T07:00", "2026-10-20T08:30"), ("2026-10-27T08:00", "2026-10-27T09:30"), ("2026-11-03T08:00", "2026-11-03T09:30")])

    def test_all_day_events_repeat_by_date_and_keep_their_length(self) -> None:
        item = repeating({"freq": "weekly", "count": 3}, start="2026-10-27T00:00:00+00:00", end="2026-10-29T00:00:00+00:00", all_day=True)
        got = rec.occurrences(item, "2026-10-01T00:00:00+00:00", "2026-12-01T00:00:00+00:00")
        self.assertEqual([(o["start_at"][:10], o["end_at"][:10]) for o in got], [("2026-10-27", "2026-10-29"), ("2026-11-03", "2026-11-05"), ("2026-11-10", "2026-11-12")])

    def test_an_occurrence_that_only_overlaps_the_window_is_included(self) -> None:
        item = repeating({"freq": "daily"}, start="2026-10-27T22:00:00+00:00", end="2026-10-28T02:00:00+00:00")
        got = rec.occurrences(item, "2026-10-28T00:00:00+00:00", "2026-10-29T00:00:00+00:00")
        self.assertEqual([o["start_at"][:13] for o in got], ["2026-10-27T22", "2026-10-28T22"])

    def test_a_non_repeating_event_has_no_occurrences_of_its_own(self) -> None:
        self.assertEqual(rec.occurrences(event(), "2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"), [])
        self.assertIsNone(rec.next_occurrence(event(), "2026-01-01T00:00:00+00:00"))


class ExceptionTests(unittest.TestCase):
    RULE = {"freq": "weekly", "count": 5}

    def test_a_cancelled_occurrence_is_skipped_and_still_counts(self) -> None:
        item = repeating(self.RULE, {"2026-11-03T08:00:00+00:00": None})
        self.assertEqual(days(item), ["2026-10-27", "2026-11-10", "2026-11-17", "2026-11-24"])

    def test_a_moved_occurrence_appears_at_its_new_time_and_is_marked(self) -> None:
        moved = {"start_at": "2026-11-05T10:00:00+00:00", "end_at": "2026-11-05T11:00:00+00:00"}
        item = repeating(self.RULE, {"2026-11-03T08:00:00+00:00": moved})
        got = rec.occurrences(item, "2026-10-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00")
        self.assertEqual([(o["start_at"][:13], o["moved"]) for o in got],
                         [("2026-10-27T08", False), ("2026-11-05T10", True), ("2026-11-10T08", False), ("2026-11-17T08", False), ("2026-11-24T08", False)])
        self.assertEqual(got[1]["occurrence"], "2026-11-03T08:00:00+00:00")             # still addressed by its original start

    def test_a_moved_occurrence_is_found_in_a_window_that_misses_its_original_time(self) -> None:
        moved = {"start_at": "2026-12-20T10:00:00+00:00", "end_at": "2026-12-20T11:00:00+00:00"}
        item = repeating(self.RULE, {"2026-11-03T08:00:00+00:00": moved})
        got = rec.occurrences(item, "2026-12-19T00:00:00+00:00", "2026-12-21T00:00:00+00:00")
        self.assertEqual([o["start_at"][:13] for o in got], ["2026-12-20T10"])

    def test_keys_that_are_not_occurrences_are_dropped(self) -> None:
        base = event()
        rule = rec.validate_rule(self.RULE, base)
        self.assertEqual(rec.validate_exceptions({"2026-11-04T08:00:00+00:00": None, "2026-11-03T08:00:00+00:00": None}, base, rule), {"2026-11-03T08:00:00+00:00": None})
        self.assertEqual(rec.validate_exceptions({"2026-11-03T08:00:00+00:00": None}, base, None), {})

    def test_bad_exceptions_are_refused(self) -> None:
        base = event()
        rule = rec.validate_rule(self.RULE, base)
        for bad in ({"soon": None}, {"2026-11-03T08:00:00+00:00": {"start_at": "2026-11-03T09:00:00+00:00"}},
                    {"2026-11-03T08:00:00+00:00": {"start_at": "2026-11-03T09:00:00+00:00", "end_at": "2026-11-03T08:00:00+00:00"}}, []):
            with self.subTest(bad=bad), self.assertRaises(BadRequest):
                rec.validate_exceptions(bad, base, rule)

    def test_next_occurrence_skips_cancelled_and_judges_moved_ones_at_their_new_time(self) -> None:
        after = "2026-10-28T00:00:00+00:00"
        item = repeating(self.RULE, {"2026-11-03T08:00:00+00:00": None})
        self.assertEqual(rec.next_occurrence(item, after)["start_at"], "2026-11-10T08:00:00+00:00")
        pulled_in = repeating(self.RULE, {"2026-11-10T08:00:00+00:00": {"start_at": "2026-10-30T08:00:00+00:00", "end_at": "2026-10-30T09:00:00+00:00"}})
        self.assertEqual(rec.next_occurrence(pulled_in, after)["start_at"], "2026-10-30T08:00:00+00:00")
        self.assertIsNone(rec.next_occurrence(item, "2027-06-01T00:00:00+00:00"))               # the series has ended


class ValidationTests(unittest.TestCase):
    def bad(self, raw: Any) -> None:
        with self.assertRaises(BadRequest):
            rec.validate_rule(raw, event())

    def test_refused(self) -> None:
        self.bad("weekly")
        self.bad({"freq": "hourly"})
        self.bad({"freq": "daily", "interval": 0})
        self.bad({"freq": "daily", "interval": 1000})
        self.bad({"freq": "daily", "interval": True})
        self.bad({"freq": "daily", "bogus": 1})
        self.bad({"freq": "daily", "weekdays": [1]})
        self.bad({"freq": "weekly", "weekdays": [0]})
        self.bad({"freq": "weekly", "weekdays": [8]})
        self.bad({"freq": "daily", "month_mode": "weekday"})
        self.bad({"freq": "monthly", "month_mode": "never"})
        self.bad({"freq": "daily", "until": "2026-10-26"})                 # before the start
        self.bad({"freq": "daily", "until": "tomorrow"})
        self.bad({"freq": "daily", "until": "2027-01-01", "count": 3})
        self.bad({"freq": "daily", "count": 0})
        self.bad({"freq": "daily", "count": 1000})

    def test_none_means_no_repeat_and_defaults_are_filled_in(self) -> None:
        self.assertIsNone(rec.validate_rule(None, event()))
        self.assertEqual(rec.validate_rule({"freq": "monthly"}, event()), {"freq": "monthly", "interval": 1, "month_mode": "date", "until": None, "count": None})


class IcalendarTests(unittest.TestCase):
    def test_rrule_for_each_shape(self) -> None:
        self.assertEqual(rec.rrule(repeating({"freq": "daily"})), "FREQ=DAILY")
        self.assertEqual(rec.rrule(repeating({"freq": "weekly", "interval": 2, "weekdays": [1, 3]})), "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TU,WE")
        self.assertEqual(rec.rrule(repeating({"freq": "monthly", "interval": 3, "count": 4})), "FREQ=MONTHLY;INTERVAL=3;COUNT=4")
        self.assertEqual(rec.rrule(repeating({"freq": "monthly", "month_mode": "weekday"})), "FREQ=MONTHLY;BYDAY=4TU")
        last = repeating({"freq": "monthly", "month_mode": "weekday"}, start="2026-10-30T08:00:00+00:00", end="2026-10-30T09:00:00+00:00")
        self.assertEqual(rec.rrule(last), "FREQ=MONTHLY;BYDAY=-1FR")
        thirty_first = repeating({"freq": "monthly"}, start="2027-01-31T08:00:00+00:00", end="2027-01-31T09:00:00+00:00")
        self.assertEqual(rec.rrule(thirty_first), "FREQ=MONTHLY;BYMONTHDAY=-1")
        self.assertEqual(rec.rrule(repeating({"freq": "yearly", "until": "2030-12-31"})), "FREQ=YEARLY;UNTIL=20301231T235959Z")
        all_day = repeating({"freq": "daily", "until": "2026-11-01"}, start="2026-10-27T00:00:00+00:00", end="2026-10-28T00:00:00+00:00", all_day=True)
        self.assertEqual(rec.rrule(all_day), "FREQ=DAILY;UNTIL=20261101")
        self.assertIsNone(rec.rrule(event()))


if __name__ == "__main__":
    unittest.main()
