from __future__ import annotations

"""
Organizer orchestrator and logging/undo layer.

This module is designed to be safe and reversible:
- Never overwrites destination files (unique naming + exclusive creation fallback).
- Logs planned actions before attempting filesystem changes.
- Supports undo for the last 24 hours (non-destructive; avoids overwrites).
"""

import json
import os
import shutil
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from silent_organizer.ai.screenshot_analyzer import analyze_screenshot
from silent_organizer.engine.rename_engine import RenameEngine
from silent_organizer.engine.rule_engine import RuleDecision, RuleEngine
from silent_organizer.utils.path_utils import (
    ensure_directory,
    generate_non_overwriting_path,
    get_default_downloads_dir,
    resolve_path_safely,
    sanitize_filename,
    validate_destination_path,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _project_log_path() -> Path:
    package_root = Path(__file__).resolve().parents[1]
    return package_root / "logs" / "activity_log.json"


class ActivityLog:
    def __init__(self, log_path: Path | None = None):
        self.log_path = resolve_path_safely(log_path or _project_log_path())
        self._lock = threading.Lock()
        ensure_directory(self.log_path.parent)

    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        entries = self._read_entries()
        if limit <= 0:
            return entries
        return entries[-limit:]

    def append_planned(self, entry: dict[str, Any]) -> str:
        entry_id = uuid.uuid4().hex
        now = _utc_now().isoformat()
        record = {
            "id": entry_id,
            "timestamp_utc": now,
            "status": "planned",
            **entry,
        }
        with self._lock:
            entries = self._read_entries_unlocked()
            entries.append(record)
            self._write_entries_unlocked(entries)
        return entry_id

    def append_completed(self, entry: dict[str, Any]) -> str:
        entry_id = uuid.uuid4().hex
        now = _utc_now().isoformat()
        record = {
            "id": entry_id,
            "timestamp_utc": now,
            "status": "completed",
            "completed_timestamp_utc": now,
            **entry,
        }
        with self._lock:
            entries = self._read_entries_unlocked()
            entries.append(record)
            self._write_entries_unlocked(entries)
        return entry_id

    def mark_completed(self, entry_id: str, updates: dict[str, Any] | None = None) -> None:
        updates = updates or {}
        with self._lock:
            entries = self._read_entries_unlocked()
            for e in reversed(entries):
                if e.get("id") == entry_id:
                    e["status"] = "completed"
                    e["completed_timestamp_utc"] = _utc_now().isoformat()
                    e.update(updates)
                    break
            self._write_entries_unlocked(entries)

    def mark_failed(self, entry_id: str, error: str) -> None:
        with self._lock:
            entries = self._read_entries_unlocked()
            for e in reversed(entries):
                if e.get("id") == entry_id:
                    e["status"] = "failed"
                    e["completed_timestamp_utc"] = _utc_now().isoformat()
                    e["error"] = error[:500]
                    break
            self._write_entries_unlocked(entries)

    def log_error(self, context: str, error: str, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"action_type": "error", "context": context, "message": error[:1000]}
        if extra:
            payload["extra"] = extra
        self.append_completed(payload)

    def recent_image_hashes(self, limit: int = 2000) -> list[str]:
        entries = self._read_entries()
        hashes: list[str] = []
        for e in reversed(entries):
            if len(hashes) >= limit:
                break
            if e.get("status") != "completed":
                continue
            if e.get("action_type") != "move":
                continue
            h = e.get("perceptual_hash")
            if isinstance(h, str) and h:
                hashes.append(h)
        return hashes

    def compute_insights(self) -> dict[str, Any]:
        entries = self._read_entries()
        now = _utc_now()
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

        moved_total = 0
        moved_today = 0
        file_type_counter: Counter[str] = Counter()
        skipped_total = 0
        errors_total = 0

        for e in entries:
            status = e.get("status")
            action_type = e.get("action_type")

            if action_type == "error":
                errors_total += 1
                continue

            if action_type == "skip":
                skipped_total += 1
                continue

            if action_type != "move" or status != "completed":
                continue

            moved_total += 1
            dest = str(e.get("destination") or "")
            ext = Path(dest).suffix.lower() if dest else ""
            if ext:
                file_type_counter[ext] += 1

            ts = _parse_utc_timestamp(e.get("timestamp_utc"))
            if ts and ts >= start_of_day:
                moved_today += 1

        time_saved_seconds = moved_total * 30
        total_events = moved_total + skipped_total
        clutter_reduction = (moved_total / total_events) * 100.0 if total_events else 0.0

        return {
            "files_organized": {"daily": moved_today, "total": moved_total},
            "estimated_time_saved_seconds": time_saved_seconds,
            "file_type_distribution": dict(file_type_counter.most_common(20)),
            "downloads_clutter_reduction_percent": round(clutter_reduction, 2),
            "errors_logged": errors_total,
        }

    def undo_last_24h(self, allowed_roots: list[Path]) -> dict[str, Any]:
        cutoff = _utc_now() - timedelta(hours=24)
        with self._lock:
            entries = self._read_entries_unlocked()

            target = None
            for e in reversed(entries):
                if e.get("status") != "completed":
                    continue
                if e.get("action_type") != "move":
                    continue
                if e.get("undone") is True:
                    continue
                ts = _parse_utc_timestamp(e.get("timestamp_utc"))
                if not ts or ts < cutoff:
                    continue
                if not isinstance(e.get("source"), str) or not isinstance(e.get("destination"), str):
                    continue
                target = e
                break

            if not target:
                return {"ok": False, "message": "No undoable actions in the last 24 hours."}

            src = resolve_path_safely(Path(target["source"]))
            dest = resolve_path_safely(Path(target["destination"]))
            if not dest.exists():
                undo_id = self.append_planned(
                    {
                        "action_type": "undo",
                        "status_note": "destination_missing",
                        "source": str(dest),
                        "destination": str(src),
                        "original_action_id": target.get("id"),
                    }
                )
                self.mark_failed(undo_id, "Destination file no longer exists.")
                return {"ok": False, "message": "Destination file missing; cannot undo.", "undo_id": undo_id}

            if not validate_destination_path(src, allowed_roots) or not validate_destination_path(dest, allowed_roots):
                undo_id = self.append_planned(
                    {
                        "action_type": "undo",
                        "status_note": "path_validation_failed",
                        "source": str(dest),
                        "destination": str(src),
                        "original_action_id": target.get("id"),
                    }
                )
                self.mark_failed(undo_id, "Path validation failed.")
                return {"ok": False, "message": "Undo blocked by path validation.", "undo_id": undo_id}

            restore_target = src
            if restore_target.exists():
                restore_target = generate_non_overwriting_path(restore_target)

            undo_id = self.append_planned(
                {
                    "action_type": "undo",
                    "source": str(dest),
                    "destination": str(restore_target),
                    "original_action_id": target.get("id"),
                }
            )

            try:
                ensure_directory(restore_target.parent)
                _safe_move_file(dest, restore_target)
            except Exception as ex:
                self.mark_failed(undo_id, f"{type(ex).__name__}: {ex}")
                return {"ok": False, "message": "Undo failed.", "undo_id": undo_id}

            self.mark_completed(undo_id)
            target["undone"] = True
            target["undo_action_id"] = undo_id
            self._write_entries_unlocked(entries)

            return {"ok": True, "message": "Undo completed.", "undo_id": undo_id, "restored_to": str(restore_target)}

    def _read_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_entries_unlocked()

    def _read_entries_unlocked(self) -> list[dict[str, Any]]:
        try:
            raw = self.log_path.read_text(encoding="utf-8")
            data = json.loads(raw or "[]")
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    def _write_entries_unlocked(self, entries: list[dict[str, Any]]) -> None:
        tmp_path = self.log_path.with_suffix(self.log_path.suffix + ".tmp")
        payload = json.dumps(entries, ensure_ascii=False, indent=2)
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(self.log_path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass(frozen=True)
class OrganizeResult:
    ok: bool
    action_id: str | None
    message: str
    destination: str | None


class Organizer:
    def __init__(
        self,
        *,
        downloads_dir: Path | None = None,
        rules_path: Path | None = None,
        log_path: Path | None = None,
    ):
        self.downloads_dir = resolve_path_safely(downloads_dir or get_default_downloads_dir())
        self.rules_path = resolve_path_safely(
            rules_path or (Path(__file__).resolve().parents[1] / "rules" / "rules.json")
        )

        self.rule_engine = RuleEngine(self.rules_path)
        self.rename_engine = RenameEngine()
        self.log = ActivityLog(log_path=log_path)

        self._last_event_utc: float | None = None

        self.reload_rules()

    def reload_rules(self) -> None:
        try:
            self.rule_engine.load()
        except Exception as ex:
            self.log.log_error("reload_rules", f"{type(ex).__name__}: {ex}")

    def status(self) -> dict[str, Any]:
        rules_version = self.rule_engine.rules.get("version") if isinstance(self.rule_engine.rules, dict) else None
        return {
            "downloads_dir": str(self.downloads_dir),
            "rules_path": str(self.rules_path),
            "rules_version": rules_version,
            "last_event_timestamp_utc": self._last_event_utc,
        }

    def handle_stable_file(self, file_path: Path, event: Any) -> None:
        self._last_event_utc = time.time()
        try:
            self.log.append_completed(
                {
                    "action_type": "event",
                    "event_type": getattr(event, "event_type", None) or "unknown",
                    "source": str(file_path),
                    "destination": getattr(event, "dest_path", None),
                }
            )
        except Exception:
            pass

        try:
            self.organize_file(file_path)
        except Exception as ex:
            self.log.log_error("organize_file", f"{type(ex).__name__}: {ex}", extra={"path": str(file_path)})

    def organize_file(self, file_path: Path) -> OrganizeResult:
        file_path = resolve_path_safely(file_path)

        if not file_path.exists():
            self.log.append_completed({"action_type": "skip", "reason": "missing", "source": str(file_path)})
            return OrganizeResult(ok=False, action_id=None, message="File missing.", destination=None)

        if file_path.is_dir():
            self.log.append_completed({"action_type": "skip", "reason": "directory", "source": str(file_path)})
            return OrganizeResult(ok=False, action_id=None, message="Ignored directory.", destination=None)

        decision = self._decide_destination(file_path)
        base_dest = self.rule_engine.get_base_destination()
        dup_settings = self.rule_engine.duplicate_settings()

        allowed_roots = [self.downloads_dir, resolve_path_safely(Path.home())]
        if not validate_destination_path(base_dest, allowed_roots):
            self.log.append_completed(
                {
                    "action_type": "skip",
                    "reason": "base_destination_not_allowed",
                    "source": str(file_path),
                    "destination": str(base_dest),
                }
            )
            return OrganizeResult(ok=False, action_id=None, message="Blocked destination.", destination=None)

        perceptual_hash = None
        is_duplicate = False
        duplicate_of_hash = None
        hamming_distance = None
        destination_relative = decision.destination_relative

        if dup_settings.get("enabled", False):
            try:
                prior_hashes = self.log.recent_image_hashes(limit=2500)
                analysis = analyze_screenshot(
                    file_path,
                    prior_hashes,
                    max_hamming_distance=int(dup_settings.get("max_hamming_distance", 2)),
                    enable_ocr=False,
                )
                perceptual_hash = analysis.perceptual_hash
                is_duplicate = bool(analysis.is_duplicate)
                duplicate_of_hash = analysis.duplicate_of_hash
                hamming_distance = analysis.hamming_distance
                if analysis.is_image and analysis.is_duplicate:
                    destination_relative = str(dup_settings.get("duplicates_destination") or "Duplicates")
            except Exception as ex:
                self.log.log_error("duplicate_detection", f"{type(ex).__name__}: {ex}", extra={"path": str(file_path)})

        rename_settings = self.rule_engine.rename_settings()
        dest_filename = sanitize_filename(file_path.name)
        rename_applied = False
        rename_confidence = None
        rename_reasons: list[str] = []

        if rename_settings.get("enabled", False):
            try:
                suggestion = self.rename_engine.suggest(file_path, category_name=decision.category_name)
                if suggestion and float(suggestion.confidence) >= float(rename_settings.get("confidence_threshold", 0.9)):
                    dest_filename = suggestion.suggested_name
                    rename_applied = True
                    rename_confidence = float(suggestion.confidence)
                    rename_reasons = list(suggestion.reasons)
            except Exception as ex:
                self.log.log_error("rename_suggestion", f"{type(ex).__name__}: {ex}", extra={"path": str(file_path)})

        dest_dir = resolve_path_safely(base_dest / destination_relative)
        if not ensure_directory(dest_dir):
            self.log.append_completed(
                {
                    "action_type": "skip",
                    "reason": "mkdir_failed",
                    "source": str(file_path),
                    "destination": str(dest_dir),
                }
            )
            return OrganizeResult(ok=False, action_id=None, message="Failed to create destination.", destination=None)

        dest_path = generate_non_overwriting_path(dest_dir / dest_filename)

        entry_id = self.log.append_planned(
            {
                "action_type": "move",
                "source": str(file_path),
                "destination": str(dest_path),
                "decision": _decision_to_dict(decision),
                "rename": {
                    "applied": rename_applied,
                    "confidence": rename_confidence,
                    "reasons": rename_reasons,
                },
                "duplicate": {
                    "is_duplicate": is_duplicate,
                    "duplicate_of_hash": duplicate_of_hash,
                    "hamming_distance": hamming_distance,
                },
                "perceptual_hash": perceptual_hash,
            }
        )

        try:
            if not file_path.exists():
                self.log.mark_failed(entry_id, "Source file disappeared before move.")
                return OrganizeResult(ok=False, action_id=entry_id, message="Source disappeared.", destination=None)

            if not validate_destination_path(dest_path, allowed_roots):
                self.log.mark_failed(entry_id, "Destination path validation failed.")
                return OrganizeResult(ok=False, action_id=entry_id, message="Blocked destination.", destination=None)

            _safe_move_file(file_path, dest_path)
        except Exception as ex:
            self.log.mark_failed(entry_id, f"{type(ex).__name__}: {ex}")
            return OrganizeResult(ok=False, action_id=entry_id, message="Move failed.", destination=None)

        self.log.mark_completed(entry_id)
        return OrganizeResult(ok=True, action_id=entry_id, message="Moved safely.", destination=str(dest_path))

    def insights(self) -> dict[str, Any]:
        return self.log.compute_insights()

    def logs(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.log.list(limit=limit)

    def undo(self) -> dict[str, Any]:
        allowed_roots = [self.downloads_dir, resolve_path_safely(Path.home())]
        try:
            return self.log.undo_last_24h(allowed_roots=allowed_roots)
        except Exception as ex:
            self.log.log_error("undo", f"{type(ex).__name__}: {ex}")
            return {"ok": False, "message": "Undo failed unexpectedly."}

    def _decide_destination(self, file_path: Path) -> RuleDecision:
        try:
            return self.rule_engine.decide(file_path)
        except Exception as ex:
            self.log.log_error("rule_decision", f"{type(ex).__name__}: {ex}", extra={"path": str(file_path)})
            return RuleDecision(category_name="Fallback", destination_relative="Misc", confidence=0.0, reasons=["error"])


def _decision_to_dict(decision: RuleDecision) -> dict[str, Any]:
    return {
        "category_name": decision.category_name,
        "destination_relative": decision.destination_relative,
        "confidence": decision.confidence,
        "reasons": decision.reasons,
    }


def _safe_move_file(src: Path, dest: Path) -> None:
    src = resolve_path_safely(src)
    dest = resolve_path_safely(dest)

    if dest.exists():
        raise FileExistsError(f"Destination already exists: {dest}")

    ensure_directory(dest.parent)

    try:
        os.link(str(src), str(dest))
        try:
            os.unlink(str(src))
        except Exception:
            try:
                os.unlink(str(dest))
            except Exception:
                pass
            raise
        return
    except Exception:
        pass

    with open(src, "rb") as rf:
        with open(dest, "xb") as wf:
            shutil.copyfileobj(rf, wf, length=1024 * 1024)
            try:
                wf.flush()
                os.fsync(wf.fileno())
            except Exception:
                pass

    try:
        shutil.copystat(src, dest, follow_symlinks=True)
    except Exception:
        pass

    os.unlink(str(src))
