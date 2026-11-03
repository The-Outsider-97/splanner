"""Workspace-aware SPlanner store with a reversible migration of existing records.

The document and alarm engines stay in the base PlannerStore.  Workspace identity
is represented by a mapping rather than rewriting existing SLDC note file IDs.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..tools import validation as v
from ..tools.database import Database, Migration
from ..tools.errors import BadRequest, Conflict, NotFound
from . import store as legacy

PERSONAL_ID = "00000000000000000000000000000001"
LOCAL_USER = "local"
RESOURCE_KINDS = {"events": "event", "tasks": "task", "notes": "note", "reminders": "reminder", "stickies": "sticky"}
MAPPED = set(RESOURCE_KINDS)


def workspace_migration() -> Migration:
    return Migration(4, "Local-first workspaces, resource isolation and document provenance", (
        """CREATE TABLE workspaces (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT 'blue', icon TEXT NOT NULL DEFAULT 'layout',
            owner_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('personal','shared')),
            archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE workspace_members (
            workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
            PRIMARY KEY(workspace_id,user_id))""",
        """CREATE TABLE workspace_resources (
            kind TEXT NOT NULL CHECK(kind IN ('event','task','note','reminder','sticky')),
            resource_id TEXT NOT NULL, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            PRIMARY KEY(kind,resource_id))""",
        "CREATE INDEX workspace_resources_by_workspace ON workspace_resources(workspace_id,kind)",
        """CREATE TABLE workspace_selection (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            workspace_id TEXT NOT NULL REFERENCES workspaces(id))""",
        """CREATE TABLE document_imports (
            id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, filename TEXT NOT NULL,
            document_title TEXT NOT NULL, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            proposals_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('preview','committed','cancelled')),
            created_at TEXT NOT NULL, committed_at TEXT)""",
        "CREATE INDEX document_imports_by_hash ON document_imports(sha256,workspace_id)",
        """CREATE TABLE document_import_items (
            workspace_id TEXT NOT NULL, sha256 TEXT NOT NULL, proposal_key TEXT NOT NULL,
            resource_kind TEXT NOT NULL, resource_id TEXT NOT NULL,
            PRIMARY KEY(workspace_id,sha256,proposal_key))""",
        """CREATE TABLE sync_outbox (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL, operation TEXT NOT NULL,
            payload_json TEXT, revision INTEGER NOT NULL DEFAULT 0,
            queued_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE remote_resources (
            workspace_id TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
            owner_id TEXT NOT NULL, revision INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT, PRIMARY KEY(workspace_id,resource_type,resource_id))""",
        """CREATE TABLE sync_checkpoints (
            workspace_id TEXT PRIMARY KEY, remote_revision INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE collaboration_bindings (
            workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
            published INTEGER NOT NULL DEFAULT 0,
            remote_owner_id TEXT NOT NULL)""",
        """CREATE TABLE sync_conflicts (
            resource_type TEXT NOT NULL, resource_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
            error TEXT NOT NULL, observed_at TEXT NOT NULL,
            PRIMARY KEY(workspace_id,resource_type,resource_id))""",
        """CREATE TABLE remote_access (
            workspace_id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
            last_seen_at TEXT NOT NULL)""",
        lambda conn: _backfill(conn),
    ))


def _backfill(conn: Any) -> None:
    now = v.utc_now()
    conn.execute("INSERT INTO workspaces (id,name,owner_id,kind,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                 (PERSONAL_ID, "My workspace", LOCAL_USER, "personal", now, now))
    conn.execute("INSERT INTO workspace_members VALUES (?,?,?)", (PERSONAL_ID, LOCAL_USER, "owner"))
    conn.execute("INSERT INTO workspace_selection VALUES (1,?)", (PERSONAL_ID,))
    for table, kind in RESOURCE_KINDS.items():
        conn.execute(f"INSERT INTO workspace_resources (kind,resource_id,workspace_id) SELECT ?,id,? FROM {table}",
                     (kind, PERSONAL_ID))


class WorkspaceStore(legacy.PlannerStore):
    """Retains base CRUD, recurrence and native SLDC while enforcing workspace scope.

    Local workspace selection is shared between the loopback-only tabs of one
    SPlanner process. It is not a remote-user authentication mechanism.
    """

    def __init__(self, data_dir: Path | str, *, backups_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.note_dir = self.data_dir / "notes"
        self.backups_dir = Path(backups_dir) if backups_dir else self.data_dir / "backups"
        self.note_dir.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._creation_depth = 0
        self._suppress_outbox = False
        self.db = Database(self.data_dir / "planner.sqlite3", legacy.migrations(self.note_dir) + [workspace_migration()],
                           backups_dir=self.backups_dir)
        self._active_id = str(self.db.scalar("SELECT workspace_id FROM workspace_selection WHERE singleton=1") or PERSONAL_ID)
        # Keep the user's pre-migration workspace name when the old preferences exist.
        if 4 in self.db.migrated:
            try:
                prefs = json.loads((self.data_dir / "prefs.json").read_text(encoding="utf-8"))
                name = prefs.get("workspace_name")
                if isinstance(name, str) and 0 < len(name.strip()) <= 80:
                    self.db.execute("UPDATE workspaces SET name=? WHERE id=?", (name.strip(), PERSONAL_ID))
            except (OSError, ValueError, TypeError):
                pass

    @property
    def current_workspace_id(self) -> str:
        return self._active_id

    def _scope(self, table: str, item_id: str) -> None:
        if self._creation_depth or table not in MAPPED:
            return
        mapped = self.db.scalar("SELECT workspace_id FROM workspace_resources WHERE kind=? AND resource_id=?",
                                (RESOURCE_KINDS[table], item_id))
        if mapped != self._active_id:
            raise NotFound("This item is not available in the active workspace.")

    def _fetch(self, table: str, item_id: str, noun: str) -> dict[str, Any]:
        row = super()._fetch(table, item_id, noun)
        self._scope(table, row["id"])
        return row

    def _sticky(self, item_id: str) -> dict[str, Any]:
        row = super()._sticky(item_id)
        self._scope("stickies", row["id"])
        return row

    def _visible(self, kind: str) -> set[str]:
        return {row["resource_id"] for row in self.db.rows(
            "SELECT resource_id FROM workspace_resources WHERE workspace_id=? AND kind=?",
            (self._active_id, kind))}

    def _owned(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        visible = self._visible(RESOURCE_KINDS[table])
        return [row for row in rows if row["id"] in visible]

    def _link(self, table: str, item_id: str, workspace_id: str | None = None) -> None:
        self.db.execute("INSERT OR REPLACE INTO workspace_resources VALUES (?,?,?)",
                        (RESOURCE_KINDS[table], item_id, workspace_id or self._active_id))

    @contextmanager
    def _creating(self) -> Iterator[None]:
        self._creation_depth += 1
        try:
            yield
        finally:
            self._creation_depth -= 1

    def _enqueue(self, table: str, item_id: str, *, deleted: bool = False) -> None:
        """Persist an outbound operation only for an explicitly published workspace."""
        if self._suppress_outbox:
            return
        if not self.db.scalar("SELECT published FROM collaboration_bindings WHERE workspace_id=?", (self._active_id,)):
            return
        if deleted:
            payload = None
        elif table == "events":
            payload = self.get_event(item_id)
        elif table == "tasks":
            payload = self._fetch("tasks", item_id, "task")
        elif table == "notes":
            payload = self.get_note(item_id)
            import base64
            blob = self._package_path(item_id).read_bytes()
            payload["sldc_base64"] = base64.b64encode(blob).decode("ascii")
        elif table == "stickies":
            payload = self.get_sticky(item_id)
        elif table == "reminders":
            payload = self._fetch("reminders", item_id, "reminder")
        else:
            return
        current = self.db.scalar("SELECT revision FROM remote_resources WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                                 (self._active_id, RESOURCE_KINDS[table], item_id)) or 0
        self.db.execute("INSERT INTO sync_outbox VALUES (?,?,?,?,?,?,?,?,?)",
                        (v.new_id(), self._active_id, RESOURCE_KINDS[table], item_id,
                         "delete" if deleted else "upsert", json.dumps(payload, ensure_ascii=False) if payload is not None else None,
                         int(current), v.utc_now(), 0))

    def publish_workspace(self, workspace_id: str, owner_id: str) -> dict[str, Any]:
        """The caller must first create this workspace on the authenticated remote service."""
        ws = self.get_workspace(workspace_id)
        if workspace_id != self._active_id:
            raise Conflict("Select the workspace before publishing it.")
        if ws["archived"]:
            raise Conflict("Cannot share an archived workspace.")
        with self.db.transaction():
            self.db.execute("INSERT OR REPLACE INTO collaboration_bindings VALUES (?,?,?)", (workspace_id, 1, owner_id))
            # Initial snapshot (preserve the same IDs, don't clone any records).
            for table, kind in RESOURCE_KINDS.items():
                for row in self.db.rows("SELECT resource_id FROM workspace_resources WHERE kind=? AND workspace_id=?", (kind, workspace_id)):
                    self._enqueue(table, row["resource_id"])
        return ws

    def _check_write(self) -> None:
        ws = self.get_workspace(self._active_id)
        if ws["archived"]:
            raise Conflict("Restore this archived workspace before modifying its contents.")

    def _sync_reminder_links(self, event_id: str) -> None:
        # Base event logic may create or remove its associated reminder.
        for row in self.db.rows("SELECT id FROM reminders WHERE entity_type='event' AND entity_id=?", (event_id,)):
            self._link("reminders", row["id"])

    def _unlink(self, table: str, item_id: str) -> None:
        self.db.execute("DELETE FROM workspace_resources WHERE kind=? AND resource_id=?",
                        (RESOURCE_KINDS[table], item_id))

    def list_events(self, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        return self._owned("events", super().list_events(start, end))

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._owned("tasks", super().list_tasks())

    def list_notes(self) -> list[dict[str, Any]]:
        return self._owned("notes", super().list_notes())

    def list_reminders(self) -> list[dict[str, Any]]:
        return self._owned("reminders", super().list_reminders())

    def list_stickies(self) -> list[dict[str, Any]]:
        return self._owned("stickies", super().list_stickies())

    def due_reminders(self) -> list[dict[str, Any]]:
        return self._owned("reminders", super().due_reminders())

    def occurrences(self, start: str | None = None, end: str | None = None) -> dict[str, Any]:
        result = super().occurrences(start, end)
        allowed = self._visible("event")
        result["occurrences"] = [row for row in result["occurrences"] if row["id"] in allowed]
        begin, finish = result["window"]["from"], result["window"]["to"]
        for remote in self._remote_payloads("events"):
            if remote["resource_id"] in allowed:
                continue
            payload = self._remote_out(remote)
            if payload.get("recurrence"):
                result["occurrences"].extend(legacy.recurrence.occurrences(payload, begin, finish, limit=500))
        result["occurrences"].sort(key=lambda row: (row["start_at"], row["end_at"], row["id"]))
        return result

    def counts(self) -> dict[str, int]:
        def count(kind: str, predicate: str = "1=1") -> int:
            table = kind if kind != "stickies" else "stickies"
            return int(self.db.scalar(
                f"SELECT count(*) FROM {table} x JOIN workspace_resources w ON w.kind=? AND w.resource_id=x.id "
                f"WHERE w.workspace_id=? AND ({predicate})",
                (RESOURCE_KINDS[kind], self._active_id)) or 0)
        totals = {"events": count("events"), "tasks": count("tasks"), "openTasks": count("tasks", "x.status='open'"),
                  "notes": count("notes", "x.kind='note'"), "stickies": count("stickies"),
                  "reminders": count("reminders", "x.status='pending'")}
        # The unified planner counts visible remote resources only once.
        for table, key in (("events", "events"), ("tasks", "tasks"), ("notes", "notes"),
                           ("stickies", "stickies"), ("reminders", "reminders")):
            local_ids = self._visible(RESOURCE_KINDS[table])
            for remote in self._remote_payloads(table):
                if remote["resource_id"] in local_ids:
                    continue
                payload = self._remote_out(remote)
                if table == "notes" and payload.get("kind") != "note":
                    continue
                if table == "reminders" and payload.get("status") != "pending":
                    continue
                totals[key] += 1
                if table == "tasks" and payload.get("status") == "open":
                    totals["openTasks"] += 1
        return totals

    def search(self, query: str, limit: int = 40) -> list[dict[str, Any]]:
        hits = super().search(query, limit=1000)
        available = {kind: self._visible(kind) for kind in RESOURCE_KINDS.values()}
        result = [h for h in hits if h["id"] in available.get(h["kind"], set())]
        needle = " ".join(query.casefold().split())
        if needle:
            already = {(h["kind"], h["id"]) for h in result}
            for table in ("events", "tasks", "notes", "stickies"):
                for remote in self._remote_payloads(table):
                    key = (RESOURCE_KINDS[table], remote["resource_id"])
                    if key in already:
                        continue
                    item = self._remote_out(remote)
                    title = item.get("title") or item.get("text") or "Shared item"
                    details = item.get("description") or item.get("excerpt") or item.get("text") or ""
                    if needle in f"{title} {details}".casefold():
                        result.append({"kind": key[0], "id": key[1], "title": str(title)[:180],
                                       "detail": str(details)[:80], "at": item.get("start_at") or item.get("due_at")})
                        already.add(key)
        return result[:limit]

    def create_event(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._guard, self.db.transaction():
            self._check_write()
            with self._creating():
                row = super().create_event(data)
            self._link("events", row["id"])
            self._sync_reminder_links(row["id"])
            self._enqueue("events", row["id"])
            return row

    def update_event(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.db.scalar("SELECT 1 FROM workspace_resources WHERE kind='event' AND resource_id=?", (item_id,)):
            return self._remote_edit("events", item_id, data)
        self._guard_remote_divergence("events", item_id)
        with self._guard, self.db.transaction():
            self._check_write()
            row = super().update_event(item_id, data)
            self._sync_reminder_links(item_id)
            self._enqueue("events", item_id)
            return row

    def delete_event(self, item_id: str) -> None:
        if not self.db.scalar("SELECT 1 FROM workspace_resources WHERE kind='event' AND resource_id=?", (item_id,)):
            self._remote_delete("events", item_id)
            return
        self._guard_remote_divergence("events", item_id)
        with self._guard, self.db.transaction():
            self._check_write()
            reminder_ids = [row["id"] for row in self.db.rows(
                "SELECT id FROM reminders WHERE entity_type='event' AND entity_id=?", (item_id,))]
            super().delete_event(item_id)
            self._enqueue("events", item_id, deleted=True)
            self._unlink("events", item_id)
            for rid in reminder_ids:
                self._unlink("reminders", rid)

    def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._guard, self.db.transaction():
            self._check_write()
            with self._creating():
                row = super().create_task(data)
            self._link("tasks", row["id"])
            self._enqueue("tasks", row["id"])
            return row

    def update_task(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.db.scalar("SELECT 1 FROM workspace_resources WHERE kind='task' AND resource_id=?", (item_id,)):
            return self._remote_edit("tasks", item_id, data)
        self._guard_remote_divergence("tasks", item_id)
        with self.db.transaction():
            self._check_write()
            result = super().update_task(item_id, data)
            self._enqueue("tasks", item_id)
            return result

    def delete_task(self, item_id: str) -> None:
        if not self.db.scalar("SELECT 1 FROM workspace_resources WHERE kind='task' AND resource_id=?", (item_id,)):
            self._remote_delete("tasks", item_id)
            return
        self._guard_remote_divergence("tasks", item_id)
        with self.db.transaction():
            self._check_write()
            super().delete_task(item_id)
            self._enqueue("tasks", item_id, deleted=True)
            self._unlink("tasks", item_id)

    def clear_completed_tasks(self) -> int:
        ids = [t["id"] for t in self.list_tasks() if t["status"] == "done"]
        for item_id in ids:
            self.delete_task(item_id)
        return len(ids)

    def create_note(self, data: dict[str, Any], *, kind: str = "note") -> dict[str, Any]:
        with self._guard:
            self._check_write()
            with self._creating():
                row = super().create_note(data, kind=kind)
            self._link("notes", row["id"])
            self._enqueue("notes", row["id"])
            return row

    def update_note(self, item_id: str, data: dict[str, Any], *, require_version: bool = True) -> dict[str, Any]:
        with self.db.transaction():
            self._check_write()
            result = super().update_note(item_id, data, require_version=require_version)
            self._enqueue("notes", item_id)
            return result

    def delete_note(self, item_id: str) -> None:
        self._check_write()
        super().delete_note(item_id)
        self._enqueue("notes", item_id, deleted=True)
        self._unlink("notes", item_id)

    def create_sticky(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._guard:
            self._check_write()
            with self._creating():
                row = super().create_sticky(data)
            self._link("stickies", row["id"])
            self._enqueue("stickies", row["id"])
            return row

    def update_sticky(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self.db.transaction():
            self._check_write()
            result = super().update_sticky(item_id, data)
            self._enqueue("stickies", item_id)
            return result

    def delete_sticky(self, item_id: str) -> None:
        with self.db.transaction():
            self._check_write()
            row = self._sticky(item_id)
            super().delete_sticky(item_id)
            self._enqueue("stickies", item_id, deleted=True)
            self._unlink("stickies", item_id)
            self._unlink("notes", row["note_id"])

    def create_reminder(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.db.transaction():
            self._check_write()
            with self._creating():
                row = super().create_reminder(data)
            self._link("reminders", row["id"])
            self._enqueue("reminders", row["id"])
            return row

    def change_reminder(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self.db.transaction():
            self._check_write()
            result = super().change_reminder(item_id, data)
            self._enqueue("reminders", item_id)
            return result

    def delete_reminder(self, item_id: str) -> None:
        with self.db.transaction():
            self._check_write()
            super().delete_reminder(item_id)
            self._enqueue("reminders", item_id, deleted=True)
            self._unlink("reminders", item_id)

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        row = self.db.row("SELECT * FROM workspaces WHERE id=?", (v.record_id(workspace_id),))
        if row is None:
            raise NotFound("Workspace not found.")
        return {**row, "archived": bool(row["archived"])}

    def list_workspaces(self, *, archived: bool = False) -> list[dict[str, Any]]:
        rows = self.db.rows("SELECT * FROM workspaces WHERE archived=? ORDER BY created_at,id", (int(archived),))
        return [{**row, "archived": bool(row["archived"])} for row in rows]

    def create_workspace(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise BadRequest("A workspace must be an object.")
        if set(data) - {"name", "description", "color", "icon"}:
            raise BadRequest("Unknown workspace property.")
        name = v.text(data.get("name"), "name", 80)
        description = v.text(data.get("description", ""), "description", 1000, empty=True, multiline=True)
        color = v.choice(data.get("color", "blue"), legacy.COLORS, "color")
        icon = v.choice(data.get("icon", "layout"), ("layout", "calendar", "folder", "home", "briefcase", "book"), "icon")
        now, item_id = v.utc_now(), v.new_id()
        with self.db.transaction():
            self.db.execute("INSERT INTO workspaces (id,name,description,color,icon,owner_id,kind,created_at,updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?)", (item_id, name, description, color, icon, LOCAL_USER, "personal", now, now))
            self.db.execute("INSERT INTO workspace_members VALUES (?,?,?)", (item_id, LOCAL_USER, "owner"))
        return self.get_workspace(item_id)

    def update_workspace(self, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
        old = self.get_workspace(workspace_id)
        allowed = {"name", "description", "color", "icon", "version"}
        if set(data) - allowed:
            raise BadRequest("Unknown workspace property.")
        legacy.PlannerStore._check_version(old, data.get("version"), "workspace")
        changes: dict[str, Any] = {}
        if "name" in data:
            changes["name"] = v.text(data["name"], "name", 80)
        if "description" in data:
            changes["description"] = v.text(data["description"], "description", 1000, empty=True, multiline=True)
        if "color" in data:
            changes["color"] = v.choice(data["color"], legacy.COLORS, "color")
        if "icon" in data:
            changes["icon"] = v.choice(data["icon"], ("layout", "calendar", "folder", "home", "briefcase", "book"), "icon")
        if changes:
            with self.db.transaction():
                self.db.execute("UPDATE workspaces SET " + ", ".join(f"{k}=?" for k in changes) +
                                ", version=version+1, updated_at=? WHERE id=? AND version=?",
                                (*changes.values(), v.utc_now(), workspace_id, old["version"]))
                if self.db.scalar("SELECT changes()") != 1:
                    raise Conflict("The workspace changed. Refresh and try again.")
        return self.get_workspace(workspace_id)

    def select_workspace(self, workspace_id: str) -> dict[str, Any]:
        with self._guard:
            ws = self.get_workspace(workspace_id)
            if ws["archived"]:
                raise Conflict("Restore this workspace before selecting it.")
            self.db.execute("UPDATE workspace_selection SET workspace_id=? WHERE singleton=1", (ws["id"],))
            self._active_id = ws["id"]
        return ws

    def archive_workspace(self, workspace_id: str, archived: bool) -> dict[str, Any]:
        with self._guard, self.db.transaction():
            ws = self.get_workspace(workspace_id)
            if ws["id"] == PERSONAL_ID:
                raise Conflict("The original personal workspace cannot be archived.")
            if archived and ws["id"] == self._active_id:
                self.select_workspace(PERSONAL_ID)
            self.db.execute("UPDATE workspaces SET archived=?, version=version+1, updated_at=? WHERE id=?",
                            (int(archived), v.utc_now(), ws["id"]))
        return self.get_workspace(workspace_id)

    def delete_workspace(self, workspace_id: str) -> None:
        ws = self.get_workspace(workspace_id)
        if ws["id"] == PERSONAL_ID:
            raise Conflict("The original personal workspace cannot be deleted.")
        if ws["id"] == self._active_id:
            raise Conflict("Switch to another workspace before deleting this workspace.")
        if self.db.scalar("SELECT 1 FROM workspace_resources WHERE workspace_id=? LIMIT 1", (ws["id"],)):
            raise Conflict("Workspace contains planning items. Move or delete them before deleting the workspace.")
        if self.db.scalar("SELECT 1 FROM document_imports WHERE workspace_id=? LIMIT 1", (ws["id"],)):
            raise Conflict("Workspace has document import history. Archive it instead of deleting its provenance.")
        if self.db.scalar("SELECT 1 FROM collaboration_bindings WHERE workspace_id=? LIMIT 1", (ws["id"],)):
            raise Conflict("Published workspaces cannot be deleted until their sharing relationship is closed.")
        with self.db.transaction():
            self.db.execute("DELETE FROM workspaces WHERE id=?", (ws["id"],))

    def move_resource(self, kind: str, resource_id: str, target_workspace_id: str) -> None:
        if kind not in RESOURCE_KINDS.values():
            raise BadRequest("Unknown resource kind.")
        self._check_write()
        self._scope(next(table for table, value in RESOURCE_KINDS.items() if value == kind), resource_id)
        target = self.get_workspace(target_workspace_id)
        if target["archived"]:
            raise Conflict("Cannot move items into an archived workspace.")
        with self.db.transaction():
            self.db.execute("UPDATE workspace_resources SET workspace_id=? WHERE kind=? AND resource_id=?",
                            (target_workspace_id, kind, resource_id))
            if kind == "event":
                self.db.execute("UPDATE workspace_resources SET workspace_id=? WHERE kind='reminder' AND resource_id "
                                "IN (SELECT id FROM reminders WHERE entity_type='event' AND entity_id=?)",
                                (target_workspace_id, resource_id))
            if kind == "sticky":
                linked = self.db.scalar("SELECT note_id FROM stickies WHERE id=?", (resource_id,))
                if linked:
                    self.db.execute("UPDATE workspace_resources SET workspace_id=? WHERE kind='note' AND resource_id=?",
                                    (target_workspace_id, linked))

    # ------------------------------------------------------------------------------------------------ remote caches
    def _remote_record(self, table: str, item_id: str) -> dict[str, Any] | None:
        kind = RESOURCE_KINDS[table]
        row = self.db.row("SELECT r.* FROM remote_resources r JOIN remote_access a ON a.workspace_id=r.workspace_id "
                          "WHERE r.resource_type=? AND r.resource_id=? AND r.deleted=0 ORDER BY r.revision DESC LIMIT 1",
                          (kind, item_id))
        return row

    def _remote_payloads(self, table: str) -> list[dict[str, Any]]:
        kind = RESOURCE_KINDS[table]
        return self.db.rows("SELECT r.* FROM remote_resources r JOIN remote_access a ON a.workspace_id=r.workspace_id "
                            "WHERE r.resource_type=? AND r.deleted=0 ORDER BY r.resource_id", (kind,))

    @staticmethod
    def _remote_out(row: dict[str, Any]) -> dict[str, Any]:
        data = json.loads(row["payload_json"]) or {}
        if not isinstance(data, dict):
            return {}
        return {**data, "id": row["resource_id"], "owner_id": row["owner_id"],
                "workspace_id": row["workspace_id"], "shared": True,
                "remote_revision": row["revision"]}

    def _remote_edit(self, table: str, item_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        row = self._remote_record(table, item_id)
        if not row:
            raise NotFound("This shared item is not available.")
        role = self.db.scalar("SELECT role FROM remote_access WHERE workspace_id=?", (row["workspace_id"],))
        if role not in ("owner", "editor"):
            raise Conflict("You have view-only access to this shared item.")
        allowed = {
            "events": {"title", "start_at", "end_at", "time_zone", "all_day", "description", "location", "color", "remind_before", "recurrence", "exceptions"},
            "tasks": {"title", "description", "status", "priority", "due_at"},
        }
        if table not in allowed:
            raise Conflict("Shared notes and boards are read-only in this build.")
        if set(changes) - (allowed[table] | {"version"}):
            raise BadRequest("Unexpected shared-item field.")
        data = self._remote_out(row)
        expected = changes.get("version")
        if expected is not None and expected != data.get("version"):
            raise Conflict("The shared item changed; refresh before editing.")
        edited = {**{k: v for k, v in data.items() if k not in ("owner_id", "workspace_id", "shared", "remote_revision")},
                  **{k: v for k, v in changes.items() if k != "version"}}
        if table == "events" and (not edited.get("start_at") or not edited.get("end_at") or edited["end_at"] <= edited["start_at"]):
            raise BadRequest("An event must end after it starts.")
        edited["version"] = int(edited.get("version", 0)) + 1
        edited["updated_at"] = v.utc_now()
        with self.db.transaction():
            self.db.execute("UPDATE remote_resources SET payload_json=? WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                            (json.dumps(edited), row["workspace_id"], RESOURCE_KINDS[table], item_id))
            self.db.execute("INSERT INTO sync_outbox VALUES (?,?,?,?,?,?,?,?,?)",
                            (v.new_id(), row["workspace_id"], RESOURCE_KINDS[table], item_id, "upsert",
                             json.dumps({k: edited[k] for k in changes if k in allowed[table]}),
                             row["revision"], v.utc_now(), 0))
        return self._remote_out({**row, "payload_json": json.dumps(edited)})

    def _remote_delete(self, table: str, item_id: str) -> None:
        row = self._remote_record(table, item_id)
        if not row:
            raise NotFound("Shared item not found.")
        role = self.db.scalar("SELECT role FROM remote_access WHERE workspace_id=?", (row["workspace_id"],))
        if role not in ("owner", "editor"):
            raise Conflict("You have view-only access to this shared item.")
        with self.db.transaction():
            self.db.execute("UPDATE remote_resources SET deleted=1 WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                            (row["workspace_id"], RESOURCE_KINDS[table], item_id))
            self.db.execute("INSERT INTO sync_outbox VALUES (?,?,?,?,?,?,?,?,?)",
                            (v.new_id(), row["workspace_id"], RESOURCE_KINDS[table], item_id, "delete", None,
                             row["revision"], v.utc_now(), 0))

    def _guard_remote_divergence(self, table: str, item_id: str) -> None:
        row = self.db.row("SELECT w.workspace_id,r.payload_json FROM workspace_resources w "
                          "JOIN remote_resources r ON r.workspace_id=w.workspace_id AND r.resource_type=w.kind AND r.resource_id=w.resource_id "
                          "WHERE w.kind=? AND w.resource_id=?", (RESOURCE_KINDS[table], item_id))
        if row is None:
            return
        if self.db.scalar("SELECT 1 FROM sync_conflicts WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                          (row["workspace_id"], RESOURCE_KINDS[table], item_id)):
            raise Conflict("This item has an unresolved synchronization conflict. Resolve it before editing.")

    def snapshot(self, start: str | None = None, end: str | None = None) -> dict[str, Any]:
        result = super().snapshot(start, end)
        # Preserve one canonical identity. A cached remotely owned resource is
        # rendered in the unified view; it is not cloned into the local table.
        for table, bucket in (("events", "events"), ("tasks", "tasks"), ("notes", "notes"),
                              ("stickies", "stickies"), ("reminders", "reminders")):
            current = {item["id"]: item for item in result[bucket]}
            for row in self._remote_payloads(table):
                item = self._remote_out(row)
                if not item or not item.get("title") and table not in ("stickies",):
                    continue
                if table == "notes":
                    # A list must not serialize a whole SLDC package to the browser.
                    item.pop("sldc_base64", None)
                    item.pop("content", None)
                item_id = item["id"]
                # Cached source records only supersede a local copy if there are
                # no unsent local writes; otherwise keep local and show conflict.
                pending = self.db.scalar("SELECT 1 FROM sync_outbox WHERE workspace_id=? AND resource_type=? AND resource_id=? LIMIT 1",
                                         (row["workspace_id"], RESOURCE_KINDS[table], item_id))
                if item_id not in current or not pending:
                    current[item_id] = item
            result[bucket] = list(current.values())
        result["events"].sort(key=lambda e: (e["start_at"], e["id"]))
        return result

    def get_event(self, item_id: str) -> dict[str, Any]:
        try:
            return super().get_event(item_id)
        except NotFound:
            row = self._remote_record("events", item_id)
            if row is None:
                raise
            return self._remote_out(row)

    def get_note(self, item_id: str) -> dict[str, Any]:
        try:
            return super().get_note(item_id)
        except NotFound:
            row = self._remote_record("notes", item_id)
            if row is None:
                raise
            payload = self._remote_out(row)
            payload.pop("sldc_base64", None)
            return {**payload, "editable": False}

    def note_file(self, item_id: str) -> tuple[bytes, str]:
        try:
            return super().note_file(item_id)
        except NotFound:
            import base64
            row = self._remote_record("notes", item_id)
            if row is None:
                raise
            payload = self._remote_out(row)
            if "sldc_base64" not in payload:
                raise NotFound("The shared document package is not available offline.")
            return base64.b64decode(payload["sldc_base64"], validate=True), f"{item_id}.sldc"

    def update_remote_or_local_event(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        mapped = self.db.scalar("SELECT 1 FROM workspace_resources WHERE kind='event' AND resource_id=?", (item_id,))
        if mapped:
            return self.update_event(item_id, data)
        return self._remote_edit("events", item_id, data)

    def update_remote_or_local_task(self, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        mapped = self.db.scalar("SELECT 1 FROM workspace_resources WHERE kind='task' AND resource_id=?", (item_id,))
        if mapped:
            return self.update_task(item_id, data)
        return self._remote_edit("tasks", item_id, data)

    def delete_remote_or_local(self, table: str, item_id: str) -> None:
        mapped = self.db.scalar("SELECT 1 FROM workspace_resources WHERE kind=? AND resource_id=?",
                                (RESOURCE_KINDS[table], item_id))
        if mapped:
            {"events": self.delete_event, "tasks": self.delete_task}[table](item_id)
        else:
            self._remote_delete(table, item_id)

    def unresolved_conflicts(self) -> list[dict[str, Any]]:
        return self.db.rows("SELECT * FROM sync_conflicts ORDER BY observed_at DESC")

    def resolve_sync_conflict(self, workspace_id: str, kind: str, resource_id: str, choice: str) -> dict[str, Any]:
        """Resolve only after the local user explicitly chooses one authoritative side."""
        if kind not in RESOURCE_KINDS.values() or choice not in ("remote", "local"):
            raise BadRequest("Choose local or remote for an existing conflict.")
        conflict = self.db.row("SELECT * FROM sync_conflicts WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                               (workspace_id, kind, resource_id))
        if conflict is None:
            raise NotFound("This conflict has already been resolved.")
        mapped = self.db.scalar("SELECT workspace_id FROM workspace_resources WHERE kind=? AND resource_id=?",
                                (kind, resource_id))
        cache = self.db.row("SELECT * FROM remote_resources WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                            (workspace_id, kind, resource_id))
        if cache is None:
            raise Conflict("No remote revision is available to compare.")
        if mapped and (mapped != workspace_id or self._active_id != workspace_id):
            raise Conflict("Select the affected local workspace before resolving this conflict.")
        with self.db.transaction():
            if choice == "remote":
                self.db.execute("DELETE FROM sync_outbox WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                                (workspace_id, kind, resource_id))
                if mapped:
                    source = self._remote_out(cache)
                    if kind == "event":
                        old = self._fetch("events", resource_id, "event")
                        fields = {k: source[k] for k in self.EVENT_FIELDS if k in source and k != "version"}
                        fields["version"] = old["version"]
                        self._suppress_outbox = True
                        try:
                            legacy.PlannerStore.update_event(self, resource_id, fields)
                        finally:
                            self._suppress_outbox = False
                        self._sync_reminder_links(resource_id)
                    elif kind == "task":
                        old = self._fetch("tasks", resource_id, "task")
                        fields = {k: source[k] for k in self.TASK_FIELDS if k in source and k != "version"}
                        fields["version"] = old["version"]
                        self._suppress_outbox = True
                        try:
                            legacy.PlannerStore.update_task(self, resource_id, fields)
                        finally:
                            self._suppress_outbox = False
                    else:
                        raise Conflict("Document and board conflicts must be resolved in their own editor.")
                else:
                    # Re-fetch the authoritative remote copy from a full incremental replay.
                    self.db.execute("DELETE FROM remote_resources WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                                    (workspace_id, kind, resource_id))
                    self.db.execute("DELETE FROM sync_checkpoints WHERE workspace_id=?", (workspace_id,))
            else:
                self.db.execute("DELETE FROM sync_outbox WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                                (workspace_id, kind, resource_id))
                if mapped:
                    table = next(table for table, name in RESOURCE_KINDS.items() if name == kind)
                    self._enqueue(table, resource_id)
                else:
                    # User explicitly elects to rebase their queued field edits.
                    edited = json.loads(cache["payload_json"])
                    self.db.execute("INSERT INTO sync_outbox VALUES (?,?,?,?,?,?,?,?,?)",
                                    (v.new_id(), workspace_id, kind, resource_id, "upsert",
                                     json.dumps(edited), cache["revision"], v.utc_now(), 0))
            self.db.execute("DELETE FROM sync_conflicts WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                            (workspace_id, kind, resource_id))
        return {"workspace_id": workspace_id, "resource_type": kind, "resource_id": resource_id, "choice": choice}
