"""Verified native SLDC -> preview -> approved SPlanner records.

Uses only SLAI's canonical src.utils.sldc reader. Parsing is deterministic,
conservative, inspectable and works without a model or external provider.
Uncertain dates remain unassigned until the person confirms them.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..tools import slai, validation as v
from ..tools.errors import BadRequest, Conflict, NotFound, LimitExceeded
from ..tools.storage import atomic_write
from .workspace_store import WorkspaceStore

MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_PROPOSALS = 300
ISO_DATE = re.compile(r"(?<!\d)(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])(?!\d)")
CLOCK = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
TASK_MARKER = re.compile(r"^(?:\s*[-*+]\s*)?(?:\[[ xX]\]\s*|(?:to\s*do|todo|action(?:\s*item)?|task|follow[- ]up)\s*[:\-]\s*)", re.I)
EVENT_WORD = re.compile(r"\b(meeting|appointment|presentation|lecture|class|workshop|webinar|review session|call)\b", re.I)
ACTION_WORD = re.compile(r"^(?:please\s+)?(submit|finish|complete|review|prepare|send|draft|write|update|check|contact|deliver|schedule|follow up|create|implement)\b", re.I)
AMBIGUOUS_DATE = re.compile(r"\b(today|tomorrow|yesterday|next\s+\w+|this\s+\w+|on\s+(?:mon|tue|wed|thu|fri|sat|sun)\w*|friday|monday)\b", re.I)


def _utc(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _parse_date_time(text: str, *, zone: str) -> tuple[str | None, str | None, list[str]]:
    """Return a timestamp only for unambiguous YYYY-MM-DD (with optional HH:MM).

    Date-only task deadlines mean end of that local calendar day. Events do not
    invent a meeting start time: their start remains absent without a clock.
    """
    warnings: list[str] = []
    dates = ISO_DATE.findall(text)
    if not dates:
        if AMBIGUOUS_DATE.search(text):
            warnings.append("Relative or ambiguous date: enter a precise date before importing.")
        return None, None, warnings
    if len(set(dates)) > 1:
        return None, None, ["Multiple dates were found: choose the intended date."]
    y, m, d = dates[0]
    try:
        local_date = datetime(int(y), int(m), int(d))
    except ValueError:
        return None, None, ["The date in this section is invalid."]
    matches = CLOCK.findall(text)
    tz = ZoneInfo(zone)
    if not matches:
        end_of_day = local_date.replace(hour=23, minute=59, tzinfo=tz)
        return None, _utc(end_of_day), warnings
    hh, mm = map(int, matches[0])
    local_start = local_date.replace(hour=hh, minute=mm, tzinfo=tz)
    # An unambiguous UTC mapping must round-trip through the named zone.
    utc = local_start.astimezone(timezone.utc)
    if utc.astimezone(tz).replace(tzinfo=None) != local_start.replace(tzinfo=None):
        return None, None, ["This wall-clock time does not exist in the specified time zone."]
    if local_start.replace(fold=0).utcoffset() != local_start.replace(fold=1).utcoffset():
        return None, None, ["This time falls in a daylight-saving overlap; confirm its offset."]
    start = _utc(local_start)
    if len(matches) > 1:
        h2, m2 = map(int, matches[1])
        local_end = local_date.replace(hour=h2, minute=m2, tzinfo=tz)
        if local_end <= local_start:
            return start, None, ["End time is not later than start; correct it in the preview."]
        return start, _utc(local_end), warnings
    return start, None, warnings


def _suggestions(package: Any, *, zone: str) -> list[dict[str, Any]]:
    sldc = slai.sldc()
    current_heading = package.document.get("title", "Document")
    proposals: list[dict[str, Any]] = []
    note_lines: list[str] = []
    for index, block in enumerate(package.document.get("blocks", [])):
        block_type = block.get("type", "")
        content = sldc.block_text(block).strip()
        if not content:
            continue
        if block_type == "heading":
            current_heading = content
            note_lines.append(content)
            continue
        lines = content.splitlines()
        for j, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line:
                continue
            cleaned = TASK_MARKER.sub("", line).strip()
            is_task = bool(TASK_MARKER.match(line) or ACTION_WORD.match(cleaned))
            is_event = bool(EVENT_WORD.search(line) and ISO_DATE.search(line))
            if is_event:
                kind = "event"
            elif is_task:
                kind = "task"
            else:
                note_lines.append(line)
                continue
            start, date_or_end, uncertainty = _parse_date_time(line, zone=zone)
            value: dict[str, Any] = {"type": kind, "title": cleaned[:180], "description": line[:5000],
                                     "source_section": current_heading, "source_block": str(block.get("id", index)),
                                     "confidence": 0.95 if not uncertainty else 0.5, "uncertainty": uncertainty}
            if kind == "task":
                value.update({"priority": "normal", "due_at": start or date_or_end})
            else:
                if not start:
                    value["uncertainty"].append("Event start time is missing. Enter it before committing.")
                value.update({"start_at": start, "end_at": date_or_end if start else None,
                              "time_zone": zone, "location": "", "remind_before": None})
                if start and not value["end_at"]:
                    value["uncertainty"].append("Event end time is missing. Enter it before committing.")
            value["key"] = hashlib.sha256(f"{block.get('id', index)}:{j}:{kind}:{line}".encode()).hexdigest()[:32]
            proposals.append(value)
            if len(proposals) >= MAX_PROPOSALS:
                break
        if len(proposals) >= MAX_PROPOSALS:
            break
    # Source files are always preserved. A note proposal keeps the full original
    # document and all advanced formatting, tables, figures and embedded assets.
    proposals.append({"key": "source-document", "type": "note", "title": package.document.get("title", "Imported document")[:180],
                      "description": "\n".join(note_lines)[:5000], "source_section": "Entire source document",
                      "source_block": None, "confidence": 1.0, "uncertainty": [], "preserve_original": True})
    return proposals


class DocumentImporter:
    def __init__(self, store: WorkspaceStore, *, max_bytes: int = MAX_SOURCE_BYTES) -> None:
        self.store = store
        self.max_bytes = max_bytes
        self.archive_dir = store.data_dir / "imports"
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def stage(self, filename: str, encoded: str, *, zone: str = "UTC") -> dict[str, Any]:
        if not isinstance(filename, str) or not filename.lower().endswith(".sldc") or not 0 < len(filename) <= 255:
            raise BadRequest("Choose a native .sldc document.")
        if not isinstance(encoded, str) or len(encoded) > (self.max_bytes * 4 // 3 + 16):
            raise LimitExceeded("Document exceeds the configured import limit.")
        try:
            blob = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BadRequest("Document data is not valid Base64.") from exc
        if len(blob) > self.max_bytes:
            raise LimitExceeded("Document exceeds the configured import limit.")
        try:
            ZoneInfo(zone)
        except (ValueError, KeyError) as exc:
            raise BadRequest("Unknown time zone.") from exc
        module = slai.sldc()
        try:
            package = module.loads_package(blob)
            module.validate_sldc(package)
        except (ValueError, OSError, TypeError) as exc:
            raise BadRequest("Invalid or unreadable SLDC document.", hint=str(exc)[:200]) from exc
        title = package.document.get("title") or Path(filename).stem
        sha = hashlib.sha256(blob).hexdigest()
        proposals = _suggestions(package, zone=zone)
        if len(proposals) > MAX_PROPOSALS + 1:
            raise LimitExceeded("Document has too many potential planning items.")
        workspace_id = self.store.current_workspace_id
        existing = self.store.db.rows("SELECT proposal_key FROM document_import_items WHERE sha256=? AND workspace_id=?",
                                      (sha, workspace_id))
        done = {row["proposal_key"] for row in existing}
        for proposal in proposals:
            proposal["already_imported"] = proposal["key"] in done
        import_id = v.new_id()
        # Atomic write preserves the *exact* source package, never an extracted approximation.
        raw_path = self.archive_dir / f"{sha}.sldc"
        if not raw_path.exists():
            atomic_write(raw_path, blob)
        now = v.utc_now()
        self.store.db.execute("INSERT INTO document_imports VALUES (?,?,?,?,?,?,?,?,?)",
                              (import_id, sha, filename, str(title)[:180], workspace_id,
                               json.dumps(proposals, ensure_ascii=False), "preview", now, None))
        return self.preview(import_id)

    def preview(self, import_id: str) -> dict[str, Any]:
        row = self.store.db.row("SELECT * FROM document_imports WHERE id=? AND workspace_id=?",
                                (v.record_id(import_id), self.store.current_workspace_id))
        if row is None:
            raise NotFound("Import preview not found in this workspace.")
        return {"id": row["id"], "filename": row["filename"], "title": row["document_title"],
                "sha256": row["sha256"], "state": row["state"], "created_at": row["created_at"],
                "proposals": json.loads(row["proposals_json"])}

    def history(self) -> list[dict[str, Any]]:
        rows = self.store.db.rows("SELECT id,filename,document_title,sha256,state,created_at,committed_at "
                                  "FROM document_imports WHERE workspace_id=? ORDER BY created_at DESC LIMIT 100",
                                  (self.store.current_workspace_id,))
        return list(rows)

    def cancel(self, import_id: str) -> dict[str, Any]:
        preview = self.preview(import_id)
        if preview["state"] != "preview":
            raise Conflict("Only uncommitted import previews can be cancelled.")
        self.store.db.execute("UPDATE document_imports SET state='cancelled' WHERE id=?", (import_id,))
        return {"cancelled": import_id}

    def commit(self, import_id: str, selected: list[dict[str, Any]]) -> dict[str, Any]:
        preview = self.preview(import_id)
        if preview["state"] != "preview":
            raise Conflict("This import has already been completed or cancelled.")
        if not isinstance(selected, list) or len(selected) > MAX_PROPOSALS + 1:
            raise BadRequest("Select a valid list of proposed items.")
        originals = {p["key"]: p for p in preview["proposals"]}
        used: set[str] = set()
        validated: list[tuple[str, dict[str, Any]]] = []
        for candidate in selected:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("key"), str):
                raise BadRequest("Every selected proposal needs its original key.")
            key = candidate["key"]
            if key not in originals or key in used:
                raise BadRequest("Unknown or repeated proposal key.")
            used.add(key)
            proposal = {**originals[key], **candidate}
            kind = proposal.get("type")
            if kind not in ("task", "event", "note"):
                raise BadRequest("A proposal must be a task, event or note.")
            title = v.text(proposal.get("title"), "title", 180)
            info = {"title": title}
            if kind == "task":
                info.update(description=v.text(proposal.get("description", ""), "description", 5000, empty=True, multiline=True),
                            priority=v.choice(proposal.get("priority", "normal"), ("low", "normal", "high"), "priority"),
                            due_at=v.instant(proposal.get("due_at"), "due_at", nullable=True))
            elif kind == "event":
                info.update(description=v.text(proposal.get("description", ""), "description", 5000, empty=True, multiline=True),
                            start_at=v.instant(proposal.get("start_at"), "start_at"),
                            end_at=v.instant(proposal.get("end_at"), "end_at"),
                            time_zone=v.zone(proposal.get("time_zone", "UTC")),
                            location=v.text(proposal.get("location", ""), "location", 200, empty=True),
                            remind_before=v.integer(proposal.get("remind_before"), "remind_before", 0, 10080, nullable=True))
                if info["end_at"] <= info["start_at"]:
                    raise BadRequest("An imported event needs an end time later than its start time.")
            else:
                info["content"] = str(proposal.get("description", ""))[:50000]
            if isinstance(candidate.get("already_imported"), bool) and candidate["already_imported"]:
                continue   # display-only metadata cannot force a duplicate.
            validated.append((key, {"type": kind, "values": info, "original": originals[key]}))
        created: list[dict[str, str]] = []
        created_note_files: list[str] = []
        source = (self.archive_dir / f"{preview['sha256']}.sldc").read_bytes()
        try:
            with self.store.db.transaction():
                for key, entry in validated:
                    seen = self.store.db.scalar("SELECT 1 FROM document_import_items WHERE workspace_id=? AND sha256=? AND proposal_key=?",
                                                (self.store.current_workspace_id, preview["sha256"], key))
                    if seen:
                        continue
                    kind, values = entry["type"], entry["values"]
                    if kind == "task":
                        row = self.store.create_task(values)
                    elif kind == "event":
                        row = self.store.create_event(values)
                    else:
                        row = self.store.create_note(values)
                        created_note_files.append(row["id"])
                        if entry["original"].get("preserve_original"):
                            atomic_write(self.store.note_dir / f"{row['id']}.sldc", source)
                            # Refresh note-derived indexes from the *canonical* original.
                            canonical_text = slai.sldc().extract_text(slai.sldc().loads_package(source))
                            excerpt = " ".join(canonical_text.split())[:160]
                            self.store.db.execute("UPDATE notes SET excerpt=?,search_text=? WHERE id=?",
                                                  (excerpt, f"{values['title']}\n{canonical_text}".casefold(), row["id"]))
                            # create_note queues its simple generated package; the source
                            # package is the one that should be synchronized instead.
                            self.store.db.execute("DELETE FROM sync_outbox WHERE workspace_id=? AND resource_type='note' AND resource_id=?",
                                                  (self.store.current_workspace_id, row["id"]))
                            self.store._enqueue("notes", row["id"])
                    self.store.db.execute("INSERT INTO document_import_items VALUES (?,?,?,?,?)",
                                          (self.store.current_workspace_id, preview["sha256"], key, kind, row["id"]))
                    created.append({"key": key, "type": kind, "id": row["id"]})
                self.store.db.execute("UPDATE document_imports SET state='committed',committed_at=? WHERE id=? AND state='preview'",
                                      (v.utc_now(), import_id))
        except BaseException:
            # SQLite rollback does not roll back external SLDC files.
            for note_id in created_note_files:
                (self.store.note_dir / f"{note_id}.sldc").unlink(missing_ok=True)
            raise
        return {"id": import_id, "created": created, "duplicates_skipped": len(validated) - len(created),
                "source_sha256": preview["sha256"]}
