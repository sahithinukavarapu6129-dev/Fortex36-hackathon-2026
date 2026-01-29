from __future__ import annotations

"""
Path and filesystem safety utilities.

These helpers are defensive and cross-platform (Windows/macOS) focused.
"""

import os
import re
from pathlib import Path


WINDOWS_INVALID_FILENAME_CHARS = r'<>:"/\\|?*'
WINDOWS_INVALID_FILENAME_RE = re.compile(f"[{re.escape(WINDOWS_INVALID_FILENAME_CHARS)}]")


def get_default_downloads_dir() -> Path:
    home = Path(os.environ.get("USERPROFILE") or Path.home()).expanduser()
    downloads = home / "Downloads"
    return downloads


def resolve_path_safely(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except Exception:
        return path.expanduser().absolute()


def is_within_directory(path: Path, base_dir: Path) -> bool:
    try:
        path_resolved = resolve_path_safely(path)
        base_resolved = resolve_path_safely(base_dir)
        return base_resolved == path_resolved or base_resolved in path_resolved.parents
    except Exception:
        return False


def sanitize_filename(filename: str) -> str:
    cleaned = filename.strip().replace("\u0000", "")
    cleaned = WINDOWS_INVALID_FILENAME_RE.sub("_", cleaned)
    cleaned = cleaned.rstrip(". ")
    if not cleaned:
        return "untitled"
    return cleaned


def is_temporary_file(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith("~$"):
        return True
    if name in {".ds_store", "thumbs.db"}:
        return True

    tmp_suffixes = {
        ".tmp",
        ".part",
        ".crdownload",
        ".download",
        ".partial",
    }
    if path.suffix.lower() in tmp_suffixes:
        return True

    if name.endswith(".tmp") or name.endswith(".crdownload") or name.endswith(".part"):
        return True

    return False


def ensure_directory(dir_path: Path) -> bool:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def generate_non_overwriting_path(dest_path: Path) -> Path:
    if not dest_path.exists():
        return dest_path

    parent = dest_path.parent
    stem = dest_path.stem
    suffix = dest_path.suffix
    for i in range(1, 10_000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate

    return parent / f"{stem} ({os.getpid()}){suffix}"


def validate_destination_path(dest_path: Path, allowed_roots: list[Path]) -> bool:
    dest_resolved = resolve_path_safely(dest_path)
    for root in allowed_roots:
        if is_within_directory(dest_resolved, root):
            return True
    return False
