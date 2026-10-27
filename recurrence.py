"""Repeating events: the repeat rule, the occurrences it produces, single-occurrence changes, and its iCalendar form.

One implementation, used by everything that needs to know when an event happens: the server expands a repeating event into the
occurrences the client draws, finds the next one for its reminder, and writes the ``RRULE`` when exporting. The browser never
expands a rule itself.

**The rule** is a small JSON object stored with the event::

    {"freq": "daily" | "weekly" | "monthly" | "yearly",
     "interval": 1..999,                    every N days / weeks / months / years
     "weekdays": [1..7],                    weekly only, Monday = 1 ... Sunday = 7 (empty: the weekday of the start)
     "month_mode": "date" | "weekday",      monthly only: "on the 14th" or "on the second Tuesday" (the last one when it is the fifth)
     "until": "YYYY-MM-DD" | null,          last day an occurrence may fall on, in the event's time zone
     "count": 1..999 | null}                total number of occurrences (including the first); not together with ``until``

"Every two months", "quarterly" (3), "trimester" (4) and "semi-annually" (6) are all ``monthly`` with a different interval; "bi-weekly" is
``weekly`` with interval 2. The names are the interface's concern, the rule only stores the number.

**Invariants.** The event's own start is always its first occurrence. An occurrence keeps its wall-clock time in the event's time zone
across daylight-saving changes, and keeps its duration. A monthly rule "on the 31st" falls on the last day of shorter months (an
occurrence is never silently dropped). All-day events repeat by date.

**Single changes** ("exceptions") are a dict keyed by the occurrence's *original* start instant: ``None`` cancels that occurrence,
``{"start_at", "end_at"}`` moves it. Keys that are no longer occurrences of the rule are discarded when the rule changes.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterator, Mapping
from zoneinfo import ZoneInfo

from ..tools import validation as v
from ..tools.errors import BadRequest

FREQUENCIES = ("daily", "weekly", "monthly", "yearly")
MONTH_MODES = ("date", "weekday")
MAX_INTERVAL = 999
MAX_COUNT = 999
MAX_EXCEPTIONS = 2_000
MAX_OCCURRENCES = 30_000                 # one expansion never returns more than this
_MAX_STEPS = 120_000                     # one expansion never walks further than this many candidate dates (a daily rule: 328 years)
_RULE_KEYS = {"freq", "interval", "weekdays", "month_mode", "until", "count"}
_DAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_FORMAT = "%Y-%m-%dT%H:%M:%S+00:00"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_FORMAT)


def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


# ---------------------------------------------------------------------------------------------------------------- the event's shape
class _Base:
    """What the rule needs to know about the event it belongs to, read once."""

    def __init__(self, event: Mapping[str, Any]) -> None:
        self.zone = _zone(event["time_zone"])
        self.all_day = bool(event["all_day"])
        start = v.parse_instant(event["start_at"]).astimezone(self.zone)
        end = v.parse_instant(event["end_at"]).astimezone(self.zone)
        self.start_date = start.date()
        self.clock = start.time().replace(tzinfo=None)
        if self.all_day:
            self.span_days = max(1, (end.date() - start.date()).days)
            self.length = timedelta(days=self.span_days)
        else:
            self.length = v.parse_instant(event["end_at"]) - v.parse_instant(event["start_at"])
            self.span_days = 0
        self.first_utc = v.parse_instant(event["start_at"]).astimezone(timezone.utc)

    def at(self, day: date) -> tuple[datetime, datetime]:
        """Start and end (UTC) of the occurrence that begins on local ``day``."""
        if self.all_day:
            begin = datetime.combine(day, time(0, 0), tzinfo=self.zone)
            finish = datetime.combine(day + timedelta(days=self.span_days), time(0, 0), tzinfo=self.zone)
            return begin.astimezone(timezone.utc), finish.astimezone(timezone.utc)
        begin = datetime.combine(day, self.clock, tzinfo=self.zone).astimezone(timezone.utc)
        return begin, begin + self.length


# ---------------------------------------------------------------------------------------------------------------- validation
def validate_rule(raw: Any, event: Mapping[str, Any]) -> dict[str, Any] | None:
    """The canonical rule for ``raw`` (``None`` means "does not repeat"). ``event`` supplies the start and the time zone."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BadRequest("recurrence must be an object, or null for an event that does not repeat")
    extra = sorted(set(raw) - _RULE_KEYS)
    if extra:
        raise BadRequest(f"Unknown recurrence field: {extra[0]}.")
    base = _Base(event)
    freq = v.choice(raw.get("freq"), FREQUENCIES, "recurrence.freq")
    interval = v.integer(raw.get("interval", 1), "recurrence.interval", 1, MAX_INTERVAL)
    rule: dict[str, Any] = {"freq": freq, "interval": interval}
    if freq == "weekly":
        days = raw.get("weekdays", [])
        if not isinstance(days, list) or len(days) > 7:
            raise BadRequest("recurrence.weekdays must be a list of weekdays (1 = Monday ... 7 = Sunday)")
        chosen = sorted({v.integer(d, "recurrence.weekdays", 1, 7) for d in days})
        if chosen and base.start_date.isoweekday() not in chosen:
            chosen = sorted({*chosen, base.start_date.isoweekday()})          # the first occurrence is the event itself
        rule["weekdays"] = [] if chosen == [base.start_date.isoweekday()] else chosen
    elif raw.get("weekdays"):
        raise BadRequest("recurrence.weekdays only applies to a weekly repeat")
    if freq == "monthly":
        rule["month_mode"] = v.choice(raw.get("month_mode", "date"), MONTH_MODES, "recurrence.month_mode")
    elif raw.get("month_mode") not in (None, "date"):
        raise BadRequest("recurrence.month_mode only applies to a monthly repeat")
    until, count = raw.get("until"), raw.get("count")
    if until is not None and count is not None:
        raise BadRequest("Choose either an end date or a number of repeats, not both.")
    if until is not None:
        if not isinstance(until, str):
            raise BadRequest("recurrence.until must be a date like 2026-12-31")
        try:
            last = date.fromisoformat(until)
        except ValueError as exc:
            raise BadRequest("recurrence.until must be a date like 2026-12-31") from exc
        if last < base.start_date:
            raise BadRequest("The repeat can not end before the event starts.", hint="Choose a later end date.")
        rule["until"] = last.isoformat()
    else:
        rule["until"] = None
    rule["count"] = v.integer(count, "recurrence.count", 1, MAX_COUNT, nullable=True)
    return rule


def validate_exceptions(raw: Any, event: Mapping[str, Any], rule: dict[str, Any] | None) -> dict[str, Any]:
    """Canonical exceptions. Keys that are not occurrences of the rule are dropped (the rule or the start may have changed)."""
    if raw is None or raw == {}:
        return {}
    if rule is None:
        return {}
    if not isinstance(raw, dict):
        raise BadRequest("exceptions must be an object")
    if len(raw) > MAX_EXCEPTIONS:
        raise BadRequest(f"A repeating event can have at most {MAX_EXCEPTIONS:,} single changes.")
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        key = v.instant(key, "exception key")
        if value is None:
            cleaned[key] = None
            continue
        if not isinstance(value, dict) or set(value) - {"start_at", "end_at"}:
            raise BadRequest("A moved occurrence needs start_at and end_at.")
        start = v.instant(value.get("start_at"), "start_at")
        end = v.instant(value.get("end_at"), "end_at")
        if end <= start:
            raise BadRequest("An event has to end after it starts.")
        cleaned[key] = {"start_at": start, "end_at": end}
    if cleaned:
        valid = _natural_keys(event, rule, max(cleaned))
        cleaned = {k: val for k, val in cleaned.items() if k in valid}
    return cleaned


# ---------------------------------------------------------------------------------------------------------------- the rule's dates
def _month_last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The ``nth`` (1-4, or 5 meaning "the last") ``weekday`` (Monday = 0) of a month."""
    if nth >= 5:
        last = date(year, month, _month_last_day(year, month))
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))


def _dates(base: _Base, rule: Mapping[str, Any]) -> Iterator[date]:
    """Calendar dates of the occurrences, in order, starting with the event's own."""
    interval = int(rule["interval"])
    start = base.start_date
    freq = rule["freq"]
    if freq == "daily":
        k = 0
        while True:
            yield start + timedelta(days=k * interval)
            k += 1
    elif freq == "weekly":
        weekdays = list(rule.get("weekdays") or [start.isoweekday()])
        monday = start - timedelta(days=start.weekday())
        k = 0
        while True:
            block = monday + timedelta(weeks=k * interval)
            for day in weekdays:
                candidate = block + timedelta(days=day - 1)
                if candidate >= start:
                    yield candidate
            k += 1
    elif freq == "monthly":
        by_weekday = rule.get("month_mode") == "weekday"
        nth = (start.day - 1) // 7 + 1
        k = 0
        while True:
            index = start.year * 12 + (start.month - 1) + k * interval
            year, month = divmod(index, 12)
            month += 1
            if year > 9999:
                return
            yield _nth_weekday(year, month, start.weekday(), nth) if by_weekday else date(year, month, min(start.day, _month_last_day(year, month)))
            k += 1
    else:
        k = 0
        while True:
            year = start.year + k * interval
            if year > 9999:
                return
            yield date(year, start.month, min(start.day, _month_last_day(year, start.month)))
            k += 1


def _natural(event: Mapping[str, Any], rule: Mapping[str, Any]) -> Iterator[tuple[datetime, datetime]]:
    """Every occurrence of the rule (UTC start, end), in order, honouring ``until`` and ``count``; unbounded rules never end."""
    base = _Base(event)
    until = date.fromisoformat(rule["until"]) if rule.get("until") else None
    limit = rule.get("count")
    produced = 0
    for steps, day in enumerate(_dates(base, rule)):
        if steps >= _MAX_STEPS or (until is not None and day > until) or (limit is not None and produced >= limit):
            return
        produced += 1
        try:
            yield base.at(day)
        except (OverflowError, ValueError):
            return


def _natural_keys(event: Mapping[str, Any], rule: Mapping[str, Any], last_key: str) -> set[str]:
    """The keys of every occurrence up to and including ``last_key`` (an instant string)."""
    keys: set[str] = set()
    for start, _end in _natural(event, rule):
        key = _iso(start)
        if key > last_key:
            break
        keys.add(key)
    return keys


# ---------------------------------------------------------------------------------------------------------------- expansion
def loads(event: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    rule = event.get("recurrence")
    exceptions = event.get("exceptions")
    if isinstance(rule, str):
        rule = json.loads(rule) if rule else None
    if isinstance(exceptions, str):
        exceptions = json.loads(exceptions) if exceptions else {}
    return rule, exceptions or {}


def is_recurring(event: Mapping[str, Any]) -> bool:
    return loads(event)[0] is not None


def occurrences(event: Mapping[str, Any], window_start: str, window_end: str, *, limit: int = MAX_OCCURRENCES) -> list[dict[str, Any]]:
    """The occurrences of a repeating event that overlap ``[window_start, window_end)``, earliest first.

    Each is the event's own record with ``start_at`` / ``end_at`` of that occurrence and two extra fields: ``occurrence`` (the key to
    cancel or move it by) and ``moved``. An event that does not repeat has none (it is its own and only occurrence).
    """
    rule, exceptions = loads(event)
    if rule is None:
        return []
    out: list[dict[str, Any]] = []

    def emit(key: str, start: str, end: str, moved: bool) -> None:
        if end > window_start and start < window_end:
            record = {k: val for k, val in event.items() if k not in ("recurrence", "exceptions")}
            record.update(recurrence=rule, start_at=start, end_at=end, occurrence=key, moved=moved)
            out.append(record)

    for start, end in _natural(event, rule):
        if _iso(start) >= window_end:
            break
        key = _iso(start)
        if key in exceptions:
            continue                                       # cancelled, or moved (handled below)
        emit(key, key, _iso(end), False)
        if len(out) >= limit:
            break
    for key, change in exceptions.items():
        if change is not None:
            emit(key, change["start_at"], change["end_at"], True)
    out.sort(key=lambda r: (r["start_at"], r["end_at"], r["occurrence"]))
    return out[:limit]


def next_occurrence(event: Mapping[str, Any], after: str) -> dict[str, str] | None:
    """The first occurrence that starts after the instant ``after``: ``{"occurrence", "start_at", "end_at"}``, or ``None`` when the
    series has ended. Cancelled occurrences are skipped and moved ones are judged at their new time."""
    rule, exceptions = loads(event)
    if rule is None:
        return None
    best: dict[str, str] | None = None
    for start, end in _natural(event, rule):
        key = _iso(start)
        if key in exceptions:
            continue
        if key > after:
            best = {"occurrence": key, "start_at": key, "end_at": _iso(end)}
            break
    for key, change in exceptions.items():
        if change is not None and change["start_at"] > after and (best is None or change["start_at"] < best["start_at"]):
            best = {"occurrence": key, "start_at": change["start_at"], "end_at": change["end_at"]}
    return best


# ---------------------------------------------------------------------------------------------------------------- iCalendar
def rrule(event: Mapping[str, Any]) -> str | None:
    """The ``RRULE`` value (without the property name) for an event's rule, or ``None``."""
    rule, _ = loads(event)
    if rule is None:
        return None
    base = _Base(event)
    parts = [f"FREQ={rule['freq'].upper()}"]
    if int(rule["interval"]) != 1:
        parts.append(f"INTERVAL={int(rule['interval'])}")
    if rule["freq"] == "weekly" and rule.get("weekdays"):
        parts.append("BYDAY=" + ",".join(_DAY_CODES[d - 1] for d in rule["weekdays"]))
    if rule["freq"] == "monthly" and rule.get("month_mode") != "weekday" and base.start_date.day == 31:
        parts.append("BYMONTHDAY=-1")                       # "the 31st" falls on the last day of shorter months here, as in the planner
    if rule["freq"] == "monthly" and rule.get("month_mode") == "weekday":
        nth = (base.start_date.day - 1) // 7 + 1
        parts.append(f"BYDAY={'-1' if nth >= 5 else nth}{_DAY_CODES[base.start_date.weekday()]}")
    if rule.get("until"):
        last = date.fromisoformat(rule["until"])
        if base.all_day:
            parts.append("UNTIL=" + last.strftime("%Y%m%d"))
        else:                                              # a timed rule ends at a UTC instant: the end of that local day
            end_of_day = datetime.combine(last, time(23, 59, 59), tzinfo=base.zone)
            parts.append("UNTIL=" + end_of_day.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if rule.get("count"):
        parts.append(f"COUNT={int(rule['count'])}")
    return ";".join(parts)
