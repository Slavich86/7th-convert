"""Bounded stdio adapter for the standalone Rust preview engine (no widgets)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from PySide6.QtCore import QObject, Signal

from .player_binary import compatible_player

MAX_FRAME_BYTES = 64 * 1024 * 1024


def player_executable() -> Path | None:
    override = os.environ.get("SEVENTH_PLAYER")
    name = "seventh-player.exe" if os.name == "nt" else "seventh-player"
    root = Path(__file__).resolve().parent
    candidates = [Path(override)] if override else [root / "bin" / name, root.parent / "rust" / "player" / "target" / "release" / name]
    return next((path for path in candidates if compatible_player(path)), None)


class RustPlayer(QObject):
    frameReady = Signal(object, object)
    stateChanged = Signal(str)
    failed = Signal(str)
    _event = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.generation = 0
        self.position_us = 0
        self.index = 0
        self.state = "paused"
        self.trace = None
        self.received_frames = 0
        self.stale_frames = 0
        self._closing = False
        self._process: subprocess.Popen | None = None
        self._writes: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
        self._threads: list[threading.Thread] = []
        self._event.connect(self._deliver)

    def start(self) -> None:
        if self._process is not None:
            return
        executable = player_executable()
        if executable is None:
            raise RuntimeError("Rust preview is not built. Run python -m seventh_convert.build_player")
        if self.trace:
            self.trace.record("engine_start", executable=str(executable), binary_mtime_ns=executable.stat().st_mtime_ns)
        self._process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for target in (self._read, self._write, self._read_errors):
            worker = threading.Thread(target=target, daemon=True)
            self._threads.append(worker)
            worker.start()

    def command(self, command: str, **fields) -> None:
        if self._closing:
            return
        if command in {"open", "seek", "scrub", "step", "close"}:
            self.generation += 1
        if self.trace and command != "ack":
            source = fields.get("source", {})
            self.trace.record("command", command=command, generation=self.generation,
                              fields={k: v for k, v in fields.items() if k != "source"},
                              source={k: v for k, v in source.items() if k != "files"},
                              sequence_files=len(source.get("files", [])))
        packet = json.dumps({"command": command, "generation": self.generation, **fields}, ensure_ascii=False).encode() + b"\n"
        if len(packet) > 16 * 1024 * 1024:
            raise ValueError("Player command exceeds 16 MiB")
        try:
            self._writes.put_nowait(packet)
        except queue.Full as exc:
            raise RuntimeError("Rust player command queue is full") from exc

    def open(self, source: dict, position_us: int = 0, index: int = 0) -> None:
        self.start()
        self.position_us, self.index = position_us, index
        self.command("open", source=source, position_us=position_us, index=index)

    def _write(self) -> None:
        try:
            assert self._process is not None and self._process.stdin is not None
            while (packet := self._writes.get()) is not None:
                self._process.stdin.write(packet)
                self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            if not self._closing:
                self._event.emit({"event": "error", "message": f"Rust player input: {exc}"}, b"")

    def _read(self) -> None:
        try:
            assert self._process is not None and self._process.stdout is not None
            stream = self._process.stdout
            while True:
                line = stream.readline(65537)
                if not line:
                    raise RuntimeError("Rust preview process exited")
                if len(line) > 65536:
                    raise ValueError("Oversized player event")
                header = json.loads(line)
                size = header.get("bytes", 0)
                if not isinstance(size, int) or not 0 <= size <= MAX_FRAME_BYTES:
                    raise ValueError("Invalid player payload size")
                if header.get("event") == "frame":
                    width, height = header.get("width", 0), header.get("height", 0)
                    if width <= 0 or height <= 0 or size != width * height * 4:
                        raise ValueError("Invalid RGBA8 frame dimensions")
                    for field, fallback in (("source_width", width), ("source_height", height)):
                        value = header.setdefault(field, fallback)
                        if not isinstance(value, int) or value <= 0:
                            raise ValueError("Invalid source raster dimensions")
                payload_started = time.perf_counter_ns()
                payload = bytearray(size)
                view = memoryview(payload)
                offset = 0
                while offset < size:
                    count = stream.readinto(view[offset:])
                    if not count:
                        raise RuntimeError("Truncated Rust player frame")
                    offset += count
                if header.get("event") == "frame":
                    self.received_frames += 1
                    header["_received_ns"] = time.perf_counter_ns()
                    if self.trace:
                        self.trace.record("received", generation=header.get("generation"), pts_us=header["position_us"],
                                          index=header["index"], engine_late_us=header.get("late_us"), bytes=size, width=header["width"], height=header["height"],
                                          read_ms=(header["_received_ns"] - payload_started) / 1e6)
                if not self._closing:
                    self._event.emit(header, payload)
        except (OSError, ValueError, RuntimeError) as exc:
            if not self._closing:
                self._event.emit({"event": "error", "message": str(exc)}, b"")

    def _read_errors(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        # Always drain the diagnostic pipe. Protocol errors carry actionable context.
        for _line in self._process.stderr:
            pass

    def _deliver(self, header: dict, payload: bytearray) -> None:
        if self._closing or header.get("generation", self.generation) != self.generation:
            if header.get("event") == "frame":
                self.stale_frames += 1
            if self.trace and header.get("event") == "frame":
                self.trace.record("stale_frame", generation=header.get("generation"), current_generation=self.generation,
                                  pts_us=header.get("position_us"))
            return
        kind = header.get("event")
        if kind == "frame":
            if self.trace:
                self.trace.record("ui_dispatch", generation=self.generation, pts_us=header["position_us"],
                                  queue_ms=(time.perf_counter_ns() - header.get("_received_ns", time.perf_counter_ns())) / 1e6)
            self.position_us, self.index = header["position_us"], header["index"]
            self.frameReady.emit(header, payload)
            self.command("ack")
        elif kind == "state":
            self.state = header["state"]
            self.stateChanged.emit(self.state)
        elif kind == "error":
            self.state = "paused"
            self.failed.emit(header["message"])

    def shutdown(self) -> None:
        if self._closing:
            return
        process = self._process
        if process is not None:
            self.command("quit")
        self._closing = True
        if process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            try:
                self._writes.put_nowait(None)
            except queue.Full:
                pass
            for worker in self._threads:
                worker.join(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
