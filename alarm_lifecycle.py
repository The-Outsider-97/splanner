"""Deterministic, pure alarm state transitions for SPlanner v2.4.

The SQLite store remains authoritative. This module only calculates changes;
callers persist returned fields in a single transaction with revision checks.
All timestamps are absolute UTC instants, not browser wall-clock timers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

DEFAULT_RING_TIMEOUT_SECONDS = 120
DEFAULT_AUTO_SNOOZE_MINUTES = 5


def _date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Alarm timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def advance(
    reminder: Mapping[str, Any],
    now: datetime,
    *,
    ring_timeout_seconds: int = DEFAULT_RING_TIMEOUT_SECONDS,
    automatic_snooze_minutes: int = DEFAULT_AUTO_SNOOZE_MINUTES,
) -> dict[str, Any]:
    """Compute the *minimal* state update due at ``now``.

    Ring and snooze phases are calculated from persisted absolute timestamps,
    so delayed polling, tab throttling, reload and sleep do not shift the
    120-second/300-second boundaries. Calculating phase arithmetically avoids
    unbounded loops after an extremely long application suspension.

    A reminder has one authoritative lifecycle; calling again at the same
    instant is idempotent. This method never dismisses a reminder.
    """
    if ring_timeout_seconds <= 0 or automatic_snooze_minutes <= 0:
        raise ValueError("Ring timeout and automatic snooze must be positive")
    if reminder["status"] != "pending":
        return {}
    state = reminder.get("alarm_state") or "scheduled"
    if state not in {"scheduled", "ringing", "snoozed"}:
        return {}
    if now.tzinfo is None:
        raise ValueError("Now must be timezone-aware")
    now = now.astimezone(timezone.utc)

    anchor = _date(reminder.get("ring_started_at") or reminder["due_at"]) if state == "ringing" else _date(reminder["due_at"])
    if now < anchor:
        return {}

    cycle_seconds = ring_timeout_seconds + automatic_snooze_minutes * 60
    elapsed = int((now - anchor).total_seconds())
    completed_cycles, seconds_into_cycle = divmod(elapsed, cycle_seconds)
    cycle_start = anchor + timedelta(seconds=completed_cycles * cycle_seconds)
    ringing = seconds_into_cycle < ring_timeout_seconds
    completed_rings = completed_cycles + (0 if ringing else 1)

    if ringing:
        updates: dict[str, Any] = {
            "alarm_state": "ringing",
            "due_at": _iso(cycle_start),
            "ring_started_at": _iso(cycle_start),
            "last_transition_at": _iso(cycle_start),
        }
    else:
        updates = {
            "alarm_state": "snoozed",
            "due_at": _iso(cycle_start + timedelta(seconds=cycle_seconds)),
            "ring_started_at": None,
            "last_transition_at": _iso(cycle_start + timedelta(seconds=ring_timeout_seconds)),
            "last_snooze_kind": "automatic",
        }
    if completed_rings:
        updates["snooze_count"] = int(reminder.get("snooze_count") or 0) + completed_rings
    changes = {key: value for key, value in updates.items() if reminder.get(key) != value}
    if changes:
        changes["alarm_revision"] = int(reminder.get("alarm_revision") or 0) + 1
    return changes


def manual_snooze(reminder: Mapping[str, Any], now: datetime, minutes: int) -> dict[str, Any]:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 43200:
        raise ValueError("Manual snooze must be between 1 and 43200 minutes")
    return {
        "due_at": _iso(now + timedelta(minutes=minutes)),
        "status": "pending",
        "alarm_state": "snoozed",
        "ring_started_at": None,
        "last_transition_at": _iso(now),
        "last_snooze_kind": "manual",
        "snooze_count": int(reminder.get("snooze_count") or 0) + 1,
        "alarm_revision": int(reminder.get("alarm_revision") or 0) + 1,
    }


def reset(reminder: Mapping[str, Any], due_at: str, now: datetime, *, dismissed: bool = False) -> dict[str, Any]:
    """Start a new occurrence or respond to an edited event's trigger time."""
    return {
        "due_at": due_at,
        "original_due_at": due_at,
        "status": "dismissed" if dismissed else "pending",
        "alarm_state": "dismissed" if dismissed else "scheduled",
        "ring_started_at": None,
        "snooze_count": 0,
        "last_transition_at": _iso(now),
        "last_snooze_kind": None,
        "alarm_revision": int(reminder.get("alarm_revision") or 0) + 1,
    }


def dismiss(reminder: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "status": "dismissed", "alarm_state": "dismissed", "ring_started_at": None,
        "last_transition_at": _iso(now), "alarm_revision": int(reminder.get("alarm_revision") or 0) + 1,
    }
