"""Incremental offline-aware collaboration synchronizer for SPlanner.

One source identity per shared item. Outgoing writes survive restarts; incoming
changes have remote revision checkpoints and tombstones. When a concurrent
modification cannot be applied safely it is retained for manual resolution.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from ..tools import validation as v
from .collaboration_client import CollaborationClient, RemoteError
from .workspace_store import WorkspaceStore


class CollaborationSync:
    def __init__(self, store: WorkspaceStore, client: CollaborationClient) -> None:
        self.store, self.client = store, client
        self._lock = threading.RLock()
        self.status = "authentication_required"
        self.last_error: str | None = None

    def _inbox(self, wid: str, entry: dict[str, Any]) -> None:
        typ, rid = entry["resource_type"], entry["resource_id"]
        current = self.store.db.row("SELECT revision FROM remote_resources WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                                    (wid, typ, rid))
        if current and current["revision"] >= entry["revision"]:
            return
        self.store.db.execute("INSERT INTO remote_resources VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace_id,resource_type,resource_id) "
                              "DO UPDATE SET owner_id=excluded.owner_id,revision=excluded.revision,"
                              "deleted=excluded.deleted,payload_json=excluded.payload_json",
                              (wid, typ, rid, entry["owner_id"], entry["revision"],
                               int(entry["deleted"]), json.dumps(entry["payload"], ensure_ascii=False)))
        # Do not overwrite a local original if it was edited by somebody else.
        # Keep the newer remote revision for the unified view; local edits require
        # a review if the local/remote values diverge.
        mapped = self.store.db.scalar("SELECT workspace_id FROM workspace_resources WHERE kind=? AND resource_id=?",
                                      (typ, rid))
        pending = self.store.db.scalar("SELECT 1 FROM sync_outbox WHERE workspace_id=? AND resource_type=? AND resource_id=? LIMIT 1",
                                       (wid, typ, rid))
        if mapped == wid:
            table = {"event": "events", "task": "tasks", "note": "notes", "sticky": "stickies", "reminder": "reminders"}.get(typ)
            original = self.store.db.row(f"SELECT * FROM {table} WHERE id=?", (rid,)) if table else None
            values = entry["payload"]
            relevant = {
                "event": ("title", "start_at", "end_at", "description", "location", "color"),
                "task": ("title", "description", "status", "priority", "due_at"),
                "note": ("title", "pinned"), "sticky": ("x", "y", "color"),
                "reminder": ("title", "due_at", "status"),
            }.get(typ, ())
            diverged = bool(original and isinstance(values, dict) and any(
                key in values and values[key] != original.get(key) for key in relevant))
            if pending or diverged:
                self.store.db.execute("INSERT OR REPLACE INTO sync_conflicts VALUES (?,?,?,?,?)",
                                      (typ, rid, wid, "Review concurrent edits before applying the remote revision." if diverged else
                                       "Remote change arrived while local edits are pending.", v.utc_now()))

    def _send_pending(self, wid: str) -> int:
        sent = 0
        blocked: set[tuple[str, str]] = set()
        entries = self.store.db.rows("SELECT * FROM sync_outbox WHERE workspace_id=? ORDER BY queued_at,id LIMIT 250", (wid,))
        for entry in entries:
            resource = (entry["resource_type"], entry["resource_id"])
            if resource in blocked:
                continue
            fields = json.loads(entry["payload_json"]) if entry["payload_json"] is not None else {}
            if not isinstance(fields, dict):
                continue
            try:
                result = self.client.send_change(wid, {
                    "op_id": entry["id"], "resource_type": entry["resource_type"], "resource_id": entry["resource_id"],
                    "operation": entry["operation"], "base_revision": entry["revision"], "fields": fields,
                    "visibility": "workspace", "selected_users": [],
                })
            except RemoteError as exc:
                blocked.add(resource)
                self.store.db.execute("UPDATE sync_outbox SET attempts=attempts+1 WHERE id=?", (entry["id"],))
                self.store.db.execute("INSERT OR REPLACE INTO sync_conflicts VALUES (?,?,?,?,?)",
                                      (entry["resource_type"], entry["resource_id"], wid, exc.message, v.utc_now()))
                continue
            with self.store.db.transaction():
                self.store.db.execute("DELETE FROM sync_outbox WHERE id=?", (entry["id"],))
                self.store.db.execute("UPDATE sync_outbox SET revision=? WHERE workspace_id=? AND resource_type=? "
                                      "AND resource_id=? AND revision=?", (result["revision"], wid, entry["resource_type"],
                                                                          entry["resource_id"], entry["revision"]))
                self.store.db.execute("DELETE FROM sync_conflicts WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                                      (wid, entry["resource_type"], entry["resource_id"]))
            sent += 1
        return sent

    def sync_once(self) -> dict[str, Any]:
        with self._lock:
            if not self.client.user:
                self.status = "authentication_required"
                return self.summary()
            self.status, self.last_error = "synchronizing", None
            try:
                memberships = self.client.memberships()
                allowed = {w["id"] for w in memberships}
                # A revoked workspace is no longer visible through the local cache.
                # Local-owned planning records remain intact and unaffected.
                with self.store.db.transaction():
                    if allowed:
                        placeholders = ",".join("?" for _ in allowed)
                        self.store.db.execute(f"DELETE FROM remote_resources WHERE workspace_id NOT IN ({placeholders})", tuple(allowed))
                        self.store.db.execute(f"DELETE FROM sync_checkpoints WHERE workspace_id NOT IN ({placeholders})", tuple(allowed))
                        self.store.db.execute(f"DELETE FROM remote_access WHERE workspace_id NOT IN ({placeholders})", tuple(allowed))
                    else:
                        self.store.db.execute("DELETE FROM remote_resources")
                        self.store.db.execute("DELETE FROM sync_checkpoints")
                        self.store.db.execute("DELETE FROM remote_access")
                    for remote in memberships:
                        self.store.db.execute("INSERT INTO remote_access VALUES (?,?,?) ON CONFLICT(workspace_id) "
                                              "DO UPDATE SET role=excluded.role,last_seen_at=excluded.last_seen_at",
                                              (remote["id"], remote["role"], v.utc_now()))
                sent, received = 0, 0
                for remote in memberships:
                    wid = remote["id"]
                    # Pull before pushing: detect remote revision changes before applying stale local edits.
                    for _ in range(25):
                        before = self.store.db.scalar("SELECT remote_revision FROM sync_checkpoints WHERE workspace_id=?", (wid,)) or 0
                        batch = self.client.changes(wid, int(before))
                        with self.store.db.transaction():
                            for change in batch["changes"]:
                                self._inbox(wid, change)
                            self.store.db.execute("INSERT INTO sync_checkpoints VALUES (?,?) ON CONFLICT(workspace_id) "
                                                  "DO UPDATE SET remote_revision=excluded.remote_revision",
                                                  (wid, batch["cursor"]))
                        received += len(batch["changes"])
                        if not batch["has_more"] or batch["cursor"] <= before:
                            break
                    if remote["role"] in ("owner", "editor"):
                        sent += self._send_pending(wid)
                self.status = "conflict_detected" if self.store.db.scalar("SELECT 1 FROM sync_conflicts LIMIT 1") else (
                    "changes_pending" if self.store.db.scalar("SELECT 1 FROM sync_outbox LIMIT 1") else "up_to_date")
                return {**self.summary(), "sent": sent, "received": received}
            except BaseException as exc:
                self.status = "offline"
                self.last_error = str(exc)[:200]
                return self.summary()

    def summary(self) -> dict[str, Any]:
        pending = int(self.store.db.scalar("SELECT count(*) FROM sync_outbox") or 0)
        conflicts = int(self.store.db.scalar("SELECT count(*) FROM sync_conflicts") or 0)
        return {"status": self.status, "pending": pending, "conflicts": conflicts,
                "last_error": self.last_error, "configured": self.client.available,
                "signed_in": self.client.user is not None}
