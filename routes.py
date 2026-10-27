"""SPlanner's HTTP API. Handlers are thin: they unpack the request and call the store; all validation and rules live in ``store.py``."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

from ..tools import slai
from ..tools.server import Request, Response, Router, created, file_response
from . import __version__
from .app import PlannerApp

router = Router()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------------------------------------------- workspace
@router.route("GET", "/api/bootstrap")
def bootstrap(app: PlannerApp, req: Request) -> Any:
    return app.bootstrap()


@router.route("GET", "/api/snapshot")
def snapshot(app: PlannerApp, req: Request) -> Any:
    return {"data": app.store.snapshot(req.query.get("from") or None, req.query.get("to") or None), "counts": app.store.counts()}


@router.route("GET", "/api/counts")
def counts(app: PlannerApp, req: Request) -> Any:
    return app.store.counts()


@router.route("GET", "/api/status")
def status(app: PlannerApp, req: Request) -> Any:
    db, store = app.store.db, app.store
    backups = []
    if store.backups_dir.is_dir():
        for path in sorted(store.backups_dir.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            info = path.stat()
            backups.append({"name": path.name, "bytes": info.st_size, "at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()})
    return {"version": __version__, "python": sys.version.split()[0], "schemaVersion": db.schema_version, "integrity": db.integrity(),
            "databaseBytes": db.path.stat().st_size, "dataDir": str(store.data_dir), "backups": backups, "counts": store.counts(),
            "slai": slai.status(app.root), "uptimeSeconds": int(time.time() - app.started)}


@router.route("GET", "/api/search")
def search(app: PlannerApp, req: Request) -> Any:
    return app.store.search(req.str_query("q", max_len=120))


@router.route("GET", "/api/prefs")
def prefs_get(app: PlannerApp, req: Request) -> Any:
    return app.preferences()


@router.route("PATCH", "/api/prefs")
def prefs_patch(app: PlannerApp, req: Request) -> Any:
    app.prefs.update(req.json())
    return app.preferences()


@router.route("POST", "/api/backup")
def backup(app: PlannerApp, req: Request) -> Any:
    path = app.store.backup("manual")
    return created({"name": path.name, "folder": str(path.parent), "bytes": path.stat().st_size})


@router.route("GET", "/api/export/json")
def export_json(app: PlannerApp, req: Request) -> Response:
    body = json.dumps(app.store.export_all(), ensure_ascii=False, indent=2).encode("utf-8")
    return file_response(body, f"splanner-export-{_stamp()}.json", "application/json; charset=utf-8")


@router.route("GET", "/api/export/ics")
def export_ics(app: PlannerApp, req: Request) -> Response:
    return file_response(app.store.export_ics().encode("utf-8"), f"splanner-events-{_stamp()}.ics", "text/calendar; charset=utf-8")


# ---------------------------------------------------------------------------------------------------------------- events
@router.route("GET", "/api/events")
def events_list(app: PlannerApp, req: Request) -> Any:
    return app.store.list_events(req.query.get("from") or None, req.query.get("to") or None)


@router.route("GET", "/api/occurrences")
def occurrences(app: PlannerApp, req: Request) -> Any:
    """The occurrences of repeating events in a window (``from`` and ``to``; around now when both are left out)."""
    return app.store.occurrences(req.query.get("from") or None, req.query.get("to") or None)


@router.route("POST", "/api/events")
def events_create(app: PlannerApp, req: Request) -> Response:
    return created(app.store.create_event(req.json()))


@router.route("GET", "/api/events/{id}")
def events_get(app: PlannerApp, req: Request) -> Any:
    return app.store.get_event(req.params["id"])


@router.route("PATCH", "/api/events/{id}")
def events_update(app: PlannerApp, req: Request) -> Any:
    return app.store.update_event(req.params["id"], req.json())


@router.route("DELETE", "/api/events/{id}")
def events_delete(app: PlannerApp, req: Request) -> Any:
    app.store.delete_event(req.params["id"])
    return {"deleted": req.params["id"]}


# ---------------------------------------------------------------------------------------------------------------- tasks
@router.route("GET", "/api/tasks")
def tasks_list(app: PlannerApp, req: Request) -> Any:
    return app.store.list_tasks()


@router.route("POST", "/api/tasks")
def tasks_create(app: PlannerApp, req: Request) -> Response:
    return created(app.store.create_task(req.json()))


@router.route("POST", "/api/tasks/clear-completed")
def tasks_clear(app: PlannerApp, req: Request) -> Any:
    return {"deleted": app.store.clear_completed_tasks()}


@router.route("PATCH", "/api/tasks/{id}")
def tasks_update(app: PlannerApp, req: Request) -> Any:
    return app.store.update_task(req.params["id"], req.json())


@router.route("DELETE", "/api/tasks/{id}")
def tasks_delete(app: PlannerApp, req: Request) -> Any:
    app.store.delete_task(req.params["id"])
    return {"deleted": req.params["id"]}


# ---------------------------------------------------------------------------------------------------------------- notes
@router.route("GET", "/api/notes")
def notes_list(app: PlannerApp, req: Request) -> Any:
    return app.store.list_notes()


@router.route("POST", "/api/notes")
def notes_create(app: PlannerApp, req: Request) -> Response:
    return created(app.store.create_note(req.json()))


@router.route("GET", "/api/notes/{id}")
def notes_get(app: PlannerApp, req: Request) -> Any:
    return app.store.get_note(req.params["id"])


@router.route("GET", "/api/notes/{id}/file")
def notes_file(app: PlannerApp, req: Request) -> Response:
    body, name = app.store.note_file(req.params["id"])
    return file_response(body, name, "application/x-slai-sldc+zip")


@router.route("PATCH", "/api/notes/{id}")
def notes_update(app: PlannerApp, req: Request) -> Any:
    return app.store.update_note(req.params["id"], req.json())


@router.route("DELETE", "/api/notes/{id}")
def notes_delete(app: PlannerApp, req: Request) -> Any:
    app.store.delete_note(req.params["id"])
    return {"deleted": req.params["id"]}


# ---------------------------------------------------------------------------------------------------------------- reminders
@router.route("GET", "/api/reminders")
def reminders_list(app: PlannerApp, req: Request) -> Any:
    return app.store.list_reminders()


@router.route("GET", "/api/reminders/due")
def reminders_due(app: PlannerApp, req: Request) -> Any:
    return {"due": app.store.due_reminders(), "now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")}


@router.route("POST", "/api/reminders")
def reminders_create(app: PlannerApp, req: Request) -> Response:
    return created(app.store.create_reminder(req.json()))


@router.route("PATCH", "/api/reminders/{id}")
def reminders_change(app: PlannerApp, req: Request) -> Any:
    return app.store.change_reminder(req.params["id"], req.json())


@router.route("DELETE", "/api/reminders/{id}")
def reminders_delete(app: PlannerApp, req: Request) -> Any:
    app.store.delete_reminder(req.params["id"])
    return {"deleted": req.params["id"]}


# ---------------------------------------------------------------------------------------------------------------- stickies
@router.route("GET", "/api/stickies")
def stickies_list(app: PlannerApp, req: Request) -> Any:
    return app.store.list_stickies()


@router.route("POST", "/api/stickies")
def stickies_create(app: PlannerApp, req: Request) -> Response:
    return created(app.store.create_sticky(req.json()))


@router.route("PATCH", "/api/stickies/{id}")
def stickies_update(app: PlannerApp, req: Request) -> Any:
    return app.store.update_sticky(req.params["id"], req.json())


@router.route("DELETE", "/api/stickies/{id}")
def stickies_delete(app: PlannerApp, req: Request) -> Any:
    app.store.delete_sticky(req.params["id"])
    return {"deleted": req.params["id"]}
