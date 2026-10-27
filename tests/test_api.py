"""HTTP-level tests: a real SPlanner server on a loopback socket, a temporary data folder, real SQLite and SLDC."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from applications.splanner import app as app_module
from applications.splanner.app import PREF_DEFAULTS, automatic_backup, build_server, load_settings
from applications.tools import slai

TOKEN = "api-test-token"
ROOT = slai.activate(slai.find_slai_root())


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


NOW = datetime.now(timezone.utc).replace(microsecond=0)


class ApiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        client = base / "dist"
        client.mkdir()
        (client / "index.html").write_text("<!doctype html><title>SPlanner</title>", encoding="utf-8")
        settings = load_settings(config=str(base / "missing.yaml"), data_dir=str(base / "data"), port=None)
        settings.port = 0                                      # let the OS pick a free port (the config schema itself requires 1-65535)
        cls.server, cls.app = build_server(settings, TOKEN, root=ROOT, client_dir=client)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()
        cls.cookie = f"{cls.server.identity.cookie}={TOKEN}"
        cls.marker = cls.server.identity.marker

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.store.close()
        cls.tmp.cleanup()

    def call(self, method: str, path: str, body=None, *, raw=False, headers=None, cookie=True, marker=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        send = dict(headers or {})
        if cookie:
            send["Cookie"] = self.cookie
        if marker and method in ("POST", "PATCH", "PUT", "DELETE"):
            send[self.marker] = "1"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            send["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=send)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        if raw:
            return resp, payload
        return resp.status, (json.loads(payload) if payload else None)

    def data(self, method: str, path: str, body=None, status: int = 200):
        code, doc = self.call(method, path, body)
        self.assertEqual(code, status, doc)
        self.assertTrue(doc["ok"])
        return doc["data"]

    def error(self, method: str, path: str, body=None, status: int = 400):
        code, doc = self.call(method, path, body)
        self.assertEqual(code, status, doc)
        self.assertFalse(doc["ok"])
        return doc["error"]


class SessionTests(ApiCase):
    def test_page_needs_the_token_then_a_cookie(self) -> None:
        resp, body = self.call("GET", "/", cookie=False, raw=True)
        self.assertEqual(resp.status, 403)
        resp, _ = self.call("GET", f"/?token={TOKEN}", cookie=False, raw=True)
        self.assertEqual(resp.status, 302)
        self.assertIn("HttpOnly", resp.getheader("Set-Cookie"))
        resp, body = self.call("GET", "/", raw=True)
        self.assertEqual((resp.status, b"SPlanner" in body), (200, True))
        self.assertIn("script-src 'self'", resp.getheader("Content-Security-Policy"))

    def test_shared_ui_kit_and_assets_are_served_from_one_place(self) -> None:
        resp, body = self.call("GET", "/tools/slai-ui.css", raw=True)
        self.assertEqual((resp.status, b"--accent" in body), (200, True))
        resp, body = self.call("GET", "/assets/img/logo.svg", raw=True)
        self.assertEqual(resp.status, 200)
        resp, _ = self.call("GET", "/assets/audio/notification_09.mp3", raw=True)
        self.assertEqual((resp.status, resp.getheader("Accept-Ranges")), (200, "bytes"))

    def test_changes_need_the_marker_header(self) -> None:
        code, doc = self.call("POST", "/api/tasks", {"title": "x"}, marker=False)
        self.assertEqual(code, 401)
        code, doc = self.call("GET", "/api/bootstrap", cookie=False)
        self.assertEqual((code, doc["error"]["code"]), (401, "unauthorized"))


class WorkspaceTests(ApiCase):
    def test_bootstrap_describes_the_workspace(self) -> None:
        boot = self.data("GET", "/api/bootstrap")
        self.assertEqual(boot["app"]["name"], "SPlanner")
        self.assertTrue(boot["capabilities"]["sldc"])
        self.assertEqual(boot["prefs"], PREF_DEFAULTS)
        self.assertEqual(set(boot["data"]), {"events", "tasks", "notes", "reminders", "stickies", "occurrences", "window"})
        self.assertIn("notification_09", boot["sounds"])
        self.assertEqual(boot["settings"]["reminderPollSeconds"], 20)

    def test_event_task_note_reminder_sticky_flow(self) -> None:
        event = self.data("POST", "/api/events", {"title": "Review", "start_at": iso(NOW + timedelta(hours=1)), "end_at": iso(NOW + timedelta(hours=2)),
                                                     "time_zone": "UTC", "remind_before": 10}, 201)
        self.assertEqual(self.data("PATCH", f"/api/events/{event['id']}", {"title": "Review v2", "version": 1})["version"], 2)
        self.assertEqual(self.error("PATCH", f"/api/events/{event['id']}", {"title": "stale", "version": 1}, 409)["code"], "conflict")
        task = self.data("POST", "/api/tasks", {"title": "Do it", "priority": "high"}, 201)
        self.assertEqual(self.data("PATCH", f"/api/tasks/{task['id']}", {"status": "done"})["status"], "done")
        note = self.data("POST", "/api/notes", {"title": "Plan", "content": "Hello\nworld"}, 201)
        self.assertEqual(self.data("GET", f"/api/notes/{note['id']}")["content"], "Hello\nworld")
        self.assertEqual(self.data("PATCH", f"/api/notes/{note['id']}", {"content": "Bye", "version": 1})["content"], "Bye")
        sticky = self.data("POST", "/api/stickies", {"content": "Remember", "color": "pink", "x": 20, "y": 30}, 201)
        self.assertEqual(self.data("PATCH", f"/api/stickies/{sticky['id']}", {"x": 99, "version": 1})["x"], 99.0)
        reminder = self.data("POST", "/api/reminders", {"title": "Ping", "due_at": iso(NOW + timedelta(days=1))}, 201)
        self.assertEqual(self.data("PATCH", f"/api/reminders/{reminder['id']}", {"action": "snooze", "minutes": 5})["status"], "pending")
        snap = self.data("GET", "/api/snapshot")["data"]
        self.assertEqual((len(snap["events"]), len(snap["tasks"]), len(snap["notes"]), len(snap["stickies"])), (1, 1, 1, 1))
        self.assertEqual(len(snap["reminders"]), 2)                                   # the standalone one and the event's
        hits = self.data("GET", "/api/search?q=review")
        self.assertEqual([h["kind"] for h in hits], ["event"])
        for path in (f"/api/events/{event['id']}", f"/api/tasks/{task['id']}", f"/api/notes/{note['id']}", f"/api/stickies/{sticky['id']}",
                     f"/api/reminders/{reminder['id']}"):
            self.data("DELETE", path)
        snap = self.data("GET", "/api/snapshot")["data"]
        self.assertEqual({k: len(v) for k, v in snap.items() if k not in ("reminders", "window")}, {"events": 0, "tasks": 0, "notes": 0, "stickies": 0, "occurrences": 0})
        self.assertEqual(snap["reminders"], [])

    def test_errors_are_specific_and_use_the_envelope(self) -> None:
        err = self.error("POST", "/api/events", {"title": "x", "start_at": "soon", "end_at": iso(NOW)})
        self.assertEqual((err["code"], "start_at" in err["message"]), ("bad_request", True))
        self.assertEqual(self.error("GET", "/api/notes/" + "0" * 32, status=404)["code"], "not_found")
        self.assertEqual(self.error("GET", "/api/notes/not-an-id")["code"], "bad_request")
        self.assertEqual(self.error("GET", "/api/nothing-here", status=404)["code"], "not_found")
        self.assertEqual(self.error("PUT", "/api/tasks", {}, 405)["code"], "method_not_allowed")
        self.assertEqual(self.error("PATCH", "/api/prefs", {"theme": "neon"})["code"], "bad_request")
        self.assertEqual(self.error("POST", "/api/reminders", {"title": "x"})["code"], "bad_request")

    def test_due_reminders(self) -> None:
        past = self.data("POST", "/api/reminders", {"title": "Late", "due_at": iso(NOW - timedelta(minutes=2))}, 201)
        doc = self.data("GET", "/api/reminders/due")
        self.assertIn(past["id"], [r["id"] for r in doc["due"]])
        self.data("PATCH", f"/api/reminders/{past['id']}", {"action": "dismiss"})
        self.assertNotIn(past["id"], [r["id"] for r in self.data("GET", "/api/reminders/due")["due"]])
        self.data("DELETE", f"/api/reminders/{past['id']}")

    def test_preferences_are_validated_and_persist(self) -> None:
        prefs = self.data("PATCH", "/api/prefs", {"theme": "dark", "week_start": "sunday", "day_start": 7, "initials": "JR", "scale": 1.1})
        self.assertEqual((prefs["theme"], prefs["week_start"], prefs["day_start"], prefs["initials"]), ("dark", "sunday", 7, "JR"))
        self.assertEqual(prefs["time_format"], "24h")                                  # untouched values keep their defaults
        self.assertTrue((Path(self.app.settings.data_dir) / "prefs.json").is_file())
        for bad in ({"scale": 3}, {"initials": "ABCD"}, {"sound_file": "../../etc/passwd"}, {"nope": 1}, {"day_start": True}):
            self.error("PATCH", "/api/prefs", bad)
        self.data("PATCH", "/api/prefs", dict(PREF_DEFAULTS))

    def test_exports_and_downloads(self) -> None:
        self.data("POST", "/api/events", {"title": "Exported", "start_at": iso(NOW), "end_at": iso(NOW + timedelta(hours=1))}, 201)
        note = self.data("POST", "/api/notes", {"title": "Downloadable", "content": "x"}, 201)
        resp, body = self.call("GET", "/api/export/json", raw=True)
        self.assertIn("attachment", resp.getheader("Content-Disposition"))
        self.assertEqual(json.loads(body)["format"], "splanner-export/1")
        resp, body = self.call("GET", "/api/export/ics", raw=True)
        self.assertEqual(resp.getheader("Content-Type").split(";")[0], "text/calendar")
        self.assertIn(b"SUMMARY:Exported", body)
        resp, body = self.call("GET", f"/api/notes/{note['id']}/file", raw=True)
        self.assertEqual((body[:2], ".sldc" in resp.getheader("Content-Disposition")), (b"PK", True))

    def test_backup_and_status(self) -> None:
        made = self.data("POST", "/api/backup", {}, 201)
        self.assertTrue((Path(made["folder"]) / made["name"]).is_file())
        status = self.data("GET", "/api/status")
        self.assertEqual((status["integrity"], status["schemaVersion"]), ("ok", 3))
        self.assertIn(made["name"], [b["name"] for b in status["backups"]])

    def test_oversized_bodies_are_refused(self) -> None:
        err = self.error("POST", "/api/notes", {"title": "big", "content": "x" * (self.app.settings.max_request_bytes + 10)}, 413)
        self.assertEqual(err["code"], "limit_exceeded")


class AutomaticBackupTests(unittest.TestCase):
    def test_makes_one_when_due_prunes_and_skips_empty_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings(config=str(Path(tmp) / "none.yaml"), data_dir=str(Path(tmp) / "d"), port=None)
            settings.backup_keep = 2
            store = app_module.PlannerStore(settings.data_dir)
            try:
                self.assertIsNone(automatic_backup(store, settings))               # nothing to protect yet
                store.create_task({"title": "t"})
                first = automatic_backup(store, settings)
                self.assertTrue(first and first.is_file())
                self.assertIsNone(automatic_backup(store, settings))               # recent enough
                for n in range(3):                                                  # age them, then make more
                    for path in store.backups_dir.glob("auto-*.sqlite3"):
                        old = time.time() - (10 + n) * 86400
                        import os
                        os.utime(path, (old, old))
                    automatic_backup(store, settings)
                self.assertLessEqual(len(list(store.backups_dir.glob("auto-*.sqlite3"))), 2)
                settings.backup_every_days = 0
                self.assertIsNone(automatic_backup(store, settings))
            finally:
                store.close()


class SettingsTests(unittest.TestCase):
    def test_defaults_and_overrides_and_bad_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "c.yaml"
            cfg.write_text("app:\n  port: 9100\n  backup_keep: 3\n", encoding="utf-8")
            s = load_settings(str(cfg), tmp, None)
            self.assertEqual((s.port, s.backup_keep, s.reminder_poll_seconds), (9100, 3, 20))
            self.assertEqual(load_settings(str(cfg), tmp, 9200).port, 9200)
            cfg.write_text("app:\n  reminder_poll_seconds: 1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(str(cfg), tmp, None)
        shipped = load_settings(None, None, None)                                   # the shipped config.yaml parses with the real schema
        self.assertEqual((shipped.port, shipped.backup_every_days), (8769, 7))


if __name__ == "__main__":
    unittest.main()
