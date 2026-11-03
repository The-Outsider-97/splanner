"""Additional SPlanner v2.4 routes. Registered once from PlannerApp.build_server.

SPlanner's existing endpoints remain unchanged, as do their JSON envelope,
loopback origin restrictions, session marker and centralized error handling.
"""
from __future__ import annotations

from typing import Any

from ..tools.errors import BadRequest, Conflict, Forbidden, NotFound, Unauthorized
from ..tools.server import Request, Router, created
from .collaboration_client import RemoteError
from .document_import import DocumentImporter


def register(router: Router) -> None:
    if getattr(router, "_splanner_v24_registered", False):
        return
    router._splanner_v24_registered = True  # type: ignore[attr-defined]

    @router.route("GET", "/api/workspaces")
    def list_workspaces(app: Any, req: Request) -> Any:
        return {"active_id": app.store.current_workspace_id, "workspaces": app.store.list_workspaces(),
                "archived": app.store.list_workspaces(archived=True)}

    @router.route("POST", "/api/workspaces")
    def create_workspace(app: Any, req: Request) -> Any:
        return created(app.store.create_workspace(req.json()))

    @router.route("GET", "/api/workspaces/{id}")
    def get_workspace(app: Any, req: Request) -> Any:
        return app.store.get_workspace(req.params["id"])

    @router.route("PATCH", "/api/workspaces/{id}")
    def patch_workspace(app: Any, req: Request) -> Any:
        return app.store.update_workspace(req.params["id"], req.json())

    @router.route("POST", "/api/workspaces/{id}/select")
    def select_workspace(app: Any, req: Request) -> Any:
        result = app.store.select_workspace(req.params["id"])
        return {"workspace": result, "data": app.store.snapshot(), "counts": app.store.counts()}

    @router.route("POST", "/api/workspaces/{id}/archive")
    def archive_workspace(app: Any, req: Request) -> Any:
        return app.store.archive_workspace(req.params["id"], True)

    @router.route("POST", "/api/workspaces/{id}/restore")
    def restore_workspace(app: Any, req: Request) -> Any:
        return app.store.archive_workspace(req.params["id"], False)

    @router.route("DELETE", "/api/workspaces/{id}")
    def delete_workspace(app: Any, req: Request) -> Any:
        app.store.delete_workspace(req.params["id"])
        return {"deleted": req.params["id"]}

    @router.route("POST", "/api/workspaces/move-resource")
    def move_resource(app: Any, req: Request) -> Any:
        data = req.json()
        app.store.move_resource(data.get("kind"), data.get("resource_id"), data.get("target_workspace_id"))
        return {"moved": data.get("resource_id"), "workspace_id": data.get("target_workspace_id")}

    @router.route("GET", "/api/document-imports")
    def history(app: Any, req: Request) -> Any:
        return DocumentImporter(app.store, max_bytes=app.settings.sldc_import_max_bytes).history()

    @router.route("POST", "/api/document-imports")
    def stage(app: Any, req: Request) -> Any:
        data = req.json()
        return created(DocumentImporter(app.store, max_bytes=app.settings.sldc_import_max_bytes).stage(
            data.get("filename", ""), data.get("base64", ""), zone=data.get("time_zone", "UTC")))

    @router.route("GET", "/api/document-imports/{id}")
    def preview(app: Any, req: Request) -> Any:
        return DocumentImporter(app.store).preview(req.params["id"])

    @router.route("POST", "/api/document-imports/{id}/commit")
    def commit(app: Any, req: Request) -> Any:
        data = req.json()
        return DocumentImporter(app.store).commit(req.params["id"], data.get("selected"))

    @router.route("POST", "/api/document-imports/{id}/cancel")
    def cancel(app: Any, req: Request) -> Any:
        return DocumentImporter(app.store).cancel(req.params["id"])

    def remote(call: Any) -> Any:
        try:
            return call()
        except RemoteError as exc:
            if exc.status == 401:
                raise Unauthorized(exc.message) from exc
            if exc.status == 403:
                raise Forbidden(exc.message) from exc
            if exc.status == 404:
                raise NotFound(exc.message) from exc
            if exc.status == 409:
                raise Conflict(exc.message) from exc
            raise BadRequest(exc.message) from exc

    @router.route("GET", "/api/collaboration/status")
    def collab_status(app: Any, req: Request) -> Any:
        return {**app.sync.summary(), **app.collaboration.status()}

    @router.route("POST", "/api/collaboration/register")
    def collab_register(app: Any, req: Request) -> Any:
        data = req.json()
        return created(remote(lambda: app.collaboration.register(data.get("username"), data.get("password"))))

    @router.route("POST", "/api/collaboration/login")
    def collab_login(app: Any, req: Request) -> Any:
        data = req.json()
        return remote(lambda: app.collaboration.login(data.get("username"), data.get("password")))

    @router.route("POST", "/api/collaboration/logout")
    def collab_logout(app: Any, req: Request) -> Any:
        remote(app.collaboration.logout)
        app.store.db.execute("DELETE FROM remote_access")  # cached copies are no longer visible after sign-out
        return {"signed_out": True}

    @router.route("GET", "/api/collaboration/workspaces")
    def collab_workspaces(app: Any, req: Request) -> Any:
        return remote(app.collaboration.memberships)

    @router.route("POST", "/api/collaboration/publish")
    def collab_publish(app: Any, req: Request) -> Any:
        ws = app.store.get_workspace(app.store.current_workspace_id)
        if app.collaboration.user is None:
            raise Unauthorized("Sign in before publishing a planner.")
        remote(lambda: app.collaboration.publish_workspace(ws["id"], ws["name"]))
        app.store.publish_workspace(ws["id"], app.collaboration.user["id"])
        return {"published": ws["id"], "sync": app.sync.sync_once()}

    @router.route("GET", "/api/collaboration/invitations")
    def collab_invitations(app: Any, req: Request) -> Any:
        return remote(app.collaboration.invitations)

    @router.route("POST", "/api/collaboration/invitations/{id}/accept")
    def collab_accept(app: Any, req: Request) -> Any:
        result = remote(lambda: app.collaboration.decide_invitation(req.params["id"], accept=True))
        app.sync.sync_once()
        return result

    @router.route("POST", "/api/collaboration/invitations/{id}/reject")
    def collab_reject(app: Any, req: Request) -> Any:
        return remote(lambda: app.collaboration.decide_invitation(req.params["id"], accept=False))

    @router.route("POST", "/api/collaboration/workspaces/{id}/invite")
    def collab_invite(app: Any, req: Request) -> Any:
        data = req.json()
        return created(remote(lambda: app.collaboration.invite(req.params["id"], data.get("username"), data.get("role", "viewer"))))

    @router.route("GET", "/api/collaboration/workspaces/{id}/members")
    def collab_members(app: Any, req: Request) -> Any:
        return remote(lambda: app.collaboration.members(req.params["id"]))

    @router.route("PATCH", "/api/collaboration/workspaces/{id}/members/{member_id}")
    def collab_role(app: Any, req: Request) -> Any:
        return remote(lambda: app.collaboration.change_role(req.params["id"], req.params["member_id"], req.json().get("role")))

    @router.route("GET", "/api/collaboration/conflicts")
    def collab_conflicts(app: Any, req: Request) -> Any:
        return app.store.unresolved_conflicts()

    @router.route("POST", "/api/collaboration/resolve")
    def collab_resolve(app: Any, req: Request) -> Any:
        data = req.json()
        result = app.store.resolve_sync_conflict(data.get("workspace_id"), data.get("resource_type"),
                                                 data.get("resource_id"), data.get("choice"))
        return {"resolved": result, "sync": app.sync.sync_once()}

    @router.route("POST", "/api/collaboration/sync")
    def collab_sync(app: Any, req: Request) -> Any:
        return app.sync.sync_once()
