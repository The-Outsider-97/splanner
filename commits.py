"""SPlanner's connection to SLAI's commit contract (``src/agents/planning/project_adapter.py``).

SPlanner already had the two things the contract needs from a host: a revision for every record (``version``, checked in the same SQL
statement that changes the row) and transactions. What it did not have is the third thing, **a proposal that a person has agreed to**,
because until now nothing but the person edited SPlanner. This module adds exactly that, and nothing that acts without a person:

* A **proposal** is a stored, validated change to one event or task (``POST /api/proposals``). Creating one changes nothing: it is checked by
  running the real update inside a transaction that is rolled back, so the person is only ever asked about a change SPlanner would accept.
* **Consent** is the person approving it (``POST /api/proposals/{id}/approve``). Only then does the change go through ``CommitReconciliationStore``
  to the host below. A workflow cannot approve for the person: the authorisation for the commit exists only inside that request.
* The **host** exposes the revisions (``"event:<id>"`` and ``"task:<id>"`` map to the record's version), applies the stored change with the
  version the proposal was made against, and writes the decision into ``commit_ledger`` *in the same transaction*, so a decision and its
  change are both there or both absent.

Notes, stickies and reminders are not covered: notes are SLDC files with their own version rules, and reminders and stickies are not
something an agent proposes changes to. The interface has no screen for proposals yet (``capabilities.agentSuggestions`` stays false), so
approval is through the API.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from ..tools import validation as v
from ..tools.commit_host import Applied, CommitHost, CommitRefused, Outbox, planning_types
from ..tools.errors import BadRequest, Conflict, LimitExceeded, NotFound
from .store import PlannerStore, TITLE

PROJECT_ID = "workspace"                           # SPlanner is one workspace per data folder
ACTOR = "splanner-local-user"                      # the person at this loopback session
OUTBOX = "commits-outbox.sqlite3"
KINDS = {"event": ("events", "update_event", "get_event"), "task": ("tasks", "update_task", "get_task")}
MAX_PENDING = 500
STATUSES = ("pending", "rejected", "executed", "failed")


class _Rollback(Exception):
    def __init__(self, result: Any) -> None:
        self.result = result


class _DbLedger:
    """``CommitLedger`` over the ``commit_ledger`` table of the planner database."""

    def __init__(self, store: PlannerStore) -> None:
        self.db = store.db

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.db.row("SELECT fingerprint, receipt FROM commit_ledger WHERE idempotency_key=?", (key,))
        return {"fingerprint": row["fingerprint"], "receipt": json.loads(row["receipt"])} if row else None

    def put(self, key: str, entry: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO commit_ledger (idempotency_key, fingerprint, receipt, created_at) VALUES (?,?,?,?)",
                        (key, entry["fingerprint"], json.dumps(entry["receipt"], sort_keys=True), v.utc_now()))


def split_artifact(artifact_id: Any) -> tuple[str, str]:
    kind, _, item = str(artifact_id).partition(":")
    if kind not in KINDS or not item:
        raise BadRequest("An artifact is 'event:<id>' or 'task:<id>'.")
    return kind, v.record_id(item)


class PlannerCommits:
    def __init__(self, store: PlannerStore) -> None:
        self.store = store
        self.db = store.db
        self.host = CommitHost(revisions=self.revisions, apply=self._apply, ledger=_DbLedger(store), lock=self.db.lock, atomic=self.db.transaction)
        self.outbox = Outbox(store.data_dir / OUTBOX, self.host)

    def close(self) -> None:
        self.outbox.close()

    # ------------------------------------------------------------------------------------------------- the adapter's callbacks
    def revisions(self, project_id: str) -> Mapping[str, str]:
        if project_id != PROJECT_ID:
            return {}
        out = {f"event:{r['id']}": str(r["version"]) for r in self.db.rows("SELECT id, version FROM events")}
        out.update({f"task:{r['id']}": str(r["version"]) for r in self.db.rows("SELECT id, version FROM tasks")})
        return out

    def _apply(self, request: Any) -> Applied:
        row = self._row(request.proposal_ref)
        kind, item = split_artifact(request.artifact_id)
        if row["status"] != "pending":
            raise CommitRefused(f"This proposal is already {row['status']}.")
        if not row["consent"]:
            raise CommitRefused("This proposal has not been approved by a person.")
        if (row["kind"], row["item_id"]) != (kind, item):
            raise CommitRefused("This proposal is for a different item than the commit names.")
        if str(row["base_version"]) != request.expected_revision:
            raise CommitRefused(f"The proposal was made for version {row['base_version']}, not {request.expected_revision}.")
        updated = getattr(self.store, KINDS[kind][1])(item, {**json.loads(row["changes"]), "version": row["base_version"]})   # the store's own checked update
        self.db.execute("UPDATE proposals SET status='executed', outcome=?, decided_at=? WHERE id=?",
                        (json.dumps({"version": updated["version"]}), v.utc_now(), row["id"]))
        return Applied(revision=str(updated["version"]), evidence_id=row["id"])

    # --------------------------------------------------------------------------------------------------------------- proposals
    def _row(self, pid: str) -> dict[str, Any]:
        row = self.db.row("SELECT * FROM proposals WHERE id=?", (v.record_id(pid),))
        if row is None:
            raise NotFound("There is no such proposal.", hint="It may have been removed. Reload to see the proposals that exist.")
        return row

    @staticmethod
    def _out(row: dict[str, Any]) -> dict[str, Any]:
        out = {k: row[k] for k in ("id", "kind", "item_id", "base_version", "title", "status", "created_at", "decided_at")}
        out.update(artifact_id=f"{row['kind']}:{row['item_id']}", changes=json.loads(row["changes"]),
                   consent=json.loads(row["consent"]) if row["consent"] else None, outcome=json.loads(row["outcome"]) if row["outcome"] else None)
        return out

    def proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in STATUSES:
            raise BadRequest(f"status is one of {', '.join(STATUSES)}.")
        rows = self.db.rows("SELECT * FROM proposals" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC, id", (status,) if status else ())
        return [self._out(r) for r in rows]

    def get(self, pid: str) -> dict[str, Any]:
        return self._out(self._row(pid))

    def _preview(self, kind: str, item: str, data: dict[str, Any]) -> dict[str, Any]:
        """What the store would return for this update, with nothing kept (the transaction is rolled back)."""
        try:
            with self.db.transaction():
                raise _Rollback(getattr(self.store, KINDS[kind][1])(item, data))
        except _Rollback as done:
            return done.result

    def propose(self, artifact_id: Any, changes: Any, title: Any = None) -> dict[str, Any]:
        kind, item = split_artifact(artifact_id)
        if not isinstance(changes, dict) or not changes:
            raise BadRequest("'changes' must say what to change, for example {\"priority\": \"high\"}.")
        if "version" in changes:
            raise BadRequest("Do not send a version: the proposal is made for the version the item has now.")
        if self.db.scalar("SELECT COUNT(*) FROM proposals WHERE status='pending'") >= MAX_PENDING:
            raise LimitExceeded(f"At most {MAX_PENDING} proposals can wait for a decision.", hint="Approve or reject some first.")
        current = getattr(self.store, KINDS[kind][2])(item)
        after = self._preview(kind, item, {**changes, "version": current["version"]})
        if {k: x for k, x in after.items() if k not in ("version", "updated_at")} == {k: x for k, x in current.items() if k not in ("version", "updated_at")}:
            raise BadRequest("That change would not alter anything.")
        pid = v.new_id()
        self.db.execute("INSERT INTO proposals (id,kind,item_id,base_version,changes,title,status,created_at) VALUES (?,?,?,?,?,?,'pending',?)",
                        (pid, kind, item, current["version"], json.dumps(changes, sort_keys=True), v.text(title or "Suggested change", "title", TITLE), v.utc_now()))
        return self.get(pid)

    def reject(self, pid: str) -> dict[str, Any]:
        with self.db.transaction():
            row = self._row(pid)
            if row["status"] != "pending":
                raise Conflict(f"This proposal is already {row['status']}.")
            self.db.execute("UPDATE proposals SET status='rejected', decided_at=? WHERE id=?", (v.utc_now(), row["id"]))
        return self.get(pid)

    def approve(self, pid: str) -> dict[str, Any]:
        """The person approves: consent is recorded, then the change is committed through the outbox. Returns the proposal and the receipt."""
        with self.db.transaction():
            row = self._row(pid)
            if row["status"] != "pending":
                raise Conflict(f"This proposal is already {row['status']}.")
            self.db.execute("UPDATE proposals SET consent=? WHERE id=?", (json.dumps({"actor": ACTOR, "at": v.utc_now()}), row["id"]))
        request = planning_types().HostCommitRequest(project_id=PROJECT_ID, artifact_id=f"{row['kind']}:{row['item_id']}", expected_revision=str(row["base_version"]),
                                                     proposal_ref=row["id"], idempotency_key=f"splanner:{row['id']}")
        record = self.outbox.commit(request, actor=ACTOR)
        if record["status"] != "confirmed":
            reason = record["receipt"]["reason"]
            self.db.execute("UPDATE proposals SET status='failed', outcome=?, decided_at=? WHERE id=? AND status='pending'",
                            (json.dumps({"reason": reason}), v.utc_now(), row["id"]))
            raise Conflict(reason, hint="Reload to see the latest version, then ask for the change again.", details={"proposal": row["id"]})
        return {"proposal": self.get(pid), "receipt": record["receipt"]}


__all__ = ["PlannerCommits", "PROJECT_ID"]
