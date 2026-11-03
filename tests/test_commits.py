"""SPlanner connected to SLAI's commit contract: proposals, consent, the version-checked commit, receipts in the same transaction as the change,
and the failure modes. Real SQLite, SLAI's real planning modules and CommitReconciliationStore."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from applications.splanner import commits as C
from applications.splanner.commits import PROJECT_ID, PlannerCommits
from applications.splanner.store import PlannerStore
from applications.tools.commit_host import CommitOutcomeUnknown
from applications.tools.errors import BadRequest, Conflict, NotFound

from . import test_api as api_tests

HAVE_PLANNING = importlib.util.find_spec("src.agents.planning.commit_reconciliation") is not None
needs_planning = unittest.skipUnless(HAVE_PLANNING, "needs SLAI's src/agents/planning commit adapters")
START, END = "2026-11-03T09:00:00+00:00", "2026-11-03T10:00:00+00:00"


@needs_planning
class Committing(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = PlannerStore(Path(self.tmp.name)); self.addCleanup(self.store.close)
        self.commits = PlannerCommits(self.store); self.addCleanup(self.commits.close)
        self.task = self.store.create_task({"title": "Write report", "priority": "normal"})
        self.event = self.store.create_event({"title": "Review", "start_at": START, "end_at": END, "remind_before": 15})

    def reminders(self) -> list:
        return self.store.db.rows("SELECT id, due_at, status FROM reminders ORDER BY id")

    # ------------------------------------------------------------------------------------------------------------ revisions
    def test_revisions_are_the_records_own_versions_and_only_for_the_workspace(self) -> None:
        revisions = self.commits.host.current_revisions(PROJECT_ID)
        self.assertEqual(revisions, {f"task:{self.task['id']}": "1", f"event:{self.event['id']}": "1"})
        self.store.update_task(self.task["id"], {"priority": "low", "version": 1})
        self.assertEqual(self.commits.host.current_revisions(PROJECT_ID)[f"task:{self.task['id']}"], "2")
        self.assertEqual(self.commits.host.current_revisions("another"), {})

    # ------------------------------------------------------------------------------------------------------------ proposing
    def test_a_proposal_is_validated_by_the_real_update_and_changes_nothing(self) -> None:
        before_reminders = self.reminders()
        p = self.commits.propose(f"event:{self.event['id']}", {"start_at": "2026-11-03T11:00:00+00:00", "end_at": "2026-11-03T12:00:00+00:00"}, "Move the review")
        self.assertEqual((p["status"], p["base_version"], p["consent"], p["title"]), ("pending", 1, None, "Move the review"))
        self.assertEqual(self.store.get_event(self.event["id"])["start_at"], START)
        self.assertEqual(self.store.get_event(self.event["id"])["version"], 1)
        self.assertEqual(self.reminders(), before_reminders, "the preview's side effects were rolled back too")

    def test_what_the_store_would_refuse_never_becomes_a_proposal(self) -> None:
        for artifact, changes in ((f"task:{self.task['id']}", {"priority": "urgent"}),                                  # not a priority
                                  (f"event:{self.event['id']}", {"end_at": "2026-11-03T08:00:00+00:00"}),               # ends before it starts
                                  (f"task:{self.task['id']}", {"priority": "normal"}),                                  # changes nothing
                                  (f"task:{self.task['id']}", {}), (f"task:{self.task['id']}", {"version": 1, "priority": "low"}),
                                  (f"task:{self.task['id']}", {"colour": "red"}), ("note:abc", {"title": "x"}), ("junk", {"title": "x"})):
            with self.assertRaises(BadRequest, msg=(artifact, changes)):
                self.commits.propose(artifact, changes)
        with self.assertRaises(NotFound):
            self.commits.propose("task:" + "f" * 32, {"priority": "low"})
        self.assertEqual(self.commits.proposals(), [])

    # ------------------------------------------------------------------------------------------------------------ approving
    def test_approval_commits_the_change_with_a_receipt_written_in_the_same_transaction(self) -> None:
        p = self.commits.propose(f"task:{self.task['id']}", {"priority": "high", "status": "done"})
        done = self.commits.approve(p["id"])
        task = self.store.get_task(self.task["id"])
        self.assertEqual((task["priority"], task["status"], task["version"]), ("high", "done", 2))
        self.assertEqual((done["proposal"]["status"], done["proposal"]["outcome"], done["proposal"]["consent"]["actor"]), ("executed", {"version": 2}, C.ACTOR))
        self.assertEqual((done["receipt"]["status"], done["receipt"]["committed_revision"], done["receipt"]["evidence_id"]), ("confirmed", "2", p["id"]))
        key = f"splanner:{p['id']}"
        self.assertEqual(self.commits.host.lookup_commit(PROJECT_ID, key).committed_revision, "2")
        self.assertEqual(self.commits.outbox.store().get(key)["status"], "confirmed")
        self.assertEqual(self.commits.host.current_revisions(PROJECT_ID)[f"task:{self.task['id']}"], "2")

    def test_an_event_change_goes_through_the_stores_own_rules_including_its_reminder(self) -> None:
        p = self.commits.propose(f"event:{self.event['id']}", {"start_at": "2026-11-03T11:00:00+00:00", "end_at": "2026-11-03T12:00:00+00:00"})
        self.commits.approve(p["id"])
        self.assertEqual(self.store.get_event(self.event["id"])["start_at"], "2026-11-03T11:00:00+00:00")
        self.assertEqual([r["due_at"] for r in self.reminders()], ["2026-11-03T10:45:00+00:00"], "the reminder followed the event")

    def test_approving_twice_or_after_rejecting_is_refused(self) -> None:
        p = self.commits.propose(f"task:{self.task['id']}", {"priority": "high"})
        self.commits.approve(p["id"])
        with self.assertRaises(Conflict):
            self.commits.approve(p["id"])
        q = self.commits.propose(f"task:{self.task['id']}", {"priority": "low"})
        self.assertEqual(self.commits.reject(q["id"])["status"], "rejected")
        with self.assertRaises(Conflict):
            self.commits.approve(q["id"])
        self.assertEqual(self.store.get_task(self.task["id"])["priority"], "high")

    def test_a_proposal_made_for_an_old_version_is_rejected_and_nothing_is_applied(self) -> None:
        p = self.commits.propose(f"task:{self.task['id']}", {"priority": "high"})
        self.store.update_task(self.task["id"], {"title": "Write the final report", "version": 1})         # the person edits first
        with self.assertRaises(Conflict) as caught:
            self.commits.approve(p["id"])
        self.assertIn("Stale", caught.exception.message)
        task = self.store.get_task(self.task["id"])
        self.assertEqual((task["priority"], task["title"], task["version"]), ("normal", "Write the final report", 2))
        self.assertEqual(self.commits.get(p["id"])["status"], "failed")
        self.assertIn("Stale", self.commits.get(p["id"])["outcome"]["reason"])

    def test_the_host_refuses_a_proposal_nobody_approved(self) -> None:
        p = self.commits.propose(f"task:{self.task['id']}", {"priority": "high"})
        request = self.commits_request(p)
        receipt = self.commits.host.commit(request)
        self.assertEqual(receipt.status, "rejected"); self.assertIn("not been approved", receipt.reason)
        self.assertEqual(self.store.get_task(self.task["id"])["version"], 1)

    def commits_request(self, p):
        from src.agents.planning.project_adapter import HostCommitRequest
        return HostCommitRequest(PROJECT_ID, p["artifact_id"], str(p["base_version"]), p["id"], f"splanner:{p['id']}")

    # ---------------------------------------------------------------------------------------------------------- failure modes
    def test_if_the_receipt_cannot_be_written_the_change_is_not_kept(self) -> None:
        p = self.commits.propose(f"task:{self.task['id']}", {"priority": "high"})
        with mock.patch.object(C._DbLedger, "put", side_effect=OSError("disk full")):
            with self.assertRaises(CommitOutcomeUnknown):
                self.commits.approve(p["id"])
        self.assertEqual(self.store.get_task(self.task["id"])["priority"], "normal", "applied-but-unrecorded cannot happen")
        self.assertEqual(self.store.get_task(self.task["id"])["version"], 1)
        self.assertEqual(self.commits.get(p["id"])["status"], "pending")

    def test_a_lost_reply_is_reconciled_by_lookup_and_the_change_happens_once(self) -> None:
        p = self.commits.propose(f"task:{self.task['id']}", {"priority": "high"})
        real = self.commits.host.commit
        def lose_reply(request):
            real(request)
            raise ConnectionError("no reply")
        with mock.patch.object(self.commits.host, "commit", side_effect=lose_reply):
            done = self.commits.approve(p["id"])
        self.assertEqual(done["receipt"]["status"], "confirmed")
        self.assertEqual(self.store.get_task(self.task["id"])["version"], 2, "one step, applied once")

    # -------------------------------------------------------------------------------------------------------------- durability
    def test_proposals_and_receipts_survive_a_restart(self) -> None:
        p = self.commits.propose(f"task:{self.task['id']}", {"priority": "high"})
        self.commits.approve(p["id"])
        pending = self.commits.propose(f"task:{self.task['id']}", {"priority": "low"})
        self.commits.close(); self.store.close()
        store = PlannerStore(Path(self.tmp.name)); self.addCleanup(store.close)
        commits = PlannerCommits(store); self.addCleanup(commits.close)
        self.assertEqual(commits.host.lookup_commit(PROJECT_ID, f"splanner:{p['id']}").status, "confirmed")
        self.assertEqual({x["id"]: x["status"] for x in commits.proposals()}, {p["id"]: "executed", pending["id"]: "pending"})
        self.assertEqual(store.db.schema_version, 4)

    # ------------------------------------------------------------------------------------ SLAI's workflow store on top of it
    def test_a_plan_goes_stale_only_for_the_artifact_that_changed(self) -> None:
        from src.agents.planning.revision_workflow import RevisionTask, RevisionWorkflowStore
        wf = RevisionWorkflowStore(Path(self.tmp.name) / "plans.sqlite3", revision_provider=self.commits.host.current_revisions); self.addCleanup(wf.close)
        t, e = f"task:{self.task['id']}", f"event:{self.event['id']}"
        source = self.commits.host.current_revisions(PROJECT_ID)
        wf.create_plan(PROJECT_ID, [RevisionTask("reprioritise", reads={t: source[t]}, writes=(t,)),
                                    RevisionTask("reschedule", reads={e: source[e]}, writes=(e,))], source, plan_id="plan")
        self.commits.approve(self.commits.propose(t, {"priority": "high"})["id"])
        status = wf.refresh_authoritative_revisions("plan")["status"]
        self.assertEqual(status, {"reprioritise": "stale", "reschedule": "pending"})


@needs_planning
class OverHttp(api_tests.ApiCase):
    def test_propose_list_approve_reject_and_the_errors_over_http(self) -> None:
        task = self.data("POST", "/api/tasks", {"title": "HTTP task"}, 201)
        p = self.data("POST", "/api/proposals", {"artifact_id": f"task:{task['id']}", "changes": {"priority": "high"}, "title": "Raise it"}, 201)
        self.assertEqual(self.data("GET", "/api/proposals?status=pending")[0]["id"], p["id"])
        done = self.data("POST", f"/api/proposals/{p['id']}/approve")
        self.assertEqual((done["proposal"]["status"], done["receipt"]["status"]), ("executed", "confirmed"))
        self.assertEqual([t for t in self.data("GET", "/api/tasks") if t["id"] == task["id"]][0]["priority"], "high")
        self.assertEqual(self.error("POST", f"/api/proposals/{p['id']}/approve", status=409)["code"], "conflict")
        other = self.data("POST", "/api/proposals", {"artifact_id": f"task:{task['id']}", "changes": {"priority": "low"}}, 201)
        self.assertEqual(self.data("POST", f"/api/proposals/{other['id']}/reject")["status"], "rejected")
        self.error("POST", "/api/proposals", {"artifact_id": f"task:{task['id']}", "changes": {"priority": "urgent"}}, 400)
        self.error("POST", f"/api/proposals/{'0' * 32}/approve", status=404)
        self.assertEqual(self.call("POST", "/api/proposals", {"artifact_id": f"task:{task['id']}", "changes": {"priority": "low"}}, marker=False)[0], 401,
                         "a page that is not SPlanner's own cannot make or approve proposals")
        self.assertEqual(self.call("GET", "/api/proposals", cookie=False)[0], 401)


if __name__ == "__main__":
    unittest.main()
