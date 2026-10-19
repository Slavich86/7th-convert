"""Qt presentation/audio adapter. Playback state and scheduling live in Rust."""
from __future__ import annotations

from fractions import Fraction
from pathlib import Path
import os
import time

from PySide6.QtCore import QEvent, QObject, QSize, QTimer, QUrl, qVersion
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from .command_builder import build_player_color_filter
from .player import RustPlayer, player_executable
from .player_trace import PlayerTrace


class PreviewController(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.available = player_executable() is not None
        self.active = False
        directory = Path(os.environ.get("SEVENTH_PLAYER_TRACE_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
        self.trace = PlayerTrace(directory)
        self.engine = RustPlayer(self)
        self.engine.trace = self.trace
        self._pending_paint = None
        self._submitted_count = self._painted_count = self._unpainted_count = 0
        self._last_submit_ns = self._last_paint_ns = None
        self._pulse_ns = self._report_ns = time.perf_counter_ns()
        self._max_ui_stall_ms = 0.0
        self._trace_error_reported = False
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(100)
        self._heartbeat.timeout.connect(self._trace_heartbeat)
        if self.trace.enabled:
            self._heartbeat.start()
            window.append_log(f"Temporary player diagnostic log: {self.trace.path}")
        elif self.trace.error:
            window.append_log(f"Player diagnostic log unavailable: {self.trace.error}")
        window.preview_placeholder.framePainted.connect(self._painted)
        screen = window.screen()
        self.trace.record("qt_environment", qt=qVersion(), platform=QGuiApplication.platformName(),
                          screen=screen.name() if screen else None, refresh_hz=screen.refreshRate() if screen else None,
                          device_pixel_ratio=window.devicePixelRatioF(), engine_available=self.available)
        self.engine.frameReady.connect(self._frame)
        self.engine.stateChanged.connect(self._state)
        self.engine.failed.connect(self._error)
        self._reload = QTimer(self)
        self._reload.setSingleShot(True)
        self._reload.timeout.connect(self._open)
        self._seek = QTimer(self)
        self._seek.setSingleShot(True)
        self._seek.timeout.connect(self._apply_seek)
        self._seek_value = 0
        self._scrubbing = self._dragging = self._scrub_resume = False
        self._scrub_pending = self._scrub_inflight = self._scrub_last_value = None
        window.position_slider.sliderPressed.connect(self._begin_scrub)
        window.position_slider.sliderReleased.connect(self._end_scrub)
        self._resume = False
        self._audio: QMediaPlayer | None = None
        self._audio_output: QAudioOutput | None = None
        self._audio_enabled = False
        self._audio_start_pending = True
        self._resize = QTimer(self)
        self._resize.setSingleShot(True)
        self._resize.timeout.connect(self.refresh)
        window.preview_placeholder.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.window.preview_placeholder and event.type() == QEvent.Type.Resize and self.active and not self._scrubbing:
            self.trace.record("viewport_resize", width=watched.width(), height=watched.height())
            self._resize.start(250)
        return super().eventFilter(watched, event)

    def open(self) -> None:
        self.active = True
        self.engine.position_us, self.engine.index = 0, 0
        self._resume = False
        self._reload.start(0)

    def refresh(self) -> None:
        if self.active:
            self._resume = self._resume or self.engine.state == "playing"
            self._reload.start(30)

    def _open(self) -> None:
        w = self.window
        if not self.active or w.preview_source_path is None:
            return
        self._cancel_scrub()
        self._seek.stop()
        try:
            fps = Fraction(str(w.current_fps)).limit_denominator(100_000)
            if w.sequence_preview_frames and w.fps_edit.text().strip():
                fps = Fraction(w.fps_edit.text().strip()).limit_denominator(100_000)
            viewport = w.preview_placeholder
            ratio = viewport.devicePixelRatioF()
            source = {
                "path": str(w.preview_source_path.resolve()),
                "files": [str(path.resolve()) for path in w.sequence_preview_frames],
                "fps_num": fps.numerator, "fps_den": fps.denominator,
                "filter": build_player_color_filter(w.current_player_preset()),
                "max_width": min(8192, max(1, round(viewport.width() * ratio))),
                "max_height": min(8192, max(1, round(viewport.height() * ratio))),
            }
            if fps <= 0:
                raise ValueError("Preview FPS must be positive")
            self.engine.open(source, self.engine.position_us, self.engine.index)
            self._prepare_audio()
            if self._resume:
                self.engine.command("play")
            self._resume = False
        except (RuntimeError, ValueError, OSError) as exc:
            self._error(str(exc))

    def stop(self) -> None:
        self._cancel_scrub()
        self._reload.stop()
        self._resize.stop()
        self._seek.stop()
        self.active = False
        if self._pending_paint:
            self.trace.record("pending_discarded", reason="source_stop", **self._pending_paint)
            self._pending_paint = None
        self.window.preview_placeholder.player_frame_token = None
        if self.engine._process is not None:
            self.engine.command("close")
        if self._audio:
            self._audio.stop()
            self._audio.setSource(QUrl())

    def stop_playback(self) -> None:
        self._flush_reload()
        self._cancel_scrub()
        self._seek.stop()
        self._resize.stop()
        self._resume = False
        self._audio_start_pending = True
        if self._audio:
            self._audio.stop()
        self.engine.command("seek", position_us=0, index=0)
        # Pause must use the new seek generation so its state event is accepted.
        self.engine.command("pause")
        self.window.update_playback_button(False)

    def toggle(self) -> None:
        if self._scrubbing:
            self._scrub_resume = not self._scrub_resume
            self.window.update_playback_button(self._scrub_resume)
            return
        self._flush_reload()
        self.engine.command("pause" if self.engine.state == "playing" else "play")

    def seek(self, value: int) -> None:
        if self._scrubbing:
            self._scrub_pending = value
            # Throttle, not debounce: movement must not postpone every request.
            # Finish one frame while retaining only the newest pointer position.
            if self._scrub_inflight is None and not self._seek.isActive():
                self._seek.start(0)
            return
        self._audio_start_pending = True
        if self._audio:
            self._audio.pause()
        self._seek_value = value
        self._seek.start(40)

    def _apply_seek(self) -> None:
        self._flush_reload()
        if self._scrubbing:
            if self._scrub_inflight is not None or self._scrub_pending is None:
                return
            self._seek_value = self._scrub_pending
            self._scrub_pending = None
        w = self.window
        command = "scrub" if self._dragging else "seek"
        if w.sequence_preview_frames:
            self.engine.command(command, index=max(0, self._seek_value))
        else:
            self.engine.command(command, position_us=max(0, self._seek_value * 1000))
        if self._scrubbing:
            self._scrub_inflight = (self.engine.generation, self._seek_value)

    def _begin_scrub(self) -> None:
        if not self.active:
            return
        self._flush_reload()
        resume = self._scrub_resume if self._scrubbing else self.engine.state == "playing"
        self._cancel_scrub()
        self._seek.stop()
        self._resize.stop()
        self._scrubbing = self._dragging = True
        self._scrub_resume = resume
        self._audio_start_pending = True
        self.engine.command("pause")
        if self._audio:
            self._audio.pause()
        self.trace.record("scrub_begin", resume=resume)

    def _end_scrub(self) -> None:
        if not self._dragging:
            return
        self._dragging = False
        value = self.window.position_slider.sliderPosition()
        self._seek.stop()
        # Release has priority over a preview still being decoded.
        self._scrub_inflight = None
        self._scrub_pending = value
        self._apply_seek()

    def _finish_scrub(self) -> None:
        resume = self._scrub_resume
        self.trace.record("scrub_end", resume=resume, pts_us=self.engine.position_us)
        self._cancel_scrub()
        self._audio_start_pending = True
        self.engine.command("play" if resume else "pause")

    def _cancel_scrub(self) -> None:
        self._scrubbing = self._dragging = self._scrub_resume = False
        self._scrub_pending = self._scrub_inflight = self._scrub_last_value = None

    def step(self, delta: int) -> None:
        self._cancel_scrub()
        self._seek.stop()
        self._flush_reload()
        self.engine.command("step", delta=delta)
        if self._audio:
            self._audio.pause()

    def _flush_reload(self) -> None:
        if self._reload.isActive():
            self._reload.stop()
            self._open()

    def _frame(self, header: dict, pixels: bytearray) -> None:
        if not self.active:
            return
        started_ns = time.perf_counter_ns()
        w = self.window
        width, height = header["width"], header["height"]
        image = QImage(pixels, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        image_ns = time.perf_counter_ns()
        w.sequence_frame_index = header["index"] if w.sequence_preview_frames else 0
        path = w._current_preview_frame_path()
        w.update_source_raster_size(QSize(header["source_width"], header["source_height"]))
        if path is not None:
            w._sync_auto_pixel_aspect_edit(path)
        aspect = w.selected_pixel_aspect_for_path(path)
        if not w.sequence_preview_frames and w.selected_preview_pixel_aspect_mode() == "auto":
            for stream in (w.current_probe_json or {}).get("streams", []):
                if stream.get("codec_type") == "video":
                    try:
                        ratio = Fraction(str(stream.get("sample_aspect_ratio", "1:1")).replace(":", "/"))
                        if ratio > 0:
                            aspect = float(ratio)
                    except (ValueError, ZeroDivisionError):
                        pass
                    break
        # Compensate integer rounding of the scaled raster to preserve display aspect.
        aspect *= header["source_width"] * height / (header["source_height"] * width)
        metadata_ns = time.perf_counter_ns()
        token = {"generation": header.get("generation", self.engine.generation), "pts_us": header.get("position_us", self.engine.position_us),
                 "submitted_ns": metadata_ns}
        if self._pending_paint is not None:
            self._unpainted_count += 1
            self.trace.record("replaced_before_paint", **self._pending_paint, replacement_pts_us=token["pts_us"],
                              replacement_generation=token["generation"])
        self._pending_paint = token
        w.preview_placeholder.player_frame_token = token
        w.preview_placeholder.set_source_pixmap(QPixmap.fromImage(image), aspect)
        pixmap_ns = time.perf_counter_ns()
        value = self.engine.index if w.preview_is_sequence else self.engine.position_us // 1000
        if not w.position_slider.isSliderDown() and not self._seek.isActive() and not self._scrubbing:
            w.position_slider.blockSignals(True)
            w.position_slider.setValue(value)
            w.position_slider.blockSignals(False)
        w.update_current_frame_label()
        if self._audio_enabled and self._audio and self.engine.state == "playing" and self._audio_start_pending and not self._scrubbing:
            # Reposition only after a user action, never repeatedly during playback.
            self.trace.record("audio_reposition", position_us=self.engine.position_us)
            self._audio.setPosition(self.engine.position_us // 1000)
            self._audio.play()
            self._audio_start_pending = False
        finished_ns = time.perf_counter_ns()
        self._submitted_count += 1
        self.trace.record("submitted", **token, state=self.engine.state,
                          interval_ms=(started_ns - self._last_submit_ns) / 1e6 if self._last_submit_ns else None,
                          image_ms=(image_ns - started_ns) / 1e6, metadata_ms=(metadata_ns - image_ns) / 1e6,
                          pixmap_ms=(pixmap_ns - metadata_ns) / 1e6, total_ms=(finished_ns - started_ns) / 1e6,
                          visible=w.preview_placeholder.isVisible(), minimized=w.isMinimized())
        self._last_submit_ns = started_ns
        if self._scrubbing and self._scrub_inflight and header.get("generation") == self._scrub_inflight[0]:
            self._scrub_last_value = self._scrub_inflight[1]
            self._scrub_inflight = None
            if self._scrub_pending == self._scrub_last_value:
                self._scrub_pending = None
            if self._scrub_pending is not None:
                self._seek.start(0)
            elif not self._dragging:
                # Defer until RustPlayer has acknowledged this exact frame.
                QTimer.singleShot(0, self._complete_scrub)

    def _complete_scrub(self) -> None:
        if self.active and self._scrubbing and not self._dragging and self._scrub_inflight is None and self._scrub_pending is None:
            self._finish_scrub()

    def _painted(self, token: dict) -> None:
        now = time.perf_counter_ns()
        self._painted_count += 1
        self.trace.record("painted", **token, paint_wait_ms=(now - token["submitted_ns"]) / 1e6,
                          interval_ms=(now - self._last_paint_ns) / 1e6 if self._last_paint_ns else None)
        self._last_paint_ns = now
        if self._pending_paint is token:
            self._pending_paint = None

    def _trace_heartbeat(self) -> None:
        now = time.perf_counter_ns()
        self._max_ui_stall_ms = max(self._max_ui_stall_ms, max(0, (now - self._pulse_ns) / 1e6 - 100))
        self._pulse_ns = now
        if self.trace.error and not self._trace_error_reported:
            self.window.append_log(f"Player diagnostic log failed: {self.trace.error}")
            self._trace_error_reported = True
        if now - self._report_ns < 1_000_000_000:
            return
        self._report_ns = now
        self._trace_summary("heartbeat", ui_stall_ms=self._max_ui_stall_ms)
        self._max_ui_stall_ms = 0.0

    def _trace_summary(self, reason: str, **fields) -> None:
        audio = self._audio if self._audio_enabled else None
        self.trace.record("summary", reason=reason, generation=self.engine.generation, state=self.engine.state,
                          pts_us=self.engine.position_us, received=self.engine.received_frames, stale=self.engine.stale_frames,
                          submitted=self._submitted_count, painted=self._painted_count,
                          replaced_before_paint=self._unpainted_count, lost_log_records=self.trace.lost_records,
                          audio_position_ms=audio.position() if audio else None,
                          audio_state=audio.playbackState().name if audio else None, **fields)

    def _state(self, state: str) -> None:
        if not self.active:
            return
        self.trace.record("state", state=state, generation=self.engine.generation, pts_us=self.engine.position_us)
        if state == "ended" and self._dragging:
            self._scrub_inflight = None
            if self._scrub_pending is not None:
                self._seek.start(0)
        self._audio_start_pending = True
        playing = self._scrub_resume if self._scrubbing else state == "playing"
        self.window.update_playback_button(playing)
        if state != "playing" and self._audio:
            self._audio.pause()

    def _prepare_audio(self) -> None:
        w = self.window
        self._audio_start_pending = True
        self._audio_enabled = bool(w.use_media and not w.sequence_preview_frames and w.current_input_has_audio)
        if not self._audio_enabled:
            if self._audio:
                self._audio.stop()
            return
        if self._audio is None:
            self._audio = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._audio.setAudioOutput(self._audio_output)
            self._audio.errorOccurred.connect(lambda _code, message: w.record_error("Preview audio failed", message))
        url = QUrl.fromLocalFile(str(w.preview_source_path))
        if self._audio.source() != url:
            self._audio.setSource(url)
        self._audio.pause()

    def _error(self, message: str) -> None:
        if not self.active:
            return
        self._cancel_scrub()
        self._seek.stop()
        self.trace.record("error", message=message, generation=self.engine.generation)
        self.window.update_playback_button(False)
        self.window.preview_placeholder.setText(f"Preview failed: {message}")
        self.window.record_error("Rust preview failed", message)
        if self._audio:
            self._audio.pause()

    def shutdown(self) -> None:
        self.stop()
        self.engine.shutdown()
        self._heartbeat.stop()
        self._trace_summary("shutdown")
        self.trace.close()
