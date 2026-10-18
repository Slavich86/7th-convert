"""Exercise the real Rust/Qt adapter using generated, silent media."""
from __future__ import annotations

import os
import json
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from seventh_convert.player import player_executable
from seventh_convert.ui import MainWindow, SelectedInput


@unittest.skipUnless(player_executable() and shutil.which("ffmpeg"), "Build player and install FFmpeg")
class PlayerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), self.window.last_error_report)

    def test_trace_detects_frames_replaced_before_qt_paint(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SEVENTH_PLAYER_TRACE": "1", "SEVENTH_PLAYER_TRACE_DIR": directory}):
            root = Path(directory)
            self.window = w = MainWindow(use_media=True, settings=QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat), app_config_dir=root / "config")
            controller = w.rust_preview
            try:
                w.show()
                self.app.processEvents()
                controller.active = True
                header = {"width": 2, "height": 2, "source_width": 2, "source_height": 2,
                          "index": 0, "generation": 0, "position_us": 100_000}
                controller._frame(header, bytearray(16))
                # Deliberately submit a second frame without servicing paint events.
                controller._frame({**header, "position_us": 140_000}, bytearray(16))
                self.wait_for(lambda: controller._painted_count == 1)
                self.assertEqual(controller._unpainted_count, 1)
            finally:
                w.close()
            records = [json.loads(line) for line in controller.trace.path.read_text().splitlines()]
            replaced = [r for r in records if r["event"] == "replaced_before_paint"]
            painted = [r for r in records if r["event"] == "painted"]
            self.assertEqual([r["pts_us"] for r in replaced], [100_000])
            self.assertEqual([r["pts_us"] for r in painted], [140_000])
            self.assertEqual(records[-1]["event"], "trace_close")
            self.assertEqual(records[-1]["lost_records"], 0)

    def test_audio_is_repositioned_only_after_playback_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.window = w = MainWindow(use_media=True, settings=QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat), app_config_dir=root / "config")
            controller = w.rust_preview
            audio = Mock()
            controller._audio = audio
            controller._audio_enabled = True
            controller.active = True
            controller.engine.state = "playing"
            controller.engine.position_us = 200_000
            header = {"width": 2, "height": 2, "source_width": 3840, "source_height": 2160, "index": 0}
            try:
                for _ in range(20):
                    controller._frame(header, bytearray(16))
                audio.setPosition.assert_called_once_with(200)
                audio.play.assert_called_once()
                self.assertEqual((w.current_source_raster_size.width(), w.current_source_raster_size.height()), (3840, 2160))
                controller._state("paused")
                audio.pause.assert_called_once()
                controller._state("playing")
                controller.engine.position_us = 700_000
                controller._frame(header, bytearray(16))
                self.assertEqual(audio.setPosition.call_count, 2)
                self.assertEqual(audio.play.call_count, 2)
                controller._audio_enabled = False
                controller._frame(header, bytearray(16))
                self.assertEqual(audio.play.call_count, 2)
            finally:
                w.close()

    def test_video_controls_and_sequence_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "preview.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x90:rate=10", "-vf", "setsar=2", "-c:v", "ffv1", str(video)], check=True)
            self.window = w = MainWindow(use_media=True, settings=QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat), app_config_dir=root / "config")
            frames = []
            w.rust_preview.engine.frameReady.connect(lambda header, pixels: frames.append((header, bytes(pixels))))
            try:
                w.show()
                w.apply_selected_input(SelectedInput(video, video))
                self.wait_for(lambda: len(frames) >= 1)
                self.assertTrue(w.rust_preview.active)
                self.assertEqual(w.play_button.text(), "")
                self.assertFalse(w.play_button.icon().isNull())
                self.assertEqual(w.play_button.toolTip(), "Play")
                self.assertFalse(w.stop_button.icon().isNull())
                self.assertTrue(w.stop_button.isEnabled())
                self.assertEqual(w.preview_placeholder._source_pixmap.width(), 160)
                self.assertAlmostEqual(w.preview_placeholder._source_pixel_aspect, 2)
                w.play_button.click()
                self.wait_for(lambda: len(frames) >= 4)
                self.assertEqual(w.play_button.accessibleName(), "Pause")
                self.assertNotEqual(frames[0][1], frames[-1][1])
                w.toggle_playback()
                self.wait_for(lambda: w.rust_preview.engine.state == "paused")
                self.assertEqual(w.play_button.accessibleName(), "Play")
                count = len(frames)
                QTest.qWait(200)
                self.assertEqual(len(frames), count)
                w.seek_preview_position(700)
                self.wait_for(lambda: w.rust_preview.engine.position_us == 700_000)
                w.set_in_point()
                self.assertEqual(w.position_slider.in_marker_ms, 700)
                w.step_preview_frames(1)
                self.wait_for(lambda: w.rust_preview.engine.position_us == 800_000)
                w.set_out_point()
                self.assertEqual(w.position_slider.out_marker_ms, 800)
                w.step_preview_frames(-1)
                self.wait_for(lambda: w.rust_preview.engine.position_us == 700_000)
                if screenshot := os.environ.get("SEVENTH_PLAYER_TEST_SCREENSHOT"):
                    self.assertTrue(w.grab().save(screenshot))
                w.play_button.click()
                self.wait_for(lambda: w.rust_preview.engine.state == "playing")
                w.stop_button.click()
                self.wait_for(lambda: w.rust_preview.engine.state == "paused" and w.rust_preview.engine.position_us == 0 and frames[-1][0]["generation"] == w.rust_preview.engine.generation)
                self.assertEqual(w.play_button.accessibleName(), "Play")
                self.assertEqual(w.position_slider.value(), 0)
                self.assertEqual(w.position_slider.in_marker_ms, 700)
                self.assertEqual(w.position_slider.out_marker_ms, 800)
                stopped_count = len(frames)
                QTest.qWait(150)
                self.assertEqual(len(frames), stopped_count)
                w.play_button.click()
                self.wait_for(lambda: w.rust_preview.engine.position_us > 0)
                w.stop_button.click()
                self.wait_for(lambda: w.rust_preview.engine.position_us == 0 and frames[-1][0]["generation"] == w.rust_preview.engine.generation)
                w.position_slider.setSliderDown(True)
                w.position_slider.setSliderPosition(1200)
                w.stop_button.click()
                self.wait_for(lambda: not w.rust_preview._scrubbing and w.rust_preview.engine.position_us == 0 and frames[-1][0]["generation"] == w.rust_preview.engine.generation)
                self.assertFalse(w.position_slider.isSliderDown())
                self.assertEqual(w.play_button.accessibleName(), "Play")
                for index in (1001, 1002, 1005):
                    w.preview_placeholder._source_pixmap.save(str(root / f"shot.{index}.png"))
                pattern = root / "shot.%04d.png"
                w.apply_selected_input(SelectedInput(pattern, root / "shot.1001.png", True, 1001, 1002, 2, "1001", "1002"))
                self.wait_for(lambda: w.sequence_preview_frames and w.rust_preview.engine.index == 0 and frames[-1][0]["generation"] == w.rust_preview.engine.generation)
                self.assertEqual([path.name for path in w.sequence_preview_frames], ["shot.1001.png", "shot.1002.png"])
                w.step_preview_frames(1)
                self.wait_for(lambda: w.rust_preview.engine.index == 1)
                self.assertEqual(w.current_frame_label.text(), "Frame: 1002")
                w.stop_button.click()
                self.wait_for(lambda: w.rust_preview.engine.index == 0 and frames[-1][0]["generation"] == w.rust_preview.engine.generation)
                self.assertEqual(w.current_frame_label.text(), "Frame: 1001")
                self.assertFalse(w.last_error_report, w.last_error_report)
            finally:
                w.close()
                self.app.processEvents()
            self.assertIsNotNone(w.rust_preview.engine._process.poll())

    def test_continuous_drag_paints_before_release_and_restores_play_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "scrub.mkv"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc=duration=4:size=160x90:rate=25", "-c:v", "ffv1", str(video)], check=True)
            self.window = w = MainWindow(use_media=True, settings=QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat), app_config_dir=root / "config")
            c = w.rust_preview
            frames = []
            c.engine.frameReady.connect(lambda h, p: frames.append(h))
            try:
                w.show()
                w.apply_selected_input(SelectedInput(video, video))
                self.wait_for(lambda: len(frames) >= 1)
                audio = Mock()
                audio.position.return_value = 0
                audio.playbackState.return_value.name = "PausedState"
                c._audio = audio
                c._audio_enabled = True
                for playing in (False, True):
                    if playing:
                        w.toggle_playback()
                        self.wait_for(lambda: audio.play.called)
                    count = len(frames)
                    w.position_slider.setSliderDown(True)
                    seeks_before = audio.setPosition.call_count
                    # Move every 10 ms for 1.2 s: a restartable 40 ms debounce
                    # never fires while the pointer moves at this rate.
                    for i in range(120):
                        w.position_slider.setSliderPosition(400 + (i % 30) * 40)
                        QTest.qWait(10)
                    self.assertGreaterEqual(len(frames) - count, 2)
                    self.assertEqual(audio.setPosition.call_count, seeks_before)
                    w.position_slider.setSliderPosition(1200)
                    w.position_slider.setSliderDown(False)
                    self.wait_for(lambda: not c._scrubbing)
                    self.wait_for(lambda: c.engine.state == ("playing" if playing else "paused"))
                    self.assertTrue(any(h["position_us"] == 1_200_000 for h in frames[count:]))
                    if playing:
                        self.wait_for(lambda: audio.setPosition.call_count > seeks_before)
                        self.assertEqual(audio.setPosition.call_count, seeks_before + 1)
                        w.toggle_playback()
                        self.wait_for(lambda: c.engine.state == "paused")
                    else:
                        self.assertEqual(c.engine.position_us, 1_200_000)
                        self.assertEqual(w.position_slider.value(), 1200)
                self.assertFalse(w.last_error_report)
            finally:
                w.close()
