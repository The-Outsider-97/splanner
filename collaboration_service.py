"""Standalone HTTPS SPlanner collaboration service (stdlib + SQLite).

This is NOT the local SPlanner HTTP server. Start separately behind a properly
configured TLS endpoint. The service never receives a local SPlanner cookie or
reads any user's local planner.sqlite3. Sharing is explicit and authorized.

Example:
  python -m applications.splanner.collaboration_service --host 0.0.0.0 \
    --port 9443 --db /srv/splanner/collaboration.sqlite3 \
    --cert /etc/ssl/splanner/fullchain.pem --key /etc/ssl/splanner/key.pem
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

MAX_BODY = 1024 * 1024
INVITE_TTL = 72 * 3600
SESSION_TTL = 30 * 86400
ROLES = ("owner", "editor", "viewer")
TYPES = ("event", "task", "note", "sticky", "reminder")


class ServiceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status, self.message = status, message
        super().__init__(message)


def _now() -> int:
    return int(time.time())


def _token() -> str:
    return secrets.token_urlsafe(32)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _password_hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def _as_object(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ServiceError(400, "Expected a JSON object")
    return data


class CollaborationDB:
    """Transactionally updates shared resources with optimistic revisions."""

    def __init__(self, path: str | Path, *, clock: Any = _now) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.clock = clock
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, salt TEXT NOT NULL, created_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            expires_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, owner_id TEXT NOT NULL REFERENCES users(id),
            created_at INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS memberships (
            workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id),
            role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
            PRIMARY KEY(workspace_id,user_id));
          CREATE TABLE IF NOT EXISTS invitations (
            id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            invited_user_id TEXT NOT NULL REFERENCES users(id),
            token_hash TEXT NOT NULL, role TEXT NOT NULL,
            expires_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS resources (
            workspace_id TEXT NOT NULL REFERENCES workspaces(id),
            resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
            owner_id TEXT NOT NULL REFERENCES users(id), revision INTEGER NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'workspace',
            selected_users TEXT NOT NULL DEFAULT '[]', deleted INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL, modified_at INTEGER NOT NULL,
            PRIMARY KEY(workspace_id,resource_type,resource_id));
          CREATE TABLE IF NOT EXISTS revision_snapshots (
            workspace_id TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
            revision INTEGER NOT NULL, payload_json TEXT NOT NULL,
            PRIMARY KEY(workspace_id,resource_type,resource_id,revision));
          CREATE TABLE IF NOT EXISTS change_log (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL,
            resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
            actor_id TEXT NOT NULL, revision INTEGER NOT NULL, changed_at INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS changes_workspace_cursor ON change_log(workspace_id,seq);
          CREATE TABLE IF NOT EXISTS applied_ops (
            op_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
            response_json TEXT NOT NULL);
        """)

    def close(self) -> None:
        self.db.close()

    def _one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _many(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def _execute(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        self.db.execute(sql, args)

    def register(self, username: str, password: str) -> dict[str, str]:
        if not isinstance(username, str) or not 3 <= len(username) <= 80 or not username.replace("_", "").replace("-", "").isalnum():
            raise ServiceError(400, "Username must contain 3-80 letters, numbers, hyphens or underscores")
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ServiceError(400, "Password must contain at least 12 characters")
        uid, salt = secrets.token_hex(16), secrets.token_bytes(16)
        with self.lock:
            try:
                self._execute("INSERT INTO users VALUES (?,?,?,?,?)",
                              (uid, username, _password_hash(password, salt), salt.hex(), self.clock()))
            except sqlite3.IntegrityError as exc:
                raise ServiceError(409, "Username is already registered") from exc
        return {"user_id": uid, "username": username}

    def login(self, username: str, password: str) -> dict[str, Any]:
        if not isinstance(username, str) or not isinstance(password, str):
            raise ServiceError(401, "Invalid credentials")
        with self.lock:
            user = self._one("SELECT * FROM users WHERE username=?", (username,))
            if not user:
                # Equalize expensive hashing time for known and unknown users.
                _password_hash(password[:1024], bytes(16))
                raise ServiceError(401, "Invalid credentials")
            try:
                derived = _password_hash(password, bytes.fromhex(user["salt"]))
            except ValueError:
                raise ServiceError(401, "Invalid credentials")
            if not hmac.compare_digest(derived, user["password_hash"]):
                raise ServiceError(401, "Invalid credentials")
            token, expires = _token(), self.clock() + SESSION_TTL
            self._execute("INSERT INTO sessions VALUES (?,?,?)", (_digest(token), user["id"], expires))
            return {"token": token, "expires_at": expires, "user_id": user["id"], "username": user["username"]}

    def whoami(self, token: str) -> dict[str, Any]:
        with self.lock:
            session = self._one("SELECT s.user_id,u.username,s.expires_at FROM sessions s "
                                "JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (_digest(token),))
            if session is None or session["expires_at"] <= self.clock():
                raise ServiceError(401, "Authentication required")
            return session

    def signout(self, token: str) -> None:
        with self.lock:
            self._execute("DELETE FROM sessions WHERE token_hash=?", (_digest(token),))

    def _membership(self, uid: str, wid: str, *, write: bool = False, owner: bool = False) -> str:
        row = self._one("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid))
        if not row:
            raise ServiceError(403, "Workspace access denied")
        role = row["role"]
        if owner and role != "owner":
            raise ServiceError(403, "Workspace owner required")
        if write and role == "viewer":
            raise ServiceError(403, "Editing permission required")
        return role

    def create_workspace(self, uid: str, workspace_id: str, name: str) -> dict[str, Any]:
        if not isinstance(workspace_id, str) or not 8 <= len(workspace_id) <= 80 or not workspace_id.replace("-", "").isalnum():
            raise ServiceError(400, "Invalid workspace ID")
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 80:
            raise ServiceError(400, "Invalid workspace name")
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                self._execute("INSERT INTO workspaces (id,title,owner_id,created_at) VALUES (?,?,?,?)",
                              (workspace_id, name.strip(), uid, self.clock()))
                self._execute("INSERT INTO memberships VALUES (?,?,?)", (workspace_id, uid, "owner"))
                self.db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                self.db.execute("ROLLBACK")
                raise ServiceError(409, "Workspace already exists") from exc
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return {"id": workspace_id, "name": name, "role": "owner"}

    def memberships(self, uid: str) -> list[dict[str, Any]]:
        with self.lock:
            return self._many("SELECT w.id,w.title AS name,w.owner_id,m.role FROM memberships m "
                              "JOIN workspaces w ON w.id=m.workspace_id WHERE m.user_id=? AND w.archived=0", (uid,))

    def members(self, uid: str, wid: str) -> list[dict[str, Any]]:
        with self.lock:
            self._membership(uid, wid)
            return self._many("SELECT u.id AS user_id,u.username,m.role FROM memberships m "
                              "JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?", (wid,))

    def invite(self, uid: str, wid: str, username: str, role: str) -> dict[str, Any]:
        if role not in ("viewer", "editor"):
            raise ServiceError(400, "Invite role must be editor or viewer")
        with self.lock:
            self._membership(uid, wid, owner=True)
            invited = self._one("SELECT id FROM users WHERE username=?", (username,))
            if not invited:
                raise ServiceError(404, "User is not registered")
            if invited["id"] == uid:
                raise ServiceError(400, "You already own this workspace")
            if self._one("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?", (wid, invited["id"])):
                raise ServiceError(409, "User is already a member")
            token, iid = _token(), secrets.token_hex(16)
            expires = self.clock() + INVITE_TTL
            self._execute("INSERT INTO invitations VALUES (?,?,?,?,?,?,?,?)",
                          (iid, wid, invited["id"], _digest(token), role, expires, "pending", self.clock()))
            # The invitation is also discoverable by the invited user's private inbox.
            return {"id": iid, "workspace_id": wid, "invite_token": token, "expires_at": expires, "role": role}

    def invitations(self, uid: str) -> list[dict[str, Any]]:
        with self.lock:
            return self._many("SELECT i.id,i.workspace_id,w.title AS workspace_name,i.role,i.expires_at FROM invitations i "
                              "JOIN workspaces w ON w.id=i.workspace_id WHERE i.invited_user_id=? "
                              "AND i.status='pending' AND i.expires_at>?", (uid, self.clock()))

    def decide_invitation(self, uid: str, invitation_id: str, accept: bool) -> dict[str, Any]:
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self._one("SELECT * FROM invitations WHERE id=? AND invited_user_id=?",
                                (invitation_id, uid))
                if not row or row["status"] != "pending" or row["expires_at"] <= self.clock():
                    raise ServiceError(404, "Invitation no longer available")
                self._execute("UPDATE invitations SET status=? WHERE id=?", ("accepted" if accept else "rejected", invitation_id))
                if accept:
                    self._execute("INSERT INTO memberships VALUES (?,?,?)", (row["workspace_id"], uid, row["role"]))
                self.db.execute("COMMIT")
                return {"workspace_id": row["workspace_id"], "accepted": accept}
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def change_role(self, uid: str, wid: str, member_uid: str, role: str | None) -> dict[str, Any]:
        with self.lock:
            self._membership(uid, wid, owner=True)
            owner = self._one("SELECT owner_id FROM workspaces WHERE id=?", (wid,))
            if owner and owner["owner_id"] == member_uid:
                raise ServiceError(409, "Cannot remove or demote the workspace owner")
            if role is not None and role not in ("editor", "viewer"):
                raise ServiceError(400, "Invalid role")
            if role is None:
                self._execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (wid, member_uid))
            else:
                cur = self.db.execute("UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?",
                                      (role, wid, member_uid))
                if cur.rowcount == 0:
                    raise ServiceError(404, "Member not found")
            return {"workspace_id": wid, "member_id": member_uid, "role": role}

    def _can_view_record(self, uid: str, record: dict[str, Any]) -> bool:
        return (record["visibility"] == "workspace" or uid == record["owner_id"] or
                (record["visibility"] == "selected" and uid in json.loads(record["selected_users"])))

    def read_changes(self, uid: str, wid: str, cursor: int = 0, limit: int = 300) -> dict[str, Any]:
        with self.lock:
            self._membership(uid, wid)
            if cursor < 0 or not 1 <= limit <= 500:
                raise ServiceError(400, "Invalid sync cursor or limit")
            entries = self._many("SELECT c.seq,c.resource_type,c.resource_id,c.revision,r.owner_id,r.visibility,"
                                 "r.selected_users,r.deleted,r.payload_json FROM change_log c JOIN resources r ON "
                                 "r.workspace_id=c.workspace_id AND r.resource_type=c.resource_type AND r.resource_id=c.resource_id "
                                 "WHERE c.workspace_id=? AND c.seq>? ORDER BY c.seq LIMIT ?", (wid, cursor, limit))
            next_cursor = max([cursor, *(x["seq"] for x in entries)])
            visible = [{"seq": e["seq"], "resource_type": e["resource_type"], "resource_id": e["resource_id"],
                        "revision": e["revision"], "owner_id": e["owner_id"], "deleted": bool(e["deleted"]),
                        "payload": json.loads(e["payload_json"])} for e in entries if self._can_view_record(uid, e)]
            return {"changes": visible, "cursor": next_cursor, "has_more": len(entries) == limit}

    def write_change(self, uid: str, wid: str, candidate: dict[str, Any]) -> dict[str, Any]:
        typ, rid = candidate.get("resource_type"), candidate.get("resource_id")
        op, op_id = candidate.get("operation"), candidate.get("op_id")
        base = candidate.get("base_revision")
        if typ not in TYPES or not isinstance(rid, str) or not 1 <= len(rid) <= 128 or not rid.replace("-", "").replace("_", "").isalnum():
            raise ServiceError(400, "Invalid resource identity")
        if op not in ("upsert", "delete") or not isinstance(op_id, str) or not 16 <= len(op_id) <= 128:
            raise ServiceError(400, "Invalid operation")
        if type(base) is not int or base < 0:
            raise ServiceError(400, "base_revision must be a nonnegative integer")
        fields = candidate.get("fields", {})
        if not isinstance(fields, dict) or len(json.dumps(fields)) > 300_000:
            raise ServiceError(400, "Invalid or oversized resource payload")
        if any(not isinstance(k, str) or len(k) > 80 for k in fields):
            raise ServiceError(400, "Invalid resource field")
        visibility = candidate.get("visibility", "workspace")
        selected = candidate.get("selected_users", [])
        if visibility not in ("workspace", "selected") or not isinstance(selected, list) or any(not isinstance(u, str) for u in selected):
            raise ServiceError(400, "Invalid sharing scope")
        with self.lock:
            self._membership(uid, wid, write=True)
            try:
                self.db.execute("BEGIN IMMEDIATE")
                already = self._one("SELECT response_json,user_id FROM applied_ops WHERE op_id=?", (op_id,))
                if already:
                    if already["user_id"] != uid:
                        raise ServiceError(409, "Operation identifier collision")
                    self.db.execute("COMMIT")
                    return json.loads(already["response_json"])
                row = self._one("SELECT * FROM resources WHERE workspace_id=? AND resource_type=? AND resource_id=?", (wid, typ, rid))
                if row and row["owner_id"] != uid:
                    # An editor may modify workspace-visible records, not private/selected records belonging to somebody else.
                    if row["visibility"] != "workspace":
                        raise ServiceError(403, "Item editing permission denied")
                    if visibility != row["visibility"] or selected != json.loads(row["selected_users"]):
                        raise ServiceError(403, "Only the owner may change item visibility")
                if row is None and base != 0:
                    raise ServiceError(409, "Missing source record: refresh before editing")
                server_rev = row["revision"] if row else 0
                current = json.loads(row["payload_json"]) if row else {}
                if row and base != server_rev:
                    base_row = self._one("SELECT payload_json FROM revision_snapshots WHERE workspace_id=? AND "
                                         "resource_type=? AND resource_id=? AND revision=?", (wid, typ, rid, base))
                    if not base_row or op != "upsert":
                        raise ServiceError(409, "Conflict: revision changed, refresh and resolve")
                    old = json.loads(base_row["payload_json"])
                    changed_keys = {key for key in set(old) | set(current) if old.get(key) != current.get(key)}
                    if changed_keys.intersection(fields):
                        raise ServiceError(409, "Conflict: a field was edited on another device")
                if row and row["deleted"] and op != "upsert":
                    raise ServiceError(409, "Record is already deleted")
                next_rev = server_rev + 1
                updated = {**current, **fields} if op == "upsert" else current
                if not row:
                    self._execute("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?)",
                                  (wid, typ, rid, uid, next_rev, visibility, json.dumps(selected), int(op == "delete"),
                                   json.dumps(updated), self.clock()))
                else:
                    self._execute("UPDATE resources SET revision=?, visibility=?, selected_users=?, deleted=?, "
                                  "payload_json=?, modified_at=? WHERE workspace_id=? AND resource_type=? AND resource_id=?",
                                  (next_rev, visibility, json.dumps(selected), int(op == "delete"), json.dumps(updated),
                                   self.clock(), wid, typ, rid))
                self._execute("INSERT INTO revision_snapshots VALUES (?,?,?,?,?)",
                              (wid, typ, rid, next_rev, json.dumps(updated)))
                self._execute("INSERT INTO change_log (workspace_id,resource_type,resource_id,actor_id,revision,changed_at) "
                              "VALUES (?,?,?,?,?,?)", (wid, typ, rid, uid, next_rev, self.clock()))
                result = {"resource_type": typ, "resource_id": rid, "revision": next_rev,
                          "operation": op, "payload": updated}
                self._execute("INSERT INTO applied_ops VALUES (?,?,?,?)", (op_id, uid, wid, json.dumps(result)))
                self.db.execute("COMMIT")
                return result
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise


class CollabServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: CollaborationDB) -> None:
        super().__init__(address, CollabHandler)
        self.store = store
        self.rate_lock = threading.Lock()
        self.rate: dict[tuple[str, str], tuple[int, int]] = {}

    def limited(self, address: str, action: str, max_count: int) -> bool:
        now = _now()
        with self.rate_lock:
            count, window = self.rate.get((address, action), (0, now))
            if now - window >= 60:
                count, window = 0, now
            count += 1
            self.rate[(address, action)] = (count, window)
            # Purge old entries to keep unauthenticated-address tracking bounded.
            if len(self.rate) > 20000:
                self.rate = {k: v for k, v in self.rate.items() if now - v[1] < 60}
            return count > max_count


class CollabHandler(BaseHTTPRequestHandler):
    server: CollabServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Never log Authorization headers or JSON request bodies.
        super().log_message(format, *args)

    def _response(self, code: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.end_headers()
        self.wfile.write(raw)

    def _input(self) -> dict[str, Any]:
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise ServiceError(415, "Expected application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ServiceError(400, "Invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY:
            raise ServiceError(413, "Request is too large")
        try:
            return _as_object(json.loads(self.rfile.read(length).decode("utf-8")))
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise ServiceError(400, "Invalid JSON") from exc

    def _auth(self) -> tuple[str, str]:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise ServiceError(401, "Authentication required")
        token = header[7:]
        if not 10 < len(token) < 300:
            raise ServiceError(401, "Authentication required")
        return self.server.store.whoami(token)["user_id"], token

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.strip("/").split("/")
            if self.server.limited(self.client_address[0], "all", 200):
                raise ServiceError(429, "Too many requests")
            if path[:1] != ["v1"]:
                raise ServiceError(404, "Not found")
            parts = path[1:]
            data = self._input() if method in ("POST", "PATCH") else {}
            db = self.server.store
            if method == "POST" and parts == ["register"]:
                if self.server.limited(self.client_address[0], "auth", 12):
                    raise ServiceError(429, "Too many authentication attempts")
                result = db.register(data.get("username"), data.get("password"))
                code = 201
            elif method == "POST" and parts == ["login"]:
                if self.server.limited(self.client_address[0], "auth", 12):
                    raise ServiceError(429, "Too many authentication attempts")
                result = db.login(data.get("username"), data.get("password"))
                code = 200
            else:
                uid, bearer = self._auth()
                code = 200
                if method == "GET" and parts == ["me"]:
                    result = db.whoami(bearer)
                elif method == "POST" and parts == ["logout"]:
                    db.signout(bearer)
                    result = {"signed_out": True}
                elif method == "GET" and parts == ["workspaces"]:
                    result = db.memberships(uid)
                elif method == "POST" and parts == ["workspaces"]:
                    result = db.create_workspace(uid, data.get("id"), data.get("name"))
                    code = 201
                elif method == "GET" and parts == ["invitations"]:
                    result = db.invitations(uid)
                elif method == "POST" and len(parts) == 3 and parts[0] == "invitations" and parts[2] in ("accept", "reject"):
                    result = db.decide_invitation(uid, parts[1], parts[2] == "accept")
                elif len(parts) >= 2 and parts[0] == "workspaces":
                    wid = parts[1]
                    if method == "GET" and len(parts) == 3 and parts[2] == "members":
                        result = db.members(uid, wid)
                    elif method == "POST" and len(parts) == 3 and parts[2] == "invitations":
                        result = db.invite(uid, wid, data.get("username"), data.get("role", "viewer"))
                        code = 201
                    elif method == "PATCH" and len(parts) == 4 and parts[2] == "members":
                        result = db.change_role(uid, wid, parts[3], data.get("role"))
                    elif method == "GET" and len(parts) == 3 and parts[2] == "changes":
                        query = parse_qs(parsed.query)
                        try:
                            cursor = int(query.get("cursor", ["0"])[0])
                            limit = int(query.get("limit", ["300"])[0])
                        except ValueError as exc:
                            raise ServiceError(400, "Invalid cursor") from exc
                        result = db.read_changes(uid, wid, cursor, limit)
                    elif method == "POST" and len(parts) == 3 and parts[2] == "changes":
                        result = db.write_change(uid, wid, data)
                        code = 201
                    else:
                        raise ServiceError(404, "Not found")
                else:
                    raise ServiceError(404, "Not found")
            self._response(code, {"ok": True, "data": result})
        except ServiceError as exc:
            self._response(exc.status, {"ok": False, "error": {"message": exc.message}})
        except (sqlite3.DatabaseError, OSError, ValueError, TypeError, KeyError):
            self._response(500, {"ok": False, "error": {"message": "Internal service error"}})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPlanner secure remote collaboration API")
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9443)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--loopback-dev", action="store_true", help="Explicit local-only HTTP testing; never exposes remote HTTP")
    args = parser.parse_args(argv)
    if not args.loopback_dev and not (args.cert and args.key):
        parser.error("Remote collaboration requires a TLS certificate and private key")
    if args.loopback_dev and args.host not in ("127.0.0.1", "::1", "localhost"):
        parser.error("--loopback-dev cannot bind to non-loopback interfaces")
    db = CollaborationDB(args.db)
    server = CollabServer((args.host, args.port), db)
    if not args.loopback_dev:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
