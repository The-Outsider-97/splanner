"""Authenticated SPlanner-to-SPlanner collaboration transport.

The browser never talks to the remote collaboration service or receives its
bearer token. The local loopback SPlanner backend is the sole network client.
Local data stays usable when the collaboration service is unavailable.
"""
from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ..tools.errors import BadRequest, Conflict, DependencyMissing


class RemoteError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


class CollaborationClient:
    def __init__(self, url: str | None, *, timeout: int = 15) -> None:
        url = (url or "").rstrip("/")
        parsed = urllib.parse.urlparse(url)
        self.available = bool(url and parsed.scheme == "https" and parsed.hostname)
        self.url = url
        self.timeout = timeout
        self._token: str | None = None
        self._user: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._context = ssl.create_default_context()

    @property
    def user(self) -> dict[str, Any] | None:
        return self._user

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 *, authentication: bool = True) -> Any:
        if not self.available:
            raise DependencyMissing("Configure an HTTPS collaboration service before sharing a planner.")
        if authentication and not self._token:
            raise DependencyMissing("Sign in to your collaboration account first.")
        url = self.url + "/v1/" + path.lstrip("/")
        raw = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token and authentication:
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(url, data=raw, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._context) as response:
                doc = json.loads(response.read(2 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            try:
                details = json.loads(exc.read(4096)).get("error", {}).get("message", "Remote request rejected")
            except (ValueError, TypeError):
                details = "Remote request rejected"
            raise RemoteError(exc.code, str(details)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DependencyMissing("Collaboration service is unreachable. Local planning is unaffected.") from exc
        if not doc.get("ok"):
            raise RemoteError(502, "Unexpected collaboration response")
        return doc["data"]

    def register(self, username: str, password: str) -> Any:
        return self._request("POST", "register", {"username": username, "password": password}, authentication=False)

    def login(self, username: str, password: str) -> Any:
        with self._lock:
            result = self._request("POST", "login", {"username": username, "password": password}, authentication=False)
            self._token = result["token"]
            self._user = {"id": result["user_id"], "username": result["username"]}
            return {"user": self._user, "expires_at": result["expires_at"]}

    def logout(self) -> None:
        with self._lock:
            if self._token:
                try:
                    self._request("POST", "logout", {})
                finally:
                    self._token, self._user = None, None

    def status(self) -> dict[str, Any]:
        return {"configured": self.available, "signed_in": self._token is not None, "user": self._user}

    def memberships(self) -> list[dict[str, Any]]:
        return self._request("GET", "workspaces")

    def members(self, wid: str) -> list[dict[str, Any]]:
        return self._request("GET", f"workspaces/{urllib.parse.quote(wid, safe='')}/members")

    def publish_workspace(self, wid: str, name: str) -> dict[str, Any]:
        return self._request("POST", "workspaces", {"id": wid, "name": name})

    def invite(self, wid: str, username: str, role: str = "viewer") -> dict[str, Any]:
        return self._request("POST", f"workspaces/{urllib.parse.quote(wid, safe='')}/invitations",
                             {"username": username, "role": role})

    def invitations(self) -> list[dict[str, Any]]:
        return self._request("GET", "invitations")

    def decide_invitation(self, iid: str, *, accept: bool) -> dict[str, Any]:
        return self._request("POST", f"invitations/{urllib.parse.quote(iid, safe='')}/{'accept' if accept else 'reject'}", {})

    def change_role(self, wid: str, user_id: str, role: str | None) -> dict[str, Any]:
        return self._request("PATCH", f"workspaces/{urllib.parse.quote(wid, safe='')}/members/{urllib.parse.quote(user_id, safe='')}",
                             {"role": role})

    def changes(self, wid: str, cursor: int = 0, limit: int = 300) -> dict[str, Any]:
        return self._request("GET", f"workspaces/{urllib.parse.quote(wid, safe='')}/changes?cursor={cursor}&limit={limit}")

    def send_change(self, wid: str, change: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"workspaces/{urllib.parse.quote(wid, safe='')}/changes", change)
