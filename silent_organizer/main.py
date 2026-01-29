from __future__ import annotations

"""
Silent Organizer entrypoint.

Starts the file watcher and the localhost-only API server in the background.
"""

import os
import signal
import threading
import time
from pathlib import Path

from silent_organizer.api.app import create_app
from silent_organizer.engine.organizer import Organizer
from silent_organizer.utils.path_utils import get_default_downloads_dir, resolve_path_safely
from silent_organizer.watcher.file_watcher import FileWatcher


def run() -> None:
    downloads_dir = resolve_path_safely(Path(os.environ.get("SO_DOWNLOADS_DIR") or get_default_downloads_dir()))
    rules_path_env = os.environ.get("SO_RULES_PATH")
    log_path_env = os.environ.get("SO_LOG_PATH")
    api_port = int(os.environ.get("SO_API_PORT") or "8731")
    api_log_level = str(os.environ.get("SO_UVICORN_LOG_LEVEL") or "warning").strip().lower()

    organizer = Organizer(
        downloads_dir=downloads_dir,
        rules_path=Path(rules_path_env) if rules_path_env else None,
        log_path=Path(log_path_env) if log_path_env else None,
    )

    watcher = FileWatcher(downloads_dir, organizer.handle_stable_file)
    watcher.start()

    stop_event = threading.Event()

    def _shutdown(*_args) -> None:  # type: ignore[no-untyped-def]
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)
    except Exception:
        pass

    api_thread = threading.Thread(
        target=_run_api_server,
        args=(organizer, watcher, api_port, api_log_level),
        name="silent-organizer-api",
        daemon=True,
    )
    api_thread.start()

    try:
        while not stop_event.is_set():
            time.sleep(0.5)
    finally:
        try:
            watcher.stop()
        except Exception:
            pass


def _run_api_server(organizer: Organizer, watcher: FileWatcher, port: int, log_level: str) -> None:
    try:
        import uvicorn  # type: ignore
    except Exception:
        organizer.log.log_error("api", "uvicorn is not installed; API server not started.")
        return

    app = create_app(organizer, watcher=watcher)
    try:
        uvicorn.run(app, host="127.0.0.1", port=int(port), log_level=log_level)
    except Exception as ex:
        organizer.log.log_error("api", f"{type(ex).__name__}: {ex}")


if __name__ == "__main__":
    run()
