"""Temporary, bounded playback diagnostics; file IO never runs in frame callbacks."""
from __future__ import annotations

from datetime import datetime, timezone
import itertools
import json
from logging import Formatter, LogRecord
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import queue
import threading
import time


class _TraceHandler(RotatingFileHandler):
    def handleError(self, record) -> None:
        raise OSError("Playback diagnostic log write failed")


class PlayerTrace:
    def __init__(self, directory: Path) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        self.path = directory / f"player-debug-{stamp}-{os.getpid()}.jsonl"
        self.enabled = os.environ.get("SEVENTH_PLAYER_TRACE", "0") == "1"
        self.error = ""
        self.lost_records = 0
        self._records: queue.Queue[dict] = queue.Queue(maxsize=2048)
        self._stop = threading.Event()
        self._sequence = itertools.count(1)
        self._worker: threading.Thread | None = None
        if not self.enabled:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handler = _TraceHandler(self.path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
            handler.setFormatter(Formatter("%(message)s"))
        except OSError as exc:
            self.enabled = False
            self.error = str(exc)
            return
        self._worker = threading.Thread(target=self._write, args=(handler,), daemon=True, name="player-trace")
        self._worker.start()
        self.record("session", utc=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
                    platform=platform.platform(), python=platform.python_version(),
                    note="painted means QWidget paint completed, not physical monitor presentation")

    def record(self, event: str, **fields) -> None:
        if not self.enabled or self._stop.is_set():
            return
        record = {"event": event, "seq": next(self._sequence), "mono_ns": time.perf_counter_ns(), **fields}
        try:
            self._records.put_nowait(record)
        except queue.Full:
            self.lost_records += 1

    def _write(self, handler: RotatingFileHandler) -> None:
        try:
            while not self._stop.is_set() or not self._records.empty():
                try:
                    fields = self._records.get(timeout=.2)
                except queue.Empty:
                    continue
                message = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
                handler.emit(LogRecord("player", 20, "", 0, message, (), None))
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            self.enabled = False
        finally:
            handler.close()

    def close(self) -> None:
        if self._stop.is_set():
            return
        self.record("trace_close", lost_records=self.lost_records)
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=2)
