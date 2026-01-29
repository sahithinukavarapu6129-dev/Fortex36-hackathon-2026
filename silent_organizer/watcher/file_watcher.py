from __future__ import annotations

"""
File watcher service for the Downloads directory.

Emits only stabilized file events (after size stops changing) and ignores common
temporary/incomplete download artifacts.
"""

import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from silent_organizer.utils.path_utils import is_temporary_file, resolve_path_safely


@dataclass(frozen=True)
class FileEvent:
    event_type: str
    src_path: str
    dest_path: str | None
    timestamp_utc: float


class FileWatcher:
    def __init__(
        self,
        downloads_dir: Path,
        on_stable_file: Callable[[Path, FileEvent], None],
        *,
        stability_seconds: float = 1.5,
        max_wait_seconds: float = 60.0,
        debounce_seconds: float = 1.0,
    ):
        self.downloads_dir = resolve_path_safely(downloads_dir)
        self.on_stable_file = on_stable_file
        self.stability_seconds = stability_seconds
        self.max_wait_seconds = max_wait_seconds
        self.debounce_seconds = debounce_seconds

        self._observer = None
        self._stop_event = threading.Event()
        self._queue: queue.Queue[tuple[Path, FileEvent]] = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, name="silent-organizer-watcher", daemon=True)
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        try:
            from watchdog.observers import Observer  # type: ignore
            from watchdog.events import FileSystemEventHandler  # type: ignore
        except Exception:
            return

        class Handler(FileSystemEventHandler):
            def __init__(self, outer: FileWatcher):
                self.outer = outer

            def on_created(self, event):  # type: ignore[no-untyped-def]
                if getattr(event, "is_directory", False):
                    return
                self.outer._enqueue_event("created", Path(str(getattr(event, "src_path", ""))), None)

            def on_moved(self, event):  # type: ignore[no-untyped-def]
                if getattr(event, "is_directory", False):
                    return
                src = Path(str(getattr(event, "src_path", "")))
                dest = str(getattr(event, "dest_path", "")) or None
                self.outer._enqueue_event("moved", src, dest)

            def on_modified(self, event):  # type: ignore[no-untyped-def]
                if getattr(event, "is_directory", False):
                    return
                self.outer._enqueue_event("modified", Path(str(getattr(event, "src_path", ""))), None)

        self._observer = Observer()
        self._observer.schedule(Handler(self), str(self.downloads_dir), recursive=False)
        self._observer.daemon = True
        self._observer.start()

        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            if self._observer:
                self._observer.stop()
                self._observer.join(timeout=5)
        except Exception:
            pass

    def is_running(self) -> bool:
        obs = self._observer
        try:
            return bool(obs and obs.is_alive())
        except Exception:
            return False

    def _enqueue_event(self, event_type: str, src_path: Path, dest_path: str | None) -> None:
        try:
            src_path = resolve_path_safely(src_path)
        except Exception:
            return

        if not src_path.name:
            return

        if is_temporary_file(src_path):
            return

        ts = time.time()
        key = str(src_path)
        with self._lock:
            last = self._last_seen.get(key, 0.0)
            if ts - last < self.debounce_seconds:
                self._last_seen[key] = ts
                return
            self._last_seen[key] = ts

        evt = FileEvent(event_type=event_type, src_path=str(src_path), dest_path=dest_path, timestamp_utc=ts)
        self._queue.put((src_path, evt))

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                src_path, evt = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                stable = self._wait_until_stable(src_path)
                if stable:
                    self.on_stable_file(src_path, evt)
            except Exception:
                pass
            finally:
                self._queue.task_done()

    def _wait_until_stable(self, path: Path) -> bool:
        start = time.time()
        last_size = None
        stable_since = None

        while time.time() - start < self.max_wait_seconds and not self._stop_event.is_set():
            try:
                if not path.exists():
                    time.sleep(0.25)
                    continue
                size = path.stat().st_size
            except Exception:
                time.sleep(0.25)
                continue

            now = time.time()
            if last_size is None or size != last_size:
                last_size = size
                stable_since = None
                time.sleep(0.35)
                continue

            if stable_since is None:
                stable_since = now

            if now - stable_since >= self.stability_seconds:
                return True

            time.sleep(0.25)

        return False
