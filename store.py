"""SPlanner's records: SQLite for events, tasks, reminders, note metadata and sticky positions, and canonical SLDC files for note text.

Rules this module keeps:

* Note *text* lives only in ``notes/<id>.sldc`` (written by SLAI's ``src.utils.sldc``). The database holds metadata plus two derived
  columns (``excerpt`` and ``search_text``) that can always be rebuilt from the files; they exist so lists and search stay fast.
* Every change that touches several rows (an event and its reminder, a note's file and its row) happens in one transaction.
* ``version`` is an optimistic-lock counter. A client that sends the version it last saw gets a 409 instead of overwriting a newer edit.
* All instants are UTC strings with an explicit offset, so ``ORDER BY`` and ``<=`` on them are correct.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..tools import slai, validation as v
from . import recurrence
from ..tools.database import Database, Migration
from ..tools.errors import BadRequest, Conflict, DependencyMissing, LimitExceeded, NotFound, StorageError
from ..tools.storage import atomic_write

COLORS = ("blue", "violet", "green", "pink", "amber")
PRIORITIES = ("low", "normal", "high")
TASK_STATUSES = ("open", "done")
NOTE_KINDS = ("note", "sticky")
LIMITS = {"events": 50_000, "tasks": 50_000, "notes": 20_000, "reminders": 20_000, "stickies": 500}
TITLE = 180
BODY = 5_000
NOTE_TEXT = 50_000
STICKY_TEXT = 2_000
MAX_REMIND_MINUTES = 10_080          # a week before
MAX_SNOOZE_MINUTES = 43_200          # a month
STICKY_MIN = (150.0, 110.0)          # the smallest a sticky note can be resized to (width, height in px)
STICKY_MAX = (1000.0, 1000.0)
WINDOW_BEFORE_DAYS = 300             # the repeats sent with a snapshot: this far back and ...
WINDOW_AFTER_DAYS = 400              # ... this far ahead of now
MAX_WINDOW_DAYS = 900

# ---------------------------------------------------------------------------------------------------------------- migrations
# v1 is the schema SPlanner 0.1 created (without a version marker), written with IF NOT EXISTS so an existing 0.1 database is simply
# stamped as version 1 and keeps every row. v2 adds what 1.0 needs and rebuilds the derived note columns from the SLDC files.

V1 = (
    """CREATE TABLE IF NOT EXISTS notes (
         id TEXT PRIMARY KEY, title TEXT NOT NULL, version INTEGER NOT NULL,
         created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS events (
         id TEXT PRIMARY KEY, title TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL, time_zone TEXT NOT NULL,
         all_day INTEGER NOT NULL, description TEXT NOT NULL, color TEXT NOT NULL, note_id TEXT REFERENCES notes(id),
         version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, CHECK(end_at > start_at))""",
    "CREATE INDEX IF NOT EXISTS events_by_time ON events(start_at,end_at)",
    """CREATE TABLE IF NOT EXISTS tasks (
         id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, priority TEXT NOT NULL, due_at TEXT,
         note_id TEXT REFERENCES notes(id), version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS tasks_by_due ON tasks(due_at)",
    """CREATE TABLE IF NOT EXISTS reminders (
         id TEXT PRIMARY KEY, title TEXT NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL, entity_type TEXT, entity_id TEXT,
         created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status,due_at)",
    """CREATE TABLE IF NOT EXISTS stickies (
         id TEXT PRIMARY KEY, note_id TEXT NOT NULL UNIQUE REFERENCES notes(id), x REAL NOT NULL, y REAL NOT NULL,
         color TEXT NOT NULL, version INTEGER NOT NULL)""",
)


def _collapse(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _fold(*parts: str) -> str:
    return "\n".join(parts).casefold()


def _v2_steps(note_dir: Path) -> tuple[Any, ...]:
    def backfill_notes(conn: sqlite3.Connection) -> None:
        """Mark sticky-backed notes, then derive excerpt and search text from each note's SLDC file (unreadable files are left blank)."""
        conn.execute("UPDATE notes SET kind='sticky' WHERE id IN (SELECT note_id FROM stickies)")
        conn.execute("UPDATE tasks SET completed_at=updated_at WHERE status='done' AND completed_at IS NULL")
        try:
            module = slai.sldc()
        except DependencyMissing:
            return
        for note_id, title in conn.execute("SELECT id, title FROM notes").fetchall():
            try:
                text = module.extract_text(module.read_package(note_dir / f"{note_id}.sldc"))
            except (OSError, ValueError):
                continue
            conn.execute("UPDATE notes SET excerpt=?, search_text=? WHERE id=?", (_collapse(text), _fold(title, text), note_id))
            conn.execute("UPDATE stickies SET body=? WHERE note_id=?", (text, note_id))          # a sticky shows its whole text, not the excerpt

    return (
        "ALTER TABLE notes ADD COLUMN kind TEXT NOT NULL DEFAULT 'note'",
        "ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE notes ADD COLUMN excerpt TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE notes ADD COLUMN search_text TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE stickies ADD COLUMN body TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE events ADD COLUMN location TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE events ADD COLUMN remind_before INTEGER",
        "ALTER TABLE tasks ADD COLUMN description TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE tasks ADD COLUMN completed_at TEXT",
        "CREATE INDEX IF NOT EXISTS tasks_by_status ON tasks(status,due_at)",
        "CREATE INDEX IF NOT EXISTS reminders_by_entity ON reminders(entity_type,entity_id)",
        backfill_notes,
    )


V3 = (
    "ALTER TABLE events ADD COLUMN recurrence TEXT",                                       # the repeat rule as JSON, NULL when the event does not repeat
    "ALTER TABLE events ADD COLUMN exceptions TEXT NOT NULL DEFAULT '{}'",                 # cancelled / moved single occurrences, as JSON
    "ALTER TABLE reminders ADD COLUMN occurrence_at TEXT",                                 # which occurrence of a repeating event an alert is for
    "ALTER TABLE stickies ADD COLUMN width REAL",                                          # NULL: the default size
    "ALTER TABLE stickies ADD COLUMN height REAL",
)


def migrations(note_dir: Path) -> list[Migration]:
    return [Migration(1, "SPlanner 0.1 schema", V1), Migration(2, "events: location and reminders; tasks: notes and completion time; "
                                                                  "notes: kind, pin and derived search columns", _v2_steps(note_dir)),
            Migration(3, "events: repeat rule and single changes; reminders: occurrence; stickies: size", V3)]


def _local_date(instant: str, zone_name: str) -> str:
    return v.parse_instant(instant).astimezone(ZoneInfo(zone_name)).date().isoformat()


class PlannerStore:
    def __init__(self, data_dir: Path | str, *, backups_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.note_dir = self.data_dir / "notes"
        self.backups_dir = Path(backups_dir) if backups_dir else self.data_dir / "backups"
        try:
            self.note_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            from ..tools.errors import classify_oserror
            raise classify_oserror(exc, "create the data folder") from exc
        self.db = Database(self.data_dir / "planner.sqlite3", migrations(self.note_dir), backups_dir=self.backups_dir)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------------------------------------------------ plumbing
    def _count(self, table: str, where: str = "1=1") -> int:
        return int(self.db.scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}") or 0)

    def _room(self, table: str, noun: str) -> None:
        if self._count(table) >= LIMITS[table]:
            raise LimitExceeded(f"SPlanner keeps at most {LIMITS[table]:,} {noun}.", hint="Delete some you no longer need.")

    def _fetch(self, table: str, item_id: str, noun: str) -> dict[str, Any]:
        row = self.db.row(f"SELECT * FROM {table} WHERE id=?", (v.record_id(item_id),))
        if row is None:
            raise NotFound(f"That {noun} no longer exists.", hint="It may have been deleted. Reload to see what is there now.")
        return row

    @staticmethod
    def _check_version(record: dict[str, Any], version: Any, noun: str) -> None:
        if version is None:
            return
        if type(version) is not int:
            raise BadRequest("The version must be a whole number.")
        if record["version"] != version:
            raise Conflict(f"This {noun} was changed somewhere else, so your edit was not saved.",
                           hint="Reload to see the latest version, then apply your change again.", details={"current_version": record["version"]})

    def _update(self, table: str, record: dict[str, Any], changes: dict[str, Any]) -> None:
        sets = dict(changes)
        if "version" in record:
            sets["version"] = record["version"] + 1
        if "updated_at" in record:
            sets["updated_at"] = v.utc_now()
        names = ", ".join(f"{name}=?" for name in sets)
        guard, params = ("id=? AND version=?", (record["id"], record["version"])) if "version" in record else ("id=?", (record["id"],))
        cursor = self.db.execute(f"UPDATE {table} SET {names} WHERE {guard}", (*sets.values(), *params))
        if cursor.rowcount != 1:
            raise Conflict("This item was changed at the same moment, so your edit was not saved.", hint="Try again.")

    @staticmethod
    def _unknown(data: dict[str, Any], allowed: set[str], noun: str) -> None:
        extra = sorted(set(data) - allowed)
        if extra:
            raise BadRequest(f"Unknown {noun} field: {extra[0]}.")

    # ------------------------------------------------------------------------------------------------------------ events
    EVENT_FIELDS = {"title", "start_at", "end_at", "time_zone", "all_day", "description", "location", "color", "remind_before", "recurrence", "exceptions", "version"}

    @staticmethod
    def _event_out(row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["all_day"] = bool(out["all_day"])
        out["recurrence"], out["exceptions"] = recurrence.loads(row)
        return out

    def _event_values(self, data: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validated column values for the keys present in ``data`` (all keys when creating)."""
        creating = base is None
        out: dict[str, Any] = {}

        def present(key: str) -> bool:
            return key in data or (creating and key in ("title", "start_at", "end_at"))

        if present("title"):
            out["title"] = v.text(data.get("title"), "title", TITLE)
        if present("start_at"):
            out["start_at"] = v.instant(data.get("start_at"), "start_at")
        if present("end_at"):
            out["end_at"] = v.instant(data.get("end_at"), "end_at")
        if "time_zone" in data or creating:
            out["time_zone"] = v.zone(data.get("time_zone", "UTC"))
        if "all_day" in data or creating:
            out["all_day"] = int(v.boolean(data.get("all_day", False), "all_day"))
        if "description" in data or creating:
            out["description"] = v.text(data.get("description", ""), "description", BODY, empty=True, multiline=True)
        if "location" in data or creating:
            out["location"] = v.text(data.get("location", ""), "location", 200, empty=True)
        if "color" in data or creating:
            out["color"] = v.choice(data.get("color", "blue"), COLORS, "color")
        if "remind_before" in data or creating:
            out["remind_before"] = v.integer(data.get("remind_before"), "remind_before", 0, MAX_REMIND_MINUTES, nullable=True)
        start = out.get("start_at", (base or {}).get("start_at"))
        end = out.get("end_at", (base or {}).get("end_at"))
        if start is not None and end is not None and end <= start:
            raise BadRequest("An event has to end after it starts.", hint="Move the end time later than the start time.")
        shape_changed = any(key in out for key in ("start_at", "end_at", "time_zone", "all_day"))
        if creating or "recurrence" in data or "exceptions" in data or shape_changed:
            merged = {**(base or {}), **out}
            current_rule, current_exceptions = recurrence.loads(base) if base else (None, {})
            if "recurrence" in data:
                rule = recurrence.validate_rule(data["recurrence"], merged)
            else:                                                  # a moved or resized series keeps its rule, re-checked against the new start
                rule = recurrence.validate_rule(current_rule, merged)
            changes = data["exceptions"] if "exceptions" in data else current_exceptions
            out["recurrence"] = json.dumps(rule, separators=(",", ":")) if rule else None
            out["exceptions"] = json.dumps(recurrence.validate_exceptions(changes, merged, rule), separators=(",", ":"))
        return out

    def _sync_event_reminder(self, event: dict[str, Any]) -> None:
        """Keep the reminder that belongs to an event in step with it: one pending reminder at ``start - remind_before``.

        A repeating event has one reminder too, always for its *next* occurrence; dismissing it (or the next occurrence's alert time
        arriving) moves it on, see ``change_reminder`` and ``due_reminders``."""
        linked = self.db.rows("SELECT * FROM reminders WHERE entity_type='event' AND entity_id=?", (event["id"],))
        minutes = event.get("remind_before")
        if minutes is None:
            if linked:
                self.db.execute("DELETE FROM reminders WHERE entity_type='event' AND entity_id=?", (event["id"],))
            return
        now = v.utc_now()
        occurrence_at: str | None = None
        if recurrence.is_recurring(event):
            nxt = recurrence.next_occurrence(event, now)
            if nxt is None:                                        # the series has ended: nothing left to alert about
                self.db.execute("UPDATE reminders SET status='dismissed', updated_at=? WHERE entity_type='event' AND entity_id=?", (now, event["id"]))
                return
            begins, occurrence_at, over = nxt["start_at"], nxt["occurrence"], False
        else:
            begins = event["start_at"]
            over = begins <= now                                   # the event is already under way or finished: nothing to alert about
        due = self._alert_time(begins, minutes)
        if not linked:
            self.db.execute("INSERT INTO reminders (id,title,due_at,status,entity_type,entity_id,created_at,updated_at,occurrence_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (v.new_id(), event["title"], due, "dismissed" if over else "pending", "event", event["id"], now, now, occurrence_at))
            return
        keep, *extra = linked
        for stale in extra:
            self.db.execute("DELETE FROM reminders WHERE id=?", (stale["id"],))
        same = keep["due_at"] == due and keep["occurrence_at"] == occurrence_at
        status = keep["status"] if same else ("dismissed" if over else "pending")
        self.db.execute("UPDATE reminders SET title=?, due_at=?, status=?, occurrence_at=?, updated_at=? WHERE id=?",
                        (event["title"], due, status, occurrence_at, now, keep["id"]))

    @staticmethod
    def _alert_time(begins: str, minutes: int) -> str:
        return (v.parse_instant(begins) - timedelta(minutes=minutes)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def list_events(self, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        """Events overlapping ``[start, end)`` (either bound optional), earliest first."""
        where, params = [], []
        if end is not None:
            where.append("start_at < ?")
            params.append(v.instant(end, "to"))
        if start is not None:
            where.append("(end_at > ? OR recurrence IS NOT NULL)")         # a repeating event may have later occurrences
            params.append(v.instant(start, "from"))
        rows = self.db.rows(f"SELECT * FROM events {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY start_at, end_at", params)
        return [self._event_out(r) for r in rows]

    def get_event(self, item_id: str) -> dict[str, Any]:
        return self._event_out(self._fetch("events", item_id, "event"))

    def create_event(self, data: dict[str, Any]) -> dict[str, Any]:
        self._unknown(data, self.EVENT_FIELDS, "event")
        values = self._event_values(data)
        self._room("events", "events")
        now, item_id = v.utc_now(), v.new_id()
        values.update(id=item_id, version=1, created_at=now, updated_at=now, note_id=None)
        with self.db.transaction():
            self.db.execute(f"INSERT INTO events ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))
            created = self._fetch("events", item_id, "event")
            self._sync_event_reminder(created)
        return self._event_out(created)

    def update_event(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self._unknown(data, self.EVENT_FIELDS, "event")
        with self.db.transaction():
            old = self._fetch("events", item_id, "event")
            self._check_version(old, data.get("version"), "event")
            changes = self._event_values(data, old)
            if changes:
                self._update("events", old, changes)
            new = self._fetch("events", item_id, "event")
            self._sync_event_reminder(new)
        return self._event_out(new)

    def delete_event(self, item_id: str) -> None:
        with self.db.transaction():
            self._fetch("events", item_id, "event")
            self.db.execute("DELETE FROM reminders WHERE entity_type='event' AND entity_id=?", (item_id,))
            self.db.execute("DELETE FROM events WHERE id=?", (item_id,))

    # ------------------------------------------------------------------------------------------------------------ tasks
    TASK_FIELDS = {"title", "description", "status", "priority", "due_at", "version"}

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.db.rows("SELECT * FROM tasks ORDER BY created_at DESC, id")

    def get_task(self, item_id: str) -> dict[str, Any]:
        return self._fetch("tasks", item_id, "task")

    def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        self._unknown(data, self.TASK_FIELDS, "task")
        title = v.text(data.get("title"), "title", TITLE)
        status = v.choice(data.get("status", "open"), TASK_STATUSES, "status")
        row = {"title": title, "description": v.text(data.get("description", ""), "description", BODY, empty=True, multiline=True),
               "status": status, "priority": v.choice(data.get("priority", "normal"), PRIORITIES, "priority"),
               "due_at": v.instant(data.get("due_at"), "due_at", nullable=True)}
        self._room("tasks", "tasks")
        now, item_id = v.utc_now(), v.new_id()
        row.update(id=item_id, version=1, created_at=now, updated_at=now, completed_at=now if status == "done" else None, note_id=None)
        with self.db.transaction():
            self.db.execute(f"INSERT INTO tasks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        return self._fetch("tasks", item_id, "task")

    def update_task(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self._unknown(data, self.TASK_FIELDS, "task")
        with self.db.transaction():
            old = self._fetch("tasks", item_id, "task")
            self._check_version(old, data.get("version"), "task")
            changes: dict[str, Any] = {}
            if "title" in data:
                changes["title"] = v.text(data["title"], "title", TITLE)
            if "description" in data:
                changes["description"] = v.text(data["description"], "description", BODY, empty=True, multiline=True)
            if "priority" in data:
                changes["priority"] = v.choice(data["priority"], PRIORITIES, "priority")
            if "due_at" in data:
                changes["due_at"] = v.instant(data["due_at"], "due_at", nullable=True)
            if "status" in data:
                status = v.choice(data["status"], TASK_STATUSES, "status")
                if status != old["status"]:
                    changes["status"] = status
                    changes["completed_at"] = v.utc_now() if status == "done" else None
            if changes:
                self._update("tasks", old, changes)
        return self._fetch("tasks", item_id, "task")

    def delete_task(self, item_id: str) -> None:
        with self.db.transaction():
            self._fetch("tasks", item_id, "task")
            self.db.execute("DELETE FROM reminders WHERE entity_type='task' AND entity_id=?", (item_id,))
            self.db.execute("DELETE FROM tasks WHERE id=?", (item_id,))

    def clear_completed_tasks(self) -> int:
        with self.db.transaction():
            count = self._count("tasks", "status='done'")
            self.db.execute("DELETE FROM tasks WHERE status='done'")
        return count

    # ------------------------------------------------------------------------------------------------------------ notes (SLDC)
    NOTE_FIELDS = {"title", "content", "pinned", "version"}

    def _package_path(self, note_id: str) -> Path:
        return self.note_dir / f"{v.record_id(note_id)}.sldc"

    def _read_package(self, note_id: str) -> Any:
        module = slai.sldc()
        try:
            return module.read_package(self._package_path(note_id))
        except FileNotFoundError as exc:
            raise StorageError("The file for this note is missing.", reason="corrupt", details={"note": note_id, "problem": "missing"}) from exc
        except ValueError as exc:                                  # SLDCError derives from ValueError
            raise StorageError("This note's file could not be read.", reason="corrupt", details={"note": note_id, "error": type(exc).__name__}) from exc

    def _write_package(self, note_id: str, title: str, text: str, *, overwrite: bool, package: Any = None) -> None:
        module = slai.sldc()
        if package is None:
            package = module.new_package(title, [module.make_block("paragraph", text)], doc_id=f"splanner-{note_id}")
        else:
            package.document["title"] = title
            package.document["blocks"][0]["text"] = text
        try:
            data = module.dumps_package(package)                      # SLDC builds and validates the bytes ...
        except ValueError as exc:
            raise BadRequest(f"The note could not be saved as an SLDC document: {exc}") from exc
        try:
            atomic_write(self._package_path(note_id), data, exclusive=not overwrite)   # ... the kit writes them, even where renaming fails
        except FileExistsError as exc:
            raise Conflict("A note with this id already exists.") from exc

    @staticmethod
    def _note_out(row: dict[str, Any]) -> dict[str, Any]:
        out = {key: row[key] for key in ("id", "title", "kind", "excerpt", "version", "created_at", "updated_at")}
        out["pinned"] = bool(row["pinned"])
        return out

    def list_notes(self) -> list[dict[str, Any]]:
        rows = self.db.rows("SELECT * FROM notes WHERE kind='note' ORDER BY pinned DESC, updated_at DESC, id")
        return [self._note_out(r) for r in rows]

    def get_note(self, item_id: str) -> dict[str, Any]:
        row = self._fetch("notes", item_id, "note")
        package = self._read_package(item_id)
        module = slai.sldc()
        editable = not package.signatures and len(package.document["blocks"]) == 1 and package.document["blocks"][0]["type"] == "paragraph"
        return {**self._note_out(row), "content": module.extract_text(package), "editable": editable}

    def create_note(self, data: dict[str, Any], *, kind: str = "note") -> dict[str, Any]:
        self._unknown(data, self.NOTE_FIELDS, "note")
        title = v.text(data.get("title"), "title", TITLE)
        content = v.text(data.get("content", ""), "content", NOTE_TEXT if kind == "note" else STICKY_TEXT, empty=True, multiline=True)
        pinned = int(v.boolean(data.get("pinned", False), "pinned"))
        self._room("notes", "notes")
        slai.sldc()                                                 # fail early, with a clear message, when SLDC is unavailable
        item_id, now = v.new_id(), v.utc_now()
        self._write_package(item_id, title, content, overwrite=False)
        try:
            with self.db.transaction():
                self.db.execute("INSERT INTO notes (id,title,version,created_at,updated_at,kind,pinned,excerpt,search_text) VALUES (?,?,?,?,?,?,?,?,?)",
                                (item_id, title, 1, now, now, kind, pinned, _collapse(content), _fold(title, content)))
        except BaseException:
            self._package_path(item_id).unlink(missing_ok=True)       # no row means no note: do not leave the file behind
            raise
        return self.get_note(item_id)

    def update_note(self, item_id: str, data: dict[str, Any], *, require_version: bool = True) -> dict[str, Any]:
        self._unknown(data, self.NOTE_FIELDS, "note")
        with self.db.transaction():
            old = self._fetch("notes", item_id, "note")
            if require_version and data.get("version") is None:
                raise BadRequest("Send the version you are editing, so a newer edit is never overwritten.")
            self._check_version(old, data.get("version"), "note")
            changes: dict[str, Any] = {}
            if "pinned" in data:
                changes["pinned"] = int(v.boolean(data["pinned"], "pinned"))
            if "title" in data or "content" in data:
                package = self._read_package(item_id)
                if package.signatures:
                    raise Conflict("This note is signed, so it can't be changed here.", hint="Open it in Reader or Writer.")
                blocks = package.document["blocks"]
                if len(blocks) != 1 or blocks[0]["type"] != "paragraph":
                    raise Conflict("This note has advanced content that SPlanner can't edit without losing it.", hint="Open it in Reader or Writer.")
                title = v.text(data.get("title", old["title"]), "title", TITLE)
                content = v.text(data.get("content", blocks[0]["text"]), "content", NOTE_TEXT if old["kind"] == "note" else STICKY_TEXT,
                                 empty=True, multiline=True)
                changes.update(title=title, excerpt=_collapse(content), search_text=_fold(title, content))
                self._write_package(item_id, title, content, overwrite=True, package=package)
            if changes:
                self._update("notes", old, changes)
        return self.get_note(item_id)

    def delete_note(self, item_id: str) -> None:
        with self.db.transaction():
            row = self._fetch("notes", item_id, "note")
            if row["kind"] == "sticky":
                raise BadRequest("Delete a sticky note from the board.")
            self.db.execute("UPDATE events SET note_id=NULL WHERE note_id=?", (item_id,))
            self.db.execute("UPDATE tasks SET note_id=NULL WHERE note_id=?", (item_id,))
            self.db.execute("DELETE FROM notes WHERE id=?", (item_id,))
        self._package_path(item_id).unlink(missing_ok=True)

    def note_file(self, item_id: str) -> tuple[bytes, str]:
        """The note's ``.sldc`` bytes and a friendly file name, for opening it in Reader or Writer."""
        row = self._fetch("notes", item_id, "note")
        try:
            body = self._package_path(item_id).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("The file for this note is missing.", reason="corrupt", details={"note": item_id}) from exc
        safe = "".join(ch if ch.isalnum() or ch in " _-" else "_" for ch in row["title"]).strip() or "note"
        return body, f"{safe[:60]}.sldc"

    # ------------------------------------------------------------------------------------------------------------ reminders
    REMINDER_FIELDS = {"title", "due_at", "action", "minutes"}

    def list_reminders(self) -> list[dict[str, Any]]:
        """Pending reminders plus the 50 most recently dismissed, soonest first."""
        pending = self.db.rows("SELECT * FROM reminders WHERE status='pending' ORDER BY due_at, id")
        done = self.db.rows("SELECT * FROM reminders WHERE status!='pending' ORDER BY updated_at DESC LIMIT 50")
        return pending + done

    def due_reminders(self) -> list[dict[str, Any]]:
        """Pending reminders whose time has come. A repeating event's alert that nobody dealt with is replaced by the next occurrence's
        as soon as that one is due, so a series keeps alerting even if one alert was ignored."""
        now = v.utc_now()
        rows = self.db.rows("SELECT * FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at, id", (now,))
        moved = False
        for row in rows:
            if row["entity_type"] == "event" and row["occurrence_at"]:
                moved = self._supersede(row, now) or moved
        if moved:
            rows = self.db.rows("SELECT * FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at, id", (now,))
        return rows

    def _supersede(self, reminder: dict[str, Any], now: str) -> bool:
        event = self.db.row("SELECT * FROM events WHERE id=?", (reminder["entity_id"],))
        if event is None or event["remind_before"] is None or not recurrence.is_recurring(event):
            return False
        nxt = recurrence.next_occurrence(event, now)
        if nxt is None or nxt["occurrence"] == reminder["occurrence_at"]:
            return False
        due = self._alert_time(nxt["start_at"], event["remind_before"])
        if due > now:
            return False
        self.db.execute("UPDATE reminders SET title=?, due_at=?, occurrence_at=?, updated_at=? WHERE id=?", (event["title"], due, nxt["occurrence"], now, reminder["id"]))
        return True

    def _advance_reminder(self, reminder: dict[str, Any], now: str) -> dict[str, Any] | None:
        """For a repeating event's reminder that was just dismissed: the values that point it at the next occurrence whose alert is still
        to come, or ``None`` when the event does not repeat any more (or no occurrence is left)."""
        if reminder["entity_type"] != "event":
            return None
        event = self.db.row("SELECT * FROM events WHERE id=?", (reminder["entity_id"],))
        if event is None or event["remind_before"] is None or not recurrence.is_recurring(event):
            return None
        minutes = int(event["remind_before"])
        horizon = max(reminder["occurrence_at"] or now, self._alert_time(now, -minutes))          # not this occurrence, and not one whose alert is already past
        nxt = recurrence.next_occurrence(event, horizon)
        if nxt is None:
            return None
        return {"due_at": self._alert_time(nxt["start_at"], minutes), "occurrence_at": nxt["occurrence"], "status": "pending", "title": event["title"]}

    def create_reminder(self, data: dict[str, Any]) -> dict[str, Any]:
        self._unknown(data, {"title", "due_at"}, "reminder")
        title = v.text(data.get("title"), "title", TITLE)
        due = v.instant(data.get("due_at"), "due_at")
        self._room("reminders", "reminders")
        now, item_id = v.utc_now(), v.new_id()
        self.db.execute("INSERT INTO reminders (id,title,due_at,status,created_at,updated_at) VALUES (?,?,?,?,?,?)", (item_id, title, due, "pending", now, now))
        return self._fetch("reminders", item_id, "reminder")

    def change_reminder(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self._unknown(data, self.REMINDER_FIELDS, "reminder")
        with self.db.transaction():
            old = self._fetch("reminders", item_id, "reminder")
            changes: dict[str, Any] = {}
            action = data.get("action")
            if action is not None:
                action = v.choice(action, ("dismiss", "snooze", "restore"), "action")
            if "title" in data:
                changes["title"] = v.text(data["title"], "title", TITLE)
            if "due_at" in data:
                changes["due_at"] = v.instant(data["due_at"], "due_at")
                changes["status"] = "pending"
            if action == "dismiss":
                changes["status"] = "dismissed"
                changes.update(self._advance_reminder(old, v.utc_now()) or {})      # a repeating event's alert moves on to its next occurrence
            elif action == "restore":
                changes["status"] = "pending"
            elif action == "snooze":
                if "minutes" in data:
                    minutes = v.integer(data["minutes"], "minutes", 1, MAX_SNOOZE_MINUTES)
                    changes["due_at"] = (datetime.now(timezone.utc) + timedelta(minutes=int(minutes or 0))).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                elif "due_at" not in changes:
                    raise BadRequest("Say how long to snooze for.", hint="Send minutes, or a new due_at.")
                if changes["due_at"] <= v.utc_now():
                    raise BadRequest("A snoozed reminder has to be due in the future.")
                changes["status"] = "pending"
            if changes:
                self.db.execute("UPDATE reminders SET " + ", ".join(f"{k}=?" for k in changes) + ", updated_at=? WHERE id=?", (*changes.values(), v.utc_now(), old["id"]))
        return self._fetch("reminders", item_id, "reminder")

    def delete_reminder(self, item_id: str) -> None:
        with self.db.transaction():
            row = self._fetch("reminders", item_id, "reminder")
            if row["entity_type"] == "event":                      # an event's alert is removed by changing the event, not on its own
                self.db.execute("UPDATE events SET remind_before=NULL WHERE id=?", (row["entity_id"],))
            self.db.execute("DELETE FROM reminders WHERE id=?", (item_id,))

    # ------------------------------------------------------------------------------------------------------------ stickies
    STICKY_FIELDS = {"x", "y", "width", "height", "color", "content", "title", "version"}

    def _sticky_out(self, row: dict[str, Any]) -> dict[str, Any]:
        return {"id": row["id"], "note_id": row["note_id"], "x": row["x"], "y": row["y"], "width": row["width"], "height": row["height"],
                "color": row["color"], "version": row["version"], "text": row["body"]}

    def list_stickies(self) -> list[dict[str, Any]]:
        rows = self.db.rows("SELECT * FROM stickies ORDER BY rowid")
        return [self._sticky_out(r) for r in rows]

    def _sticky(self, item_id: str) -> dict[str, Any]:
        row = self.db.row("SELECT * FROM stickies WHERE id=?", (v.record_id(item_id),))
        if row is None:
            raise NotFound("That sticky note no longer exists.", hint="Reload to see the board as it is now.")
        return row

    @staticmethod
    def _coordinate(value: Any, label: str) -> float:
        return v.number(value, label, -10_000, 10_000)

    @staticmethod
    def _size(value: Any, label: str, low: float, high: float) -> float | None:
        """A sticky note's width or height in pixels; ``None`` is the default size."""
        return None if value is None else v.number(value, label, low, high)

    @staticmethod
    def _sticky_title(content: str) -> str:
        first = next((line.strip() for line in content.splitlines() if line.strip()), "")
        return (first[:60] or "Sticky note")

    def get_sticky(self, item_id: str) -> dict[str, Any]:
        sticky = self._sticky_out(self._sticky(item_id))
        sticky["text"] = self.get_note(sticky["note_id"])["content"]
        return sticky

    def create_sticky(self, data: dict[str, Any]) -> dict[str, Any]:
        self._unknown(data, self.STICKY_FIELDS, "sticky note")
        x = self._coordinate(data.get("x", 48), "x")
        y = self._coordinate(data.get("y", 48), "y")
        color = v.choice(data.get("color", "amber"), COLORS, "color")
        width = self._size(data.get("width"), "width", STICKY_MIN[0], STICKY_MAX[0])
        height = self._size(data.get("height"), "height", STICKY_MIN[1], STICKY_MAX[1])
        content = v.text(data.get("content", ""), "content", STICKY_TEXT, empty=True, multiline=True)
        self._room("stickies", "sticky notes")
        item_id = v.new_id()
        note = self.create_note({"title": self._sticky_title(content), "content": content}, kind="sticky")
        try:
            self.db.execute("INSERT INTO stickies (id,note_id,x,y,color,version,body,width,height) VALUES (?,?,?,?,?,?,?,?,?)",
                            (item_id, note["id"], x, y, color, 1, content, width, height))
        except BaseException:
            self.db.execute("DELETE FROM notes WHERE id=?", (note["id"],))
            self._package_path(note["id"]).unlink(missing_ok=True)
            raise
        return self.get_sticky(item_id)

    def update_sticky(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self._unknown(data, self.STICKY_FIELDS, "sticky note")
        with self.db.transaction():
            old = self._sticky(item_id)
            self._check_version(old, data.get("version"), "sticky note")
            changes: dict[str, Any] = {}
            if "x" in data:
                changes["x"] = self._coordinate(data["x"], "x")
            if "y" in data:
                changes["y"] = self._coordinate(data["y"], "y")
            if "color" in data:
                changes["color"] = v.choice(data["color"], COLORS, "color")
            if "width" in data:
                changes["width"] = self._size(data["width"], "width", STICKY_MIN[0], STICKY_MAX[0])
            if "height" in data:
                changes["height"] = self._size(data["height"], "height", STICKY_MIN[1], STICKY_MAX[1])
            if "content" in data:
                content = v.text(data["content"], "content", STICKY_TEXT, empty=True, multiline=True)
                note = self._fetch("notes", old["note_id"], "note")
                self.update_note(old["note_id"], {"title": self._sticky_title(content), "content": content, "version": note["version"]})
                changes["body"] = content                          # also a change to the sticky itself, so its version moves
            if changes:
                row = {"id": old["id"], "version": old["version"]}
                self._update("stickies", row, changes)
        return self.get_sticky(item_id)

    def delete_sticky(self, item_id: str) -> None:
        with self.db.transaction():
            sticky = self._sticky(item_id)
            self.db.execute("DELETE FROM stickies WHERE id=?", (item_id,))
            self.db.execute("DELETE FROM notes WHERE id=?", (sticky["note_id"],))
        self._package_path(sticky["note_id"]).unlink(missing_ok=True)

    # ------------------------------------------------------------------------------------------------------------ whole-workspace
    def default_window(self) -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        return ((now - timedelta(days=WINDOW_BEFORE_DAYS)).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                (now + timedelta(days=WINDOW_AFTER_DAYS)).strftime("%Y-%m-%dT%H:%M:%S+00:00"))

    def occurrences(self, start: str | None = None, end: str | None = None) -> dict[str, Any]:
        """The occurrences of every repeating event that fall in ``[start, end)`` (a default window around now), and the window itself.
        Events that do not repeat are not included: they are their own occurrence and the client already has them."""
        begin, finish = (self.default_window() if start is None and end is None else
                         (v.instant(start, "from"), v.instant(end, "to")))                          # type: ignore[arg-type]
        if finish <= begin:
            raise BadRequest("The window has to end after it starts.")
        if v.parse_instant(finish) - v.parse_instant(begin) > timedelta(days=MAX_WINDOW_DAYS):
            raise BadRequest(f"Ask for at most {MAX_WINDOW_DAYS} days at a time.")
        found: list[dict[str, Any]] = []
        for row in self.db.rows("SELECT * FROM events WHERE recurrence IS NOT NULL AND start_at < ? ORDER BY start_at, id", (finish,)):
            found.extend(recurrence.occurrences(row, begin, finish, limit=recurrence.MAX_OCCURRENCES - len(found)))
            if len(found) >= recurrence.MAX_OCCURRENCES:
                break
        found.sort(key=lambda r: (r["start_at"], r["end_at"], r["id"]))
        return {"occurrences": found, "window": {"from": begin, "to": finish}}

    def snapshot(self, start: str | None = None, end: str | None = None) -> dict[str, Any]:
        return {"events": self.list_events(), "tasks": self.list_tasks(), "notes": self.list_notes(), "reminders": self.list_reminders(),
                "stickies": self.list_stickies(), **self.occurrences(start, end)}

    def counts(self) -> dict[str, int]:
        return {"events": self._count("events"), "tasks": self._count("tasks"), "openTasks": self._count("tasks", "status='open'"),
                "notes": self._count("notes", "kind='note'"), "stickies": self._count("stickies"), "reminders": self._count("reminders", "status='pending'")}

    def search(self, query: str, limit: int = 40) -> list[dict[str, Any]]:
        needle = " ".join(query.casefold().split())
        if not needle:
            return []
        hits: list[dict[str, Any]] = []

        def add(kind: str, row: dict[str, Any], detail: str, at: str | None) -> None:
            hits.append({"kind": kind, "id": row["id"], "title": row["title"], "detail": detail, "at": at})

        for row in self.db.rows("SELECT id,title,description,location,start_at FROM events ORDER BY start_at DESC"):
            if needle in _fold(row["title"], row["description"], row["location"]):
                add("event", row, row["location"] or _collapse(row["description"], 80), row["start_at"])
        for row in self.db.rows("SELECT id,title,description,due_at,status FROM tasks ORDER BY created_at DESC"):
            if needle in _fold(row["title"], row["description"]):
                add("task", row, "Done" if row["status"] == "done" else _collapse(row["description"], 80), row["due_at"])
        for row in self.db.rows("SELECT id,title,excerpt,search_text,updated_at FROM notes WHERE kind='note' ORDER BY updated_at DESC"):
            if needle in row["search_text"] or needle in row["title"].casefold():
                add("note", row, row["excerpt"], row["updated_at"])
        for row in self.db.rows("SELECT n.id AS note_id, s.id AS id, n.title, n.excerpt, n.search_text FROM stickies s JOIN notes n ON n.id=s.note_id"):
            if needle in row["search_text"]:
                add("sticky", row, row["excerpt"], None)
        for row in self.db.rows("SELECT id,title,due_at FROM reminders WHERE status='pending' AND entity_type IS NULL ORDER BY due_at"):
            if needle in row["title"].casefold():
                add("reminder", row, "Reminder", row["due_at"])
        return hits[:limit]

    def export_all(self) -> dict[str, Any]:
        notes = []
        for note in self.db.rows("SELECT * FROM notes ORDER BY created_at"):
            try:
                content = self.get_note(note["id"])["content"]
            except StorageError:
                content = None                                     # the file is damaged; export what the database knows
            notes.append({**self._note_out(note), "content": content})
        return {"format": "splanner-export/1", "exported_at": v.utc_now(), "events": self.list_events(), "tasks": self.list_tasks(),
                "notes": notes, "reminders": self.db.rows("SELECT * FROM reminders ORDER BY due_at"),
                "stickies": [{k: s[k] for k in ("id", "note_id", "x", "y", "width", "height", "color")} for s in self.list_stickies()]}

    def export_ics(self) -> str:
        return events_to_ics(self.list_events())

    def backup(self, label: str = "backup") -> Path:
        return self.db.backup(label, self.backups_dir)


# -------------------------------------------------------------------------------------------------------------------- iCalendar

def _ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def _fold_line(line: str) -> list[str]:
    """RFC 5545 folding: lines are at most 75 octets, continuation lines start with one space; never split a UTF-8 character."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    out, current, size = [], "", 0
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > (75 if not out else 74):
            out.append(current)
            current, size = ch, width
        else:
            current += ch
            size += width
    out.append(current)
    return [out[0]] + [" " + part for part in out[1:]]


def _ics_utc(instant: str) -> str:
    return v.parse_instant(instant).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ics_local(instant: str, zone_name: str) -> str:
    return v.parse_instant(instant).astimezone(ZoneInfo(zone_name)).strftime("%Y%m%dT%H%M%S")


def _ics_event(event: dict[str, Any], stamp: str, *, override: dict[str, str] | None = None, key: str | None = None) -> list[str]:
    """One VEVENT: the event itself (with its RRULE and EXDATEs when it repeats), or - with ``override`` and the original ``key`` - one
    moved occurrence of it (same UID plus RECURRENCE-ID, as RFC 5545 asks)."""
    start_at, end_at = (override["start_at"], override["end_at"]) if override else (event["start_at"], event["end_at"])
    zone_name, repeating = event["time_zone"], event.get("recurrence") is not None
    lines = ["BEGIN:VEVENT", f"UID:{event['id']}@splanner.slai", f"DTSTAMP:{stamp}"]
    if event["all_day"]:
        if key:
            lines.append(f"RECURRENCE-ID;VALUE=DATE:{_local_date(key, zone_name).replace('-', '')}")
        lines += [f"DTSTART;VALUE=DATE:{_local_date(start_at, zone_name).replace('-', '')}", f"DTEND;VALUE=DATE:{_local_date(end_at, zone_name).replace('-', '')}"]
    elif repeating and not key:                    # a repeat keeps its local time across clock changes, so it is written in the event's own zone
        lines += [f"DTSTART;TZID={zone_name}:{_ics_local(start_at, zone_name)}", f"DTEND;TZID={zone_name}:{_ics_local(end_at, zone_name)}"]
    else:
        if key:
            lines.append(f"RECURRENCE-ID:{_ics_utc(key)}")
        lines += [f"DTSTART:{_ics_utc(start_at)}", f"DTEND:{_ics_utc(end_at)}"]
    if repeating and not key:
        lines.append(f"RRULE:{recurrence.rrule(event)}")
        for original, change in sorted(event["exceptions"].items()):
            if change is None:                     # a cancelled occurrence; a moved one stays in the rule and is replaced by its own VEVENT
                lines.append(f"EXDATE;VALUE=DATE:{_local_date(original, zone_name).replace('-', '')}" if event["all_day"] else f"EXDATE:{_ics_utc(original)}")
    lines.append(f"SUMMARY:{_ics_text(event['title'])}")
    if event["description"]:
        lines.append(f"DESCRIPTION:{_ics_text(event['description'])}")
    if event.get("location"):
        lines.append(f"LOCATION:{_ics_text(event['location'])}")
    if event.get("remind_before") is not None:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_text(event['title'])}", f"TRIGGER:-PT{int(event['remind_before'])}M", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def events_to_ics(events: list[dict[str, Any]]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SLAI//SPlanner//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    stamp = _ics_utc(v.utc_now())
    for event in events:
        lines += _ics_event(event, stamp)
        for key, change in sorted((event.get("exceptions") or {}).items()):
            if change is not None:
                lines += _ics_event(event, stamp, override=change, key=key)
    lines.append("END:VCALENDAR")
    folded = [part for line in lines for part in _fold_line(line)]
    return "\r\n".join(folded) + "\r\n"
