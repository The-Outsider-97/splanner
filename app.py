"""SPlanner's application object, settings, preferences and start-up. The shared mechanics (server, token handshake, config, database,
preferences file, SLAI lookup) come from ``applications.tools``; only what is specific to a planner is here."""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..tools import slai
from ..tools.config import Field, load_section
from ..tools.errors import AppError
from ..tools.server import Identity, LocalServer, Router, ServerOptions, add_server_arguments, refuse_remote_host, serve
from ..tools.storage import Pref, Prefs, default_data_dir
from ..tools.validation import utc_now
from . import __version__
from .store import PlannerStore

SLUG, TITLE, DEFAULT_PORT = "splanner", "SPlanner", 8769
HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "configs" / "config.yaml"

SETTINGS_FIELDS = {
    "port": Field(DEFAULT_PORT, 1, 65535),
    "reminder_poll_seconds": Field(20, 5, 3600),
    "max_request_bytes": Field(524_288, 16_384, 8 * 1024 * 1024),
    "backup_every_days": Field(7, 0, 365),
    "backup_keep": Field(8, 1, 100),
}

SOUNDS = tuple([f"notification_{n:02d}" for n in range(1, 10)] + [f"accepted_{n:02d}" for n in range(1, 4)] +
               [f"ringtone_{n:02d}" for n in range(1, 6)] + [f"warning_alert_{n:02d}" for n in range(1, 3)])
VIEWS = ("today", "calendar", "tasks", "notes", "board")

# Personal choices, saved in prefs.json next to the database. Every value is validated here, on the server, so a hand-edited file can
# never put the interface in a state it cannot display (the Prefs class drops anything invalid and says so).
PREF_SCHEMA = {
    "theme": Pref(str, allowed=("system", "light", "dark", "contrast")),
    "scale": Pref(float, low=0.85, high=1.4),
    "motion": Pref(str, allowed=("system", "reduce")),
    "workspace_name": Pref(str, max_len=40, pattern=r"[^\x00-\x1f<>]*"),
    "initials": Pref(str, max_len=3, pattern=r"[^\x00-\x1f<>\s]*"),
    "week_start": Pref(str, allowed=("monday", "sunday")),
    "time_format": Pref(str, allowed=("24h", "12h")),
    "day_start": Pref(int, low=0, high=22),
    "day_end": Pref(int, low=2, high=24),
    "default_view": Pref(str, allowed=VIEWS),
    "default_duration": Pref(int, low=5, high=720),
    "default_remind": Pref(int, low=-1, high=10_080),
    "sound": Pref(bool),
    "sound_file": Pref(str, allowed=SOUNDS),
    "sound_volume": Pref(float, low=0.0, high=1.0),
    "browser_notifications": Pref(bool),
}
PREF_DEFAULTS: dict[str, Any] = {
    "theme": "system", "scale": 1.0, "motion": "system", "workspace_name": "My workspace", "initials": "", "week_start": "monday",
    "time_format": "24h", "day_start": 8, "day_end": 18, "default_view": "today", "default_duration": 60, "default_remind": 10,
    "sound": True, "sound_file": "notification_09", "sound_volume": 0.6, "browser_notifications": False,
}


@dataclass
class Settings:
    port: int
    reminder_poll_seconds: int
    max_request_bytes: int
    backup_every_days: int
    backup_keep: int
    data_dir: Path
    config_path: Path
    note: str | None = None


def load_settings(config: str | None = None, data_dir: str | None = None, port: int | None = None) -> Settings:
    path = Path(config) if config else DEFAULT_CONFIG
    values, note = load_section(path, "app", SETTINGS_FIELDS, label="SPlanner configuration", overrides={"port": port})
    return Settings(data_dir=Path(data_dir) if data_dir else default_data_dir(SLUG, TITLE), config_path=path, note=note, **values)


class PlannerApp:
    """Everything a request handler needs, passed explicitly instead of through module globals."""

    def __init__(self, settings: Settings, store: PlannerStore, prefs: Prefs, root: Path | None) -> None:
        self.settings = settings
        self.store = store
        self.prefs = prefs
        self.root = root
        self.started = time.time()

    def preferences(self) -> dict[str, Any]:
        return {**PREF_DEFAULTS, **self.prefs.snapshot()}

    def notices(self) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        if self.settings.note:
            found.append({"kind": "info", "text": self.settings.note})
        if self.prefs.recovered:
            found.append({"kind": "warn", "text": self.prefs.recovered})
        if not slai.sldc_info()["available"]:
            found.append({"kind": "warn", "text": "SLAI's SLDC module was not found, so notes and sticky notes can't be opened or saved. "
                                                    "Events, tasks and reminders still work. Start SPlanner inside the SLAI folder or pass --slai-root."})
        if self.store.db.upgraded:
            backup = self.store.db.migration_backup
            found.append({"kind": "info", "text": "Your SPlanner data was upgraded to the new format." +
                          (f" A copy from before the upgrade is in {backup.parent}." if backup else "")})
        return found

    def bootstrap(self) -> dict[str, Any]:
        return {
            "app": {"name": TITLE, "version": __version__, "slai": slai.status(self.root)},
            "settings": {"reminderPollSeconds": self.settings.reminder_poll_seconds},
            "prefs": self.preferences(), "sounds": list(SOUNDS), "notices": self.notices(),
            "capabilities": {"sldc": bool(slai.sldc_info()["available"]), "recurrence": True, "sharing": False, "agentSuggestions": False,
                             "backgroundAlerts": False},
            "storage": {"dataDir": str(self.settings.data_dir), "backupsDir": str(self.store.backups_dir)},
            "counts": self.store.counts(), "data": self.store.snapshot(), "now": utc_now(),
        }


def automatic_backup(store: PlannerStore, settings: Settings) -> Path | None:
    """One database copy per ``backup_every_days`` (while there is something worth saving), pruned to ``backup_keep``."""
    if settings.backup_every_days <= 0:
        return None
    folder = store.backups_dir
    existing = sorted(folder.glob("auto-*.sqlite3"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    made: Path | None = None
    empty = sum(store.counts().values()) == 0
    if not empty and (not existing or time.time() - existing[-1].stat().st_mtime >= settings.backup_every_days * 86400):
        made = store.backup("auto")
        existing.append(made)
    for old in existing[: max(0, len(existing) - settings.backup_keep)]:
        try:
            old.unlink()
        except OSError:
            pass
    return made


def build_server(settings: Settings, token: str, *, root: Path | None, client_dir: Path | None = None, host: str = "127.0.0.1") -> tuple[LocalServer, PlannerApp]:
    from .routes import router

    store = PlannerStore(settings.data_dir)
    try:
        prefs = Prefs(settings.data_dir / "prefs.json", PREF_SCHEMA)
        app = PlannerApp(settings, store, prefs, root)
        options = ServerOptions(token=token, client_dir=client_dir or HERE / "dist", host=host, port=settings.port,
                                tools_web_dir=slai.applications_dir() / "tools" / "web",
                                asset_dirs=(HERE / "assets", slai.shared_assets_dir()), max_body_bytes=settings.max_request_bytes)
        return LocalServer(app, Identity(SLUG, TITLE), router, options), app
    except BaseException:
        store.close()
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m applications.splanner", description=f"{TITLE}: a local planning workspace.")
    add_server_arguments(parser, title=TITLE, default_port=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if refuse_remote_host(args.host, TITLE, "your plans and notes"):
        return 2
    root = slai.activate(slai.find_slai_root(args.slai_root))
    try:
        settings = load_settings(args.config, args.data_dir, args.port)
        client_dir = Path(args.client_dir) if args.client_dir else HERE / "dist"
        if not (client_dir / "index.html").is_file():
            print(f"The web client is not built ({client_dir / 'index.html'} is missing). Run `npm install` and `npm run build` in "
                  f"{HERE}.", file=sys.stderr)
            return 2
        token = args.token or secrets.token_urlsafe(32)
        server, app = build_server(settings, token, root=root, client_dir=client_dir, host=args.host)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    except AppError as exc:
        print(f"{exc.message} {exc.hint or ''}".strip(), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{TITLE} could not start: {exc.strerror or exc}. Is another copy already running? Try --port.", file=sys.stderr)
        return 1
    try:
        made = automatic_backup(app.store, settings)
    except (AppError, OSError) as exc:
        print(f"notice: the automatic backup was skipped ({getattr(exc, 'message', exc)})", file=sys.stderr)
        made = None
    notes = [n["text"] for n in app.notices()] + ([f"Database backed up to {made}"] if made else [])
    details = [("Data", str(settings.data_dir)), ("SLAI", str(root) if root else "not found"), ("Config", str(settings.config_path))]
    return serve(server, title=TITLE, version=__version__, url=f"http://127.0.0.1:{server.port}/?token={token}", details=details,
                 notes=notes, open_browser=not args.no_browser, on_close=app.store.close)
