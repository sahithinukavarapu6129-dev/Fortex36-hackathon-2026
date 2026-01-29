from __future__ import annotations

"""
Conservative screenshot/image analysis utilities.

- Perceptual hashing uses imagehash + PIL (optional dependency).
- OCR uses pytesseract + PIL (optional dependency).
If dependencies are missing or analysis fails, this module returns safe defaults.
"""

from dataclasses import dataclass
from pathlib import Path


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp"}


@dataclass(frozen=True)
class ScreenshotAnalysis:
    is_image: bool
    perceptual_hash: str | None
    is_duplicate: bool
    duplicate_of_hash: str | None
    hamming_distance: int | None
    ocr_text_excerpt: str | None


def analyze_screenshot(
    file_path: Path,
    prior_hashes: list[str],
    *,
    max_hamming_distance: int = 2,
    enable_ocr: bool = False,
) -> ScreenshotAnalysis:
    if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return ScreenshotAnalysis(
            is_image=False,
            perceptual_hash=None,
            is_duplicate=False,
            duplicate_of_hash=None,
            hamming_distance=None,
            ocr_text_excerpt=None,
        )

    p_hash = _compute_perceptual_hash(file_path)
    if not p_hash:
        return ScreenshotAnalysis(
            is_image=True,
            perceptual_hash=None,
            is_duplicate=False,
            duplicate_of_hash=None,
            hamming_distance=None,
            ocr_text_excerpt=None,
        )

    dup_hash, dist = _find_near_duplicate(p_hash, prior_hashes, max_hamming_distance=max_hamming_distance)
    excerpt = _extract_ocr_excerpt(file_path) if enable_ocr else None

    return ScreenshotAnalysis(
        is_image=True,
        perceptual_hash=p_hash,
        is_duplicate=dup_hash is not None,
        duplicate_of_hash=dup_hash,
        hamming_distance=dist,
        ocr_text_excerpt=excerpt,
    )


def _compute_perceptual_hash(file_path: Path) -> str | None:
    try:
        from PIL import Image  # type: ignore
        import imagehash  # type: ignore
    except Exception:
        return None

    try:
        with Image.open(file_path) as img:
            img_hash = imagehash.phash(img)
            return str(img_hash)
    except Exception:
        return None


def _find_near_duplicate(
    new_hash: str,
    prior_hashes: list[str],
    *,
    max_hamming_distance: int,
) -> tuple[str | None, int | None]:
    try:
        import imagehash  # type: ignore
    except Exception:
        return None, None

    try:
        new = imagehash.hex_to_hash(new_hash)
    except Exception:
        return None, None

    best: tuple[str, int] | None = None
    for prev in prior_hashes:
        try:
            old = imagehash.hex_to_hash(prev)
            dist = int(new - old)
        except Exception:
            continue
        if dist <= max_hamming_distance:
            if best is None or dist < best[1]:
                best = (prev, dist)
                if dist == 0:
                    break

    if best is None:
        return None, None
    return best[0], best[1]


def _extract_ocr_excerpt(file_path: Path) -> str | None:
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore
    except Exception:
        return None

    try:
        with Image.open(file_path) as img:
            text = pytesseract.image_to_string(img)
        text = " ".join((text or "").split())
        if not text:
            return None
        return text[:200]
    except Exception:
        return None
