"""Real-browser harness: starts the actual SPlanner service on a free loopback port with a throw-away data folder and drives the built
client in Chromium through Playwright with real mouse and keyboard input. Nothing is stubbed.

Run (from applications/splanner):  python -m unittest discover -s tests_e2e -v
Needs:  pip install playwright, a Chromium that Playwright can find, and a built client (npm run build).
Screenshots of the main screens are written to docs/screenshots/ when SPLANNER_SHOTS=1.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent
SLAI_ROOT = APP.parent.parent
SHOTS = APP / "docs" / "screenshots"
TOKEN = "e2e-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


class Service:
    """The real service in a subprocess (so a restart test really restarts it)."""

    def __init__(self, root: Path) -> None:
        self.root, self.port, self.proc = root, free_port(), None
        self.log = root / "service.log"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={TOKEN}"

    def start(self) -> None:
        cmd = [sys.executable, "-m", "applications.splanner", "--no-browser", "--port", str(self.port), "--token", TOKEN,
               "--data-dir", str(self.root / "data"), "--slai-root", str(SLAI_ROOT)]
        with open(self.log, "ab") as out:
            self.proc = subprocess.Popen(cmd, cwd=str(SLAI_ROOT), stdout=out, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"service exited: {self.log.read_text(errors='replace')[-800:]}")
                time.sleep(0.1)
        raise RuntimeError("service did not start")

    def restart(self) -> None:
        """A real restart on a fresh port (the old one may linger in TIME_WAIT; the service never sets SO_REUSEADDR)."""
        self.stop()
        self.port = free_port()
        self.start()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


class E2E(unittest.TestCase):
    """One browser per test class; every test gets its own service, data folder and page."""
    pw = None
    browser = None
    viewport = {"width": 1286, "height": 724}
    expect_errors = False                                    # tests that provoke a refusal check for it themselves
    allow_statuses: tuple[int, ...] = ()                     # HTTP statuses a test provokes on purpose (the browser logs each as a console error)

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise unittest.SkipTest("playwright is not installed") from exc
        if not (APP / "dist" / "index.html").is_file():
            raise unittest.SkipTest("the client is not built (run: npm run build)")
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.browser:
            cls.browser.close()
        if cls.pw:
            cls.pw.stop()

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="splanner-e2e-"))
        self.svc = Service(self.tmp)
        self.svc.start()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.svc.stop)
        self.errors: list[str] = []
        self.ctx = self.browser.new_context(viewport=self.viewport, timezone_id="UTC", locale="en-US", accept_downloads=True)
        self.addCleanup(self.ctx.close)
        self.page = self.ctx.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        self.opened = False

    def open(self, route: str = "#/today") -> None:
        """Opens the app (the first call trades the token for the cookie), then goes to a hash route."""
        if not self.opened:
            self.page.goto(self.svc.url)
            self.opened = True
        base = f"http://127.0.0.1:{self.svc.port}/"
        self.page.goto(base + route)
        self.page.reload()                                   # a hash change alone would not re-run the boot
        self.page.wait_for_selector(".shell")

    def tearDown(self) -> None:
        if not self.expect_errors:
            allowed = tuple(f"status of {code}" for code in self.allow_statuses)
            unexpected = [e for e in self.errors if not (e.startswith("console.error: Failed to load resource") and any(a in e for a in allowed))]
            self.assertEqual(unexpected, [], "the browser reported errors")

    # ------------------------------------------------------------------ talking to the service from inside the page
    def api(self, method: str, path: str, body: dict | None = None, *, status: int = 200):
        if not self.opened:
            self.page.goto(self.svc.url)
            self.opened = True
        result = self.page.evaluate(
            """async ([method, path, body]) => {
                const r = await fetch(path, { method, headers: { 'X-SLAI-Splanner': '1', 'Content-Type': 'application/json' },
                                              body: body ? JSON.stringify(body) : undefined });
                return { status: r.status, json: await r.json().catch(() => null) };
            }""", [method, path, body])
        self.assertEqual(result["status"], status, f"{method} {path}: {result['json']}")
        return (result["json"] or {}).get("data")

    def seed_week(self) -> None:
        """A small, realistic week around today, as a person might have."""
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        def at(day: int, hour: int, minute: int = 0) -> str:
            return iso(today + timedelta(days=day, hours=hour, minutes=minute))
        for title, day, hour, minutes, colour in (("Team sync", 0, 9, 60, "blue"), ("Study session", 0, 11, 120, "violet"),
                                                  ("Deadline review", 1, 14, 90, "amber"), ("Focus work", 2, 10, 60, "pink")):
            self.api("POST", "/api/events", {"title": title, "start_at": at(day, hour), "end_at": at(day, hour, minutes), "color": colour}, status=201)
        for title, priority in (("Finish the portfolio", "high"), ("Review research notes", "normal"), ("Send project update", "high")):
            self.api("POST", "/api/tasks", {"title": title, "priority": priority}, status=201)
        self.api("POST", "/api/notes", {"title": "Meeting notes", "content": "Agenda and follow-ups."}, status=201)

    def shot(self, name: str) -> None:
        if os.environ.get("SPLANNER_SHOTS"):
            SHOTS.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(SHOTS / f"{name}.png"))

    def toast_text(self) -> str:
        return self.page.locator(".toast").last.inner_text()
