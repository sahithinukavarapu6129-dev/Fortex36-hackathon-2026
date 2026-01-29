"""
Conservative rename suggestion engine.

Outputs rename suggestions and confidence scores. It never applies renames directly and
never suggests overwriting an existing file.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from silent_organizer.utils.path_utils import sanitize_filename


@dataclass(frozen=True)
class RenameSuggestion:
    suggested_name: str
    confidence: float
    reasons: list[str]


DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(20\d{2})[-_\.](\d{2})[-_\.](\d{2})\b"), "%Y-%m-%d"),
    (re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b"), "%Y%m%d"),
    (re.compile(r"\b(\d{2})[-_\.](\d{2})[-_\.](20\d{2})\b"), "%d-%m-%Y"),
]


class RenameEngine:
    def suggest(self, file_path: Path, category_name: str | None = None) -> RenameSuggestion | None:
        stem = file_path.stem
        suffix = file_path.suffix
        reasons: list[str] = []

        date = _extract_date(stem)
        if date:
            reasons.append("date_from_name")
        else:
            try:
                date = datetime.fromtimestamp(file_path.stat().st_mtime).date()
                reasons.append("date_from_mtime")
            except Exception:
                date = None

        title = _extract_title(stem)
        if title:
            reasons.append("title_tokens")
        else:
            title = stem.strip()

        parts: list[str] = []
        confidence = 0.55
        if date:
            parts.append(date.strftime("%Y-%m-%d"))
            confidence += 0.18

        if category_name and category_name not in {"Fallback", "Unknown"}:
            parts.append(category_name)
            confidence += 0.07

        if title:
            parts.append(title)
            confidence += 0.15

        candidate = " - ".join(p for p in parts if p).strip()
        if not candidate:
            return None

        suggested = sanitize_filename(candidate) + suffix

        if suggested == file_path.name:
            return None

        if len(reasons) >= 2:
            confidence += 0.07

        confidence = min(0.95, max(0.0, confidence))
        return RenameSuggestion(suggested_name=suggested, confidence=confidence, reasons=reasons)


TITLE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _extract_title(stem: str) -> str:
    raw_tokens = TITLE_TOKEN_RE.findall(stem or "")
    tokens = [t for t in raw_tokens if len(t) >= 2]
    if not tokens:
        return ""
    stop = {"final", "midterm", "exam", "quiz", "lecture", "assignment", "homework", "slides", "notes"}
    filtered = [t for t in tokens if t.lower() not in stop]
    chosen = filtered[:8] if filtered else tokens[:8]
    return " ".join(chosen).strip()


def _extract_date(text: str) -> date | None:
    for pattern, fmt in DATE_PATTERNS:
        m = pattern.search(text or "")
        if not m:
            continue
        try:
            value = m.group(0)
            return datetime.strptime(value, fmt).date()
        except Exception:
            continue
    return None
