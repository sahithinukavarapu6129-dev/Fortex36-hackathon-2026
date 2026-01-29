from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from silent_organizer.engine.organizer import Organizer


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="silent-organizer-debug-") as td:
        root = Path(td)
        downloads = root / "Downloads"
        downloads.mkdir(parents=True, exist_ok=True)

        base_dest = root / "SilentOrganizer"
        base_dest.mkdir(parents=True, exist_ok=True)

        rules = {
            "version": 1,
            "education_mode": True,
            "base_destination": str(base_dest),
            "rename": {"enabled": False, "confidence_threshold": 0.9},
            "duplicate_detection": {"enabled": False, "max_hamming_distance": 2, "duplicates_destination": "Duplicates"},
            "categories": [
                {
                    "name": "Lectures",
                    "destination": "Education/Lectures",
                    "keywords": ["lecture", "slides", "lesson"],
                    "extensions": [".pdf"],
                }
            ],
            "fallback_destination": "Misc",
        }
        rules_path = root / "rules.json"
        rules_path.write_text(json.dumps(rules, indent=2), encoding="utf-8")

        log_path = root / "activity_log.json"
        log_path.write_text("[]", encoding="utf-8")

        organizer = Organizer(downloads_dir=downloads, rules_path=rules_path, log_path=log_path)

        sample = downloads / "OS lecture 01.pdf"
        sample.write_bytes(b"%PDF-1.4\n%fake\n")

        result = organizer.organize_file(sample)
        if not result.ok:
            raise RuntimeError(f"organize_file failed: {result.message}")
        if not result.destination:
            raise RuntimeError("organize_file returned ok but no destination")

        dest = Path(result.destination)
        if not dest.exists():
            raise RuntimeError(f"destination missing: {dest}")
        if sample.exists():
            raise RuntimeError("source still exists after move")

        undo_result = organizer.undo()
        if not undo_result.get("ok"):
            raise RuntimeError(f"undo failed: {undo_result}")

        restored_to = Path(str(undo_result.get("restored_to")))
        if not restored_to.exists():
            raise RuntimeError(f"restore missing: {restored_to}")

        entries = json.loads(log_path.read_text(encoding="utf-8"))
        moves = [e for e in entries if e.get("action_type") == "move"]
        undos = [e for e in entries if e.get("action_type") == "undo"]
        if not moves or not undos:
            raise RuntimeError("expected move and undo entries in log")

        print("DEEP_DEBUG_OK")
        print(f"downloads_dir={downloads}")
        print(f"base_destination={base_dest}")
        print(f"moved_to={dest}")
        print(f"restored_to={restored_to}")
        print(f"log_entries={len(entries)}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
