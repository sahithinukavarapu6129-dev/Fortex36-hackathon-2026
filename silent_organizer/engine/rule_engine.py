"""
Rule engine for deciding destination categories based on human-editable JSON rules.

This module is conservative: it only decides destinations and returns reasons/confidence.
It does not perform any file system side-effects.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from silent_organizer.utils.path_utils import resolve_path_safely


@dataclass(frozen=True)
class RuleDecision:
    category_name: str
    destination_relative: str
    confidence: float
    reasons: list[str]


class RuleEngine:
    def __init__(self, rules_path: Path):
        self.rules_path = rules_path
        self.rules: dict[str, Any] = {}

    def load(self) -> None:
        rules_path = resolve_path_safely(self.rules_path)
        try:
            raw = rules_path.read_text(encoding="utf-8")
            self.rules = json.loads(raw)
        except Exception:
            self.rules = {
                "version": 1,
                "education_mode": True,
                "base_destination": str(Path.home() / "Downloads" / "SilentOrganizer"),
                "rename": {"enabled": False, "confidence_threshold": 0.9},
                "duplicate_detection": {
                    "enabled": False,
                    "max_hamming_distance": 2,
                    "duplicates_destination": "Duplicates",
                },
                "categories": [],
                "fallback_destination": "Misc",
            }

    def get_base_destination(self) -> Path:
        base = self.rules.get("base_destination") or str(Path.home() / "Downloads" / "SilentOrganizer")
        return resolve_path_safely(Path(base))

    def rename_settings(self) -> dict[str, Any]:
        rename = self.rules.get("rename") or {}
        if not isinstance(rename, dict):
            return {"enabled": False, "confidence_threshold": 0.9}
        return {
            "enabled": bool(rename.get("enabled", False)),
            "confidence_threshold": float(rename.get("confidence_threshold", 0.9)),
        }

    def duplicate_settings(self) -> dict[str, Any]:
        dup = self.rules.get("duplicate_detection") or {}
        if not isinstance(dup, dict):
            return {"enabled": False, "max_hamming_distance": 2, "duplicates_destination": "Duplicates"}
        return {
            "enabled": bool(dup.get("enabled", False)),
            "max_hamming_distance": int(dup.get("max_hamming_distance", 2)),
            "duplicates_destination": str(dup.get("duplicates_destination", "Duplicates")),
        }

    def decide(self, file_path: Path) -> RuleDecision:
        filename = file_path.name.lower()
        stem = file_path.stem.lower()
        ext = file_path.suffix.lower()

        education_mode = bool(self.rules.get("education_mode", True))
        categories = self.rules.get("categories") or []
        if not isinstance(categories, list):
            categories = []

        best_score = 0.0
        best: dict[str, Any] | None = None
        best_reasons: list[str] = []

        tokens = _tokenize(stem)
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            cat_name = str(cat.get("name", "Unknown"))
            destination = str(cat.get("destination") or "")
            keywords = cat.get("keywords") or []
            extensions = cat.get("extensions") or []

            score = 0.0
            reasons: list[str] = []

            if isinstance(extensions, list) and ext and ext in {str(e).lower() for e in extensions}:
                score += 0.35
                reasons.append(f"extension:{ext}")

            keyword_hits = 0
            if isinstance(keywords, list):
                for kw in keywords:
                    kw_norm = str(kw).strip().lower()
                    if not kw_norm:
                        continue
                    if kw_norm in filename:
                        keyword_hits += 1
                        reasons.append(f"keyword:{kw_norm}")

            if keyword_hits:
                score += min(0.55, 0.2 + keyword_hits * 0.12)

            if education_mode and any(k in {"lecture", "assignment", "exam", "quiz", "midterm", "final"} for k in tokens):
                score += 0.08
                reasons.append("education_mode_boost")

            if destination and score > best_score:
                best_score = score
                best = {"name": cat_name, "destination": destination}
                best_reasons = reasons

        if best and best_score >= 0.35:
            return RuleDecision(
                category_name=best["name"],
                destination_relative=best["destination"],
                confidence=min(0.99, max(0.0, best_score)),
                reasons=best_reasons,
            )

        fallback = str(self.rules.get("fallback_destination") or "Misc")
        return RuleDecision(
            category_name="Fallback",
            destination_relative=fallback,
            confidence=0.25,
            reasons=["fallback"],
        )


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text or "")]
