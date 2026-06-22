import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QKeyEvent, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from seventh_convert.ui import (  # noqa: E402
    MainWindow,
    PreviewLabel,
    SequenceFileDialog,
    SelectedInput,
    _fps_from_probe,
    _input_list_items,
    _navigation_places,
    _display_pixmap_for_input_transform,
    _frame_text_to_ms,
    _media_summary,
    _ms_to_frame,
    _parse_positive_float,
    _time_to_ms,
    _default_output_path,
    apply_theme,
)
from seventh_convert.presets import get_preset  # noqa: E402
from seventh_convert.sequence import sequence_pattern_from_selection, sequence_start_number  # noqa: E402


class UiTests(unittest.TestCase):
    def test_main_window_can_be_created(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        self.assertEqual(window.windowTitle(), "7th Convert")
        self.assertGreaterEqual(window.tabs.count(), 4)
        window.close()
        app.processEvents()

    def test_progress_panel_is_global_below_tabs(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        self.assertEqual(window.main_layout.indexOf(window.tabs), 0)
        self.assertGreater(window.main_layout.indexOf(window.progress_bar.parentWidget()), window.main_layout.indexOf(window.tabs))
        window.tabs.setCurrentIndex(window.tabs.indexOf(window.log_text.parentWidget()))
        window.progress_label.setText("Running")
        self.assertEqual(window.progress_label.text(), "Running")
        window.close()
        app.processEvents()

    def test_player_is_not_created_before_input_file(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=True)
        self.assertIsNone(window.player)
        self.assertFalse(window.play_button.isEnabled())
        self.assertEqual(window.preview_placeholder.text(), "Open a media file to preview")
        window.close()
        app.processEvents()

    def test_selecting_video_input_preloads_player_without_playing(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=True)
        preload_calls = []
        window._start_preview_source = lambda: preload_calls.append(True)  # type: ignore[method-assign]
        window._prepare_preview_source("/tmp/example.mov")
        self.assertEqual(preload_calls, [True])
        self.assertTrue(window.play_button.isEnabled())
        window.close()
        app.processEvents()

    def test_video_timeline_is_enabled_after_probe_before_play(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=True)
        window._start_preview_source = lambda: None  # type: ignore[method-assign]
        window._prepare_preview_source("/tmp/example.mov")
        window.current_probe_json = {
            "format": {"duration": "2.5"},
            "streams": [{"codec_type": "video", "avg_frame_rate": "25/1"}],
        }

        window._sync_video_timeline_from_probe()

        self.assertTrue(window.position_slider.isEnabled())
        self.assertEqual(window.position_slider.maximum(), 2500)
        window.close()
        app.processEvents()

    def test_range_edits_update_timeline_markers(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.position_slider.setMaximum(10_000)
        window.in_edit.setText("00:00:02.500")
        window.out_edit.setText("00:00:08.000")
        window.update_range_markers_from_edits()
        self.assertEqual(window.position_slider.in_marker_ms, 2500)
        self.assertEqual(window.position_slider.out_marker_ms, 8000)
        window.clear_range()
        self.assertIsNone(window.position_slider.in_marker_ms)
        self.assertIsNone(window.position_slider.out_marker_ms)
        window.close()
        app.processEvents()

    def test_time_to_ms_accepts_common_timecode_shapes(self):
        self.assertEqual(_time_to_ms("00:00:02.500"), 2500)
        self.assertEqual(_time_to_ms("01:02.000"), 62000)
        self.assertEqual(_time_to_ms("3.25"), 3250)
        self.assertIsNone(_time_to_ms("bad"))

    def test_frame_conversion_helpers_use_current_fps(self):
        self.assertEqual(_ms_to_frame(2000, 25.0), 50)
        self.assertEqual(_frame_text_to_ms("50", 25.0), 2000)
        self.assertIsNone(_frame_text_to_ms("bad", 25.0))

    def test_frame_mode_updates_markers_and_ffmpeg_time(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.current_fps = 25.0
        window.time_mode_combo.setCurrentIndex(1)
        window.in_edit.setText("50")
        window.out_edit.setText("100")
        window.update_range_markers_from_edits()
        self.assertEqual(window.position_slider.in_marker_ms, 2000)
        self.assertEqual(window.position_slider.out_marker_ms, 4000)
        self.assertEqual(window._edit_value_to_ffmpeg_time("50"), "00:00:02.000")
        window.close()
        app.processEvents()

    def test_switching_to_frame_mode_reformats_existing_markers(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.current_fps = 25.0
        window.position_slider.set_in_marker(2000)
        window.position_slider.set_out_marker(4000)
        window.in_edit.setText("00:00:02.000")
        window.out_edit.setText("00:00:04.000")
        window.time_mode_combo.setCurrentIndex(1)
        self.assertEqual(window.in_edit.text(), "50")
        self.assertEqual(window.out_edit.text(), "100")
        window.close()
        app.processEvents()

    def test_fps_from_probe_reads_video_frame_rate(self):
        probe_json = {"streams": [{"codec_type": "video", "avg_frame_rate": "30000/1001"}]}
        self.assertAlmostEqual(_fps_from_probe(probe_json), 29.97002997)

    def test_media_summary_shows_duration_in_seconds_and_frames(self):
        probe_json = {
            "format": {"format_name": "mov", "duration": "2.0", "size": "1000"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "avg_frame_rate": "25/1", "width": 1920, "height": 1080}],
        }

        summary = _media_summary(probe_json)

        self.assertIn("Duration: 2.000s / 50 frames", summary)
        self.assertIn("Size: 0.98 KB", summary)

    def test_media_summary_formats_large_size_as_mb(self):
        probe_json = {
            "format": {"format_name": "image2", "duration": "0.04", "size": "5235008"},
            "streams": [],
        }

        summary = _media_summary(probe_json)

        self.assertIn("Size: 4.99 MB", summary)

    def test_media_summary_counts_sequence_frames_from_input_pattern(self):
        probe_json = {
            "format": {"format_name": "image2", "duration": "0.04", "size": "1000"},
            "streams": [{"codec_type": "video", "codec_name": "mjpeg", "avg_frame_rate": "25/1", "width": 100, "height": 100}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in range(1000, 1005):
                (root / f"shot_{frame}.jpg").touch()

            summary = _media_summary(probe_json, input_path=root / "shot_%04d.jpg", fps=25.0)

        self.assertIn("Duration: 0.200s / 5 frames", summary)
        self.assertNotIn("0.040s / 1 frames", summary)

    def test_media_summary_sums_sequence_file_sizes(self):
        probe_json = {
            "format": {"format_name": "image2", "duration": "0.04", "size": "1024"},
            "streams": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in range(1000, 1003):
                (root / f"shot_{frame}.jpg").write_bytes(b"x" * 1024)

            summary = _media_summary(probe_json, input_path=root / "shot_%04d.jpg", fps=25.0)

        self.assertIn("Size: 3.00 KB", summary)
        self.assertNotIn("Size: 1.00 KB", summary)

    def test_finished_playback_restores_placeholder_without_dropping_source(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.preview_source_path = "/tmp/example.mov"
        window.position_slider.setValue(100)
        window.reset_preview_after_finished()
        self.assertIsNone(window.player)
        self.assertIsNone(window.video_widget)
        self.assertEqual(window.preview_placeholder.text(), "Playback finished. Press Play to preview again.")
        self.assertEqual(window.position_slider.value(), 0)
        self.assertEqual(window.preview_source_path, "/tmp/example.mov")
        window.close()
        app.processEvents()

    def test_output_controls_replace_preset_selector(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        self.assertFalse(hasattr(window, "preset_combo"))
        self.assertEqual(window.file_type_combo.currentData(), "exr")
        self.assertEqual(window.codec_combo.currentData(), "exr")
        self.assertEqual(window.codec_profile_label.text(), "Compression")
        self.assertEqual(window.codec_profile_combo.currentData(), "zip1")
        self.assertTrue(window.fps_edit.isHidden())
        window.close()
        app.processEvents()

    def test_jpg_to_exr_defaults_to_srgb_input_and_linear_output_transform(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.input_edit.setText("/tmp/shot_%04d.jpg")
        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("exr"))
        window.refresh_color_transform_defaults()

        preset = window.current_preset()

        self.assertEqual(window.input_transform_combo.currentData(), "srgb")
        self.assertEqual(window.output_transform_combo.currentData(), "linear")
        self.assertEqual(preset.filters["input_color_space"], "srgb")
        self.assertEqual(preset.filters["output_color_space"], "linear")
        window.close()
        app.processEvents()

    def test_exr_compression_options_match_reference_supported_by_ffmpeg(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        labels = [window.codec_profile_combo.itemText(index) for index in range(window.codec_profile_combo.count())]
        values = [window.codec_profile_combo.itemData(index) for index in range(window.codec_profile_combo.count())]
        disabled = [
            window.codec_profile_combo.itemData(index)
            for index in range(window.codec_profile_combo.count())
            if not window.codec_profile_combo.model().item(index).isEnabled()
        ]

        self.assertEqual(labels, [
            "none",
            "Zip (1 scanline)",
            "Zip (16 scanlines)",
            "RLE",
        ])
        self.assertEqual(values, ["none", "zip1", "zip16", "rle"])
        self.assertEqual(disabled, [])
        window.close()
        app.processEvents()

    def test_file_type_menu_contains_requested_formats_in_order(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        values = [window.file_type_combo.itemData(index) for index in range(window.file_type_combo.count())]
        self.assertEqual(values[:7], ["exr", "gif", "png", "jpg", "mov", "mp4", "targa"])
        window.close()
        app.processEvents()

    def test_probe_button_and_command_preview_are_removed(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        self.assertFalse(hasattr(window, "probe_button"))
        self.assertFalse(hasattr(window, "command_preview"))
        self.assertEqual(window.build_button.text(), "Copy Command")
        window.close()
        app.processEvents()

    def test_sequence_selection_collapses_to_ffmpeg_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "shot_A_0001.png"
            second = root / "shot_A_0002.png"
            other = root / "shot_B_0001.png"
            first.touch()
            second.touch()
            other.touch()

            pattern = sequence_pattern_from_selection([first])

        self.assertEqual(pattern, first.with_name("shot_A_%04d.png"))

    def test_seq_mode_input_list_groups_sequence_as_one_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "subdir").mkdir()
            (root / "DSCF0358_1000.jpg").touch()
            (root / "DSCF0358_1001.jpg").touch()
            (root / "notes.txt").touch()

            items = _input_list_items(root, seq_mode=True)

        self.assertEqual([item.label for item in items], ["subdir", "DSCF0358_%04d.jpg  (1000-1001, 2 frames)"])
        self.assertFalse(items[0].is_sequence)
        self.assertTrue(items[0].is_directory)
        self.assertTrue(items[1].is_sequence)

    def test_seq_mode_splits_sequence_groups_on_frame_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in list(range(0, 11)) + list(range(15, 31)):
                (root / f"shot_{frame:04d}.jpg").touch()

            items = _input_list_items(root, seq_mode=True)

        labels = [item.label for item in items]
        self.assertEqual(labels, [
            "shot_%04d.jpg  (0000-0010, 11 frames)",
            "shot_%04d.jpg  (0015-0030, 16 frames)",
        ])
        self.assertTrue(all(item.is_sequence for item in items))
        self.assertEqual(items[0].sequence_start, 0)
        self.assertEqual(items[0].sequence_end, 10)
        self.assertEqual(items[1].sequence_start, 15)
        self.assertEqual(items[1].sequence_end, 30)

    def test_selected_sequence_range_changes_output_name_and_start_number(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in list(range(0, 11)) + list(range(15, 31)):
                (root / f"shot_{frame:04d}.jpg").touch()
            item = _input_list_items(root, seq_mode=True)[1]

            window = MainWindow(use_media=False)
            selected = SelectedInput(
                item.input_path,
                item.preview_path,
                item.is_sequence,
                item.sequence_start,
                item.sequence_end,
                item.sequence_frame_count,
                item.sequence_start_text,
                item.sequence_end_text,
            )
            window.input_edit.setText(str(selected.input_path))
            window.current_input_is_sequence = selected.is_sequence
            window.current_input_sequence_start = selected.sequence_start
            window.current_input_sequence_end = selected.sequence_end
            window.current_input_sequence_frame_count = selected.sequence_frame_count
            window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("exr"))
            window.output_edit.setText(_default_output_path(
                selected.input_path,
                window.current_preset(),
                sequence_start=selected.sequence_start,
                sequence_end=selected.sequence_end,
                sequence_start_text=selected.sequence_start_text,
                sequence_end_text=selected.sequence_end_text,
            ))
            job = window.build_job()

        self.assertEqual(window.output_edit.text(), str(root / "shot.%04d.exr"))
        self.assertEqual(job.input_start_number, 15)
        self.assertEqual(job.output_start_number, 15)
        window.close()
        app.processEvents()

    def test_sequence_output_start_number_preserves_source_frame_range_after_in_point(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in range(1000, 1150):
                (root / f"DSCF0358_{frame:04d}.jpg").touch()

            window = MainWindow(use_media=False)
            window.current_fps = 25.0
            window.time_mode_combo.setCurrentIndex(window.time_mode_combo.findData("frames"))
            window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("png"))
            window.input_edit.setText(str(root / "DSCF0358_%04d.jpg"))
            window.output_edit.setText(str(root / "DSCF0358.%04d.png"))
            window.current_input_is_sequence = True
            window.current_input_sequence_start = 1000
            window.current_input_sequence_end = 1149
            window.in_edit.setText("145")
            window.out_edit.setText("149")

            job = window.build_job()

        self.assertEqual(job.input_start_number, 1000)
        self.assertEqual(job.output_start_number, 1145)
        self.assertEqual(job.output.name, "DSCF0358.%04d.png")
        window.close()
        app.processEvents()

    def test_selected_sequence_range_survives_file_type_refresh(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in range(1, 45):
                (root / f"DSCF0358_{frame:04d}.jpg").touch()
            item = _input_list_items(root, seq_mode=True)[0]

            window = MainWindow(use_media=False)
            window.input_edit.setText(str(item.input_path))
            window.current_input_is_sequence = item.is_sequence
            window.current_input_sequence_start = item.sequence_start
            window.current_input_sequence_end = item.sequence_end
            window.current_input_sequence_frame_count = item.sequence_frame_count
            window.current_input_sequence_start_text = item.sequence_start_text
            window.current_input_sequence_end_text = item.sequence_end_text
            window.input_range_edit.setText(window._selected_range_text())
            window.output_edit.setText(str(root / "DSCF0358.%04d.exr"))
            window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("exr"))
            window.refresh_output_path_extension()

        self.assertEqual(window.input_edit.text(), str(root / "DSCF0358_%04d.jpg"))
        self.assertEqual(window.input_range_edit.text(), "0001-0044, 44 frames")
        self.assertEqual(window.output_edit.text(), str(root / "DSCF0358.%04d.exr"))
        window.close()
        app.processEvents()

    def test_sequence_output_keeps_clean_base_name_without_range(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in range(1, 45):
                (root / f"DSCF0358_{frame:04d}.jpg").touch()
            item = _input_list_items(root, seq_mode=True)[0]

            window = MainWindow(use_media=False)
            window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("png"))
            output = _default_output_path(
                item.input_path,
                window.current_preset(),
                sequence_start=item.sequence_start,
                sequence_end=item.sequence_end,
                sequence_start_text=item.sequence_start_text,
                sequence_end_text=item.sequence_end_text,
            )

        self.assertEqual(output, str(root / "DSCF0358.%04d.png"))
        self.assertEqual(output.replace("%04d", "0001"), str(root / "DSCF0358.0001.png"))
        window.close()
        app.processEvents()

    def test_normal_input_list_keeps_sequence_frames_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "subdir").mkdir()
            (root / "DSCF0358_1000.jpg").touch()
            (root / "DSCF0358_1001.jpg").touch()

            items = _input_list_items(root, seq_mode=False)

        self.assertEqual(
            [item.label for item in items],
            ["subdir", "DSCF0358_1000.jpg", "DSCF0358_1001.jpg"],
        )

    def test_sequence_dialog_uses_folder_icon_for_directories(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "child").mkdir()

            dialog = SequenceFileDialog(None, root)
            item = dialog.file_list.item(0)

        self.assertEqual(item.text(), "child")
        self.assertFalse(item.icon().isNull())
        dialog.close()
        app.processEvents()

    def test_sequence_dialog_uses_compact_up_button_without_extra_folder_actions(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            dialog = SequenceFileDialog(None, Path(tmp))

        self.assertEqual(dialog.up_button.text(), "↑")
        self.assertFalse(dialog.up_button.icon().isNull())
        button_texts = [button.text() for button in dialog.findChildren(QPushButton)]
        self.assertNotIn("New Folder", button_texts)
        self.assertNotIn("Choose Folder", button_texts)
        dialog.close()
        app.processEvents()

    def test_navigation_places_include_home_and_root(self):
        places = dict(_navigation_places())

        self.assertEqual(places["Home"], Path.home())
        self.assertEqual(places["Root"], Path("/"))

    def test_sequence_dialog_open_on_directory_navigates_instead_of_accepting(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "child"
            child.mkdir()

            dialog = SequenceFileDialog(None, root)
            dialog.file_list.setCurrentRow(0)
            dialog.accept_selection()

        self.assertEqual(dialog.current_dir, child)
        self.assertIsNone(dialog.selected_input)
        dialog.close()
        app.processEvents()

    def test_sequence_start_number_reads_first_available_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "DSCF0358_1000.jpg").touch()
            (root / "DSCF0358_1001.jpg").touch()

            start_number = sequence_start_number(root / "DSCF0358_%04d.jpg")

        self.assertEqual(start_number, 1000)

    def test_ui_build_job_infers_sequence_start_number(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "DSCF0358_1000.jpg").touch()
            (root / "DSCF0358_1001.jpg").touch()

            window = MainWindow(use_media=False)
            window.input_edit.setText(str(root / "DSCF0358_%04d.jpg"))
            window.output_edit.setText(str(root / "out.mp4"))
            job = window.build_job()

        self.assertEqual(job.input_start_number, 1000)
        window.close()
        app.processEvents()

    def test_zero_zero_range_is_treated_as_no_range(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.input_edit.setText("/tmp/input.mov")
        window.output_edit.setText("/tmp/output.mp4")
        window.in_edit.setText("00:00:00.000")
        window.out_edit.setText("00:00:00.000")

        job = window.build_job()

        self.assertIsNone(job.in_point)
        self.assertIsNone(job.out_point)
        window.close()
        app.processEvents()

    def test_invalid_nonzero_range_fails_before_ffmpeg(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.input_edit.setText("/tmp/input.mov")
        window.output_edit.setText("/tmp/output.mp4")
        window.in_edit.setText("00:00:02.000")
        window.out_edit.setText("00:00:01.000")

        with self.assertRaisesRegex(ValueError, "Out point must be greater"):
            window.build_job()

        window.close()
        app.processEvents()

    def test_sequence_preview_uses_frame_timer_instead_of_media_player(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "DSCF0358_1000.jpg"
            second = root / "DSCF0358_1001.jpg"
            first.touch()
            second.touch()

            window = MainWindow(use_media=True)
            window.input_edit.setText(str(root / "DSCF0358_%04d.jpg"))
            window._prepare_preview_source(first, is_sequence=True)

        self.assertIsNone(window.player)
        self.assertEqual(len(window.sequence_preview_frames), 2)
        self.assertTrue(window.play_button.isEnabled())
        self.assertEqual(window.position_slider.maximum(), 1)
        window.close()
        app.processEvents()

    def test_preview_pixmap_rescales_when_preview_label_resizes(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        label = PreviewLabel()
        pixmap = QPixmap(200, 100)
        pixmap.fill(QColor("#336699"))

        label.resize(100, 100)
        label.show()
        label.set_source_pixmap(pixmap)
        app.processEvents()
        first_size = label.pixmap().size()

        label.resize(50, 100)
        app.processEvents()
        second_size = label.pixmap().size()

        self.assertEqual(first_size.width(), 100)
        self.assertEqual(first_size.height(), 50)
        self.assertEqual(second_size.width(), 50)
        self.assertEqual(second_size.height(), 25)
        label.close()
        app.processEvents()

    def test_linear_input_transform_is_converted_for_preview_display(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        pixmap = QPixmap(1, 1)
        pixmap.fill(QColor(128, 128, 128))

        converted = _display_pixmap_for_input_transform(pixmap, "linear")
        displayed = converted.toImage().pixelColor(0, 0)

        self.assertGreater(displayed.red(), 128)
        self.assertGreater(displayed.green(), 128)
        self.assertGreater(displayed.blue(), 128)
        app.processEvents()

    def test_rec709_input_transform_is_converted_for_preview_display(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        pixmap = QPixmap(1, 1)
        pixmap.fill(QColor(32, 32, 32))

        converted = _display_pixmap_for_input_transform(pixmap, "rec709")
        displayed = converted.toImage().pixelColor(0, 0)

        self.assertGreater(displayed.red(), 32)
        self.assertGreater(displayed.green(), 32)
        self.assertGreater(displayed.blue(), 32)
        app.processEvents()

    def test_video_file_preview_uses_input_transform_display(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        pixmap = QPixmap(1, 1)
        pixmap.fill(QColor(128, 128, 128))
        window._last_video_source_pixmap = pixmap

        window._set_combo_data(window.input_transform_combo, "linear")

        displayed = window.preview_placeholder._source_pixmap.toImage().pixelColor(0, 0)
        self.assertGreater(displayed.red(), 128)
        self.assertGreater(displayed.green(), 128)
        self.assertGreater(displayed.blue(), 128)
        window.close()
        app.processEvents()

    def test_player_shows_current_frame_and_arrow_keys_step_sequence(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in range(1000, 1021):
                (root / f"shot_{frame:04d}.jpg").touch()

            window = MainWindow(use_media=False)
            window.input_edit.setText(str(root / "shot_%04d.jpg"))
            window._prepare_preview_source(root / "shot_1000.jpg", is_sequence=True)

            window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier))
            self.assertEqual(window.sequence_frame_index, 1)
            self.assertEqual(window.current_frame_label.text(), "Frame: 1001")

            window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier))
            self.assertEqual(window.sequence_frame_index, 11)
            self.assertEqual(window.current_frame_label.text(), "Frame: 1011")

            window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier))
            self.assertEqual(window.sequence_frame_index, 10)
            self.assertEqual(window.current_frame_label.text(), "Frame: 1010")

            window.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
            self.assertEqual(window.sequence_frame_index, 0)
            self.assertEqual(window.current_frame_label.text(), "Frame: 1000")

        window.close()
        app.processEvents()

    def test_switching_from_sequence_to_single_exr_clears_old_preview_pixmap(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jpg = root / "shot_1000.jpg"
            jpg.write_bytes(bytes.fromhex("47494638396101000100800000ff00000000002c00000000010001000002024401003b"))
            exr = root / "shot_1000.exr"
            exr.write_text("not a real exr", encoding="utf-8")

            window = MainWindow(use_media=True)
            window.input_edit.setText(str(root / "shot_%04d.jpg"))
            window._prepare_preview_source(jpg, is_sequence=True)
            self.assertIsNotNone(window.preview_placeholder.pixmap())

            window.input_edit.setText(str(exr))
            window._prepare_preview_source(exr, is_sequence=False)

        pixmap = window.preview_placeholder.pixmap()
        self.assertTrue(pixmap is None or pixmap.isNull())
        self.assertEqual(window.preview_placeholder.text(), "shot_1000.exr")
        self.assertFalse(window.play_button.isEnabled())
        self.assertIsNone(window.player)
        window.close()
        app.processEvents()

    def test_sequence_frame_mode_set_in_uses_frame_index_not_slider_milliseconds(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in range(1000, 1012):
                (root / f"shot_{frame}.jpg").touch()

            window = MainWindow(use_media=False)
            window.current_fps = 25.0
            window.input_edit.setText(str(root / "shot_%04d.jpg"))
            window._prepare_preview_source(root / "shot_1000.jpg", is_sequence=True)
            window.time_mode_combo.setCurrentIndex(1)
            window.show_sequence_frame(10)
            window.set_in_point()

        self.assertEqual(window.in_edit.text(), "10")
        self.assertEqual(window.position_slider.in_marker_ms, 400)
        window.close()
        app.processEvents()

    def test_sequence_seconds_mode_set_out_converts_frame_index_to_time(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for frame in range(1000, 1012):
                (root / f"shot_{frame}.jpg").touch()

            window = MainWindow(use_media=False)
            window.current_fps = 25.0
            window.input_edit.setText(str(root / "shot_%04d.jpg"))
            window._prepare_preview_source(root / "shot_1000.jpg", is_sequence=True)
            window.show_sequence_frame(10)
            window.set_out_point()

        self.assertEqual(window.out_edit.text(), "00:00:00.400")
        self.assertEqual(window.position_slider.out_marker_ms, 400)
        window.close()
        app.processEvents()

    def test_default_output_path_removes_input_sequence_pattern_for_video_output(self):
        output = _default_output_path(Path("/tmp/DSCF0358_%04d.jpg"), get_preset("h264_mp4"))

        self.assertEqual(output, "/tmp/DSCF0358_h264_mp4.mp4")

    def test_refresh_output_path_removes_sequence_pattern_for_file_output(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.input_edit.setText("/tmp/DSCF0358_%04d.jpg")
        window.output_edit.setText("/tmp/DSCF0358.%04d.exr")
        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("mp4"))
        window.refresh_output_path_extension()

        self.assertEqual(window.output_edit.text(), "/tmp/DSCF0358.mp4")
        window.close()
        app.processEvents()

    def test_mp4_h265_selection_builds_h265_preset(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("mp4"))
        window.codec_combo.setCurrentIndex(window.codec_combo.findData("h265"))
        preset = window.current_preset()
        self.assertEqual(preset.output["extension"], "mp4")
        self.assertEqual(preset.video["codec"], "libx265")
        window.close()
        app.processEvents()

    def test_file_to_sequence_output_hides_fps_and_keeps_source_filter(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("png"))
        window.fps_edit.setText("24")
        preset = window.current_preset()
        self.assertTrue(window.fps_edit.isHidden())
        self.assertEqual(preset.output["extension"], "png")
        self.assertEqual(preset.filters["fps"], "source")
        window.close()
        app.processEvents()

    def test_png_defaults_to_rgb_8bit_and_offers_16bit_profiles(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("png"))

        labels = [window.codec_profile_combo.itemText(index) for index in range(window.codec_profile_combo.count())]
        preset = window.current_preset()

        self.assertEqual(window.codec_profile_label.text(), "Color / Bit Depth")
        self.assertEqual(window.codec_profile_combo.currentData(), "rgb_8")
        self.assertEqual(labels, ["RGB 8-bit", "RGBA 8-bit", "RGB 16-bit", "RGBA 16-bit"])
        self.assertEqual(preset.video["pix_fmt"], "rgb24")

        window.codec_profile_combo.setCurrentIndex(window.codec_profile_combo.findData("rgb_16"))
        self.assertEqual(window.current_preset().video["pix_fmt"], "rgb48be")
        window.close()
        app.processEvents()

    def test_jpg_uses_quality_slider_0_to_100(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("jpg"))

        self.assertFalse(window.jpg_quality_slider.isHidden())
        self.assertEqual(window.jpg_quality_slider.minimum(), 0)
        self.assertEqual(window.jpg_quality_slider.maximum(), 100)
        self.assertEqual(window.jpg_quality_slider.value(), 90)

        self.assertEqual(window.current_preset().video["quality"], 5)
        window.jpg_quality_slider.setValue(100)
        self.assertEqual(window.current_preset().video["quality"], 2)
        window.jpg_quality_slider.setValue(0)
        self.assertEqual(window.current_preset().video["quality"], 31)
        window.close()
        app.processEvents()

    def test_sequence_input_to_file_output_shows_fps_and_applies_filter(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shot_1000.jpg").touch()
            (root / "shot_1001.jpg").touch()

            window = MainWindow(use_media=False)
            window.input_edit.setText(str(root / "shot_%04d.jpg"))
            window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("mp4"))
            window.fps_edit.setText("24")
            preset = window.current_preset()

        self.assertFalse(window.fps_edit.isHidden())
        self.assertEqual(preset.output["extension"], "mp4")
        self.assertEqual(preset.filters["fps"], 24.0)
        window.close()
        app.processEvents()

    def test_file_input_to_file_output_hides_fps_and_keeps_source_filter(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)
        window.input_edit.setText("/tmp/input.mov")
        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("mp4"))
        window.fps_edit.setText("24")
        preset = window.current_preset()

        self.assertTrue(window.fps_edit.isHidden())
        self.assertEqual(preset.filters["fps"], "source")
        window.close()
        app.processEvents()

    def test_new_file_types_build_expected_presets(self):
        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        window = MainWindow(use_media=False)

        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("gif"))
        gif_preset = window.current_preset()
        self.assertEqual(gif_preset.output["extension"], "gif")
        self.assertEqual(gif_preset.video["codec"], "gif")
        self.assertTrue(window.fps_edit.isHidden())

        window.file_type_combo.setCurrentIndex(window.file_type_combo.findData("targa"))
        targa_preset = window.current_preset()
        self.assertEqual(targa_preset.output["extension"], "tga")
        self.assertEqual(targa_preset.video["codec"], "targa")
        self.assertTrue(window.fps_edit.isHidden())

        window.close()
        app.processEvents()

    def test_positive_float_parser(self):
        self.assertEqual(_parse_positive_float("24"), 24.0)
        self.assertEqual(_parse_positive_float("23.976"), 23.976)
        self.assertIsNone(_parse_positive_float("0"))
        self.assertIsNone(_parse_positive_float("bad"))


if __name__ == "__main__":
    unittest.main()
