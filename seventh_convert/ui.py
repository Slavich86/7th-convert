from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QColorSpace, QImage, QKeyEvent, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .command_builder import ConvertJob, build_ffmpeg_args
from .converter import format_command, same_input_and_output, validate_job
from .exr_metadata import preserve_exr_metadata_for_job
from .ffprobe import duration_seconds, probe
from .presets import Preset, get_preset, load_presets
from .sequence import sequence_frames, sequence_groups, sequence_start_number, split_sequence_name

try:
    import PyOpenColorIO as ocio
except Exception:  # noqa: BLE001 - optional runtime dependency.
    ocio = None


STILL_IMAGE_EXTENSIONS = {
    ".exr",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".targa",
    ".tga",
    ".tif",
    ".tiff",
}


COLOR_TRANSFORM_OPTIONS = [
    ("None", "none"),
    ("sRGB", "srgb"),
    ("Linear", "linear"),
    ("Rec.709", "rec709"),
]

COLOR_WORKFLOW_BASIC = "basic"
COLOR_WORKFLOW_OCIO = "ocio"
OCIO_PREVIEW_MAX_SIZE = QSize(960, 960)

INPUT_COLOR_DEFAULT_BY_EXTENSION = {
    ".exr": "linear",
    ".gif": "srgb",
    ".jpeg": "srgb",
    ".jpg": "srgb",
    ".mov": "rec709",
    ".mp4": "rec709",
    ".png": "srgb",
    ".targa": "srgb",
    ".tga": "srgb",
}

OUTPUT_COLOR_DEFAULT_BY_FILE_TYPE = {
    "exr": "linear",
    "gif": "srgb",
    "jpg": "srgb",
    "mov": "rec709",
    "mp4": "rec709",
    "png": "srgb",
    "targa": "srgb",
}


OUTPUT_OPTIONS = {
    "exr": {
        "label": "exr",
        "extension": "exr",
        "sequence": True,
        "codecs": {
            "exr": {
                "label": "OpenEXR",
                "preset": "exr_sequence",
                "profile_label": "Compression",
                "profiles": {
                    "none": {"label": "none", "compression": "none"},
                    "zip1": {"label": "Zip (1 scanline)", "compression": "zip1"},
                    "zip16": {"label": "Zip (16 scanlines)", "compression": "zip16"},
                    "rle": {"label": "RLE", "compression": "rle"},
                },
                "default_profile": "zip1",
            }
        },
        "default_codec": "exr",
    },
    "gif": {
        "label": "gif",
        "extension": "gif",
        "codecs": {
            "gif": {
                "label": "GIF",
                "preset": "gif",
                "profiles": {
                    "standard": {"label": "Standard"},
                },
                "default_profile": "standard",
            }
        },
        "default_codec": "gif",
    },
    "png": {
        "label": "png",
        "extension": "png",
        "sequence": True,
        "codecs": {
            "png": {
                "label": "PNG",
                "preset": "png_sequence",
                "profile_label": "Color / Bit Depth",
                "profiles": {
                    "rgb_8": {"label": "RGB 8-bit", "pix_fmt": "rgb24"},
                    "rgba_8": {"label": "RGBA 8-bit", "pix_fmt": "rgba"},
                    "rgb_16": {"label": "RGB 16-bit", "pix_fmt": "rgb48be"},
                    "rgba_16": {"label": "RGBA 16-bit", "pix_fmt": "rgba64be"},
                },
                "default_profile": "rgb_8",
            }
        },
        "default_codec": "png",
    },
    "jpg": {
        "label": "jpg",
        "extension": "jpg",
        "sequence": True,
        "codecs": {
            "jpg": {
                "label": "JPEG",
                "preset": "jpg_sequence",
                "profiles": {
                    "standard": {"label": "Standard"},
                },
                "default_profile": "standard",
            }
        },
        "default_codec": "jpg",
    },
    "mov": {
        "label": "mov",
        "extension": "mov",
        "codecs": {
            "apple_prores": {
                "label": "Apple ProRes",
                "preset": "prores_hq_mov",
                "profiles": {
                    "proxy": {"label": "ProRes 422 Proxy", "profile": "proxy", "pix_fmt": "yuv422p10le"},
                    "lt": {"label": "ProRes 422 LT", "profile": "lt", "pix_fmt": "yuv422p10le"},
                    "standard": {"label": "ProRes 422 10-bit", "profile": "standard", "pix_fmt": "yuv422p10le"},
                    "hq": {"label": "ProRes 422 HQ 10-bit", "profile": "hq", "pix_fmt": "yuv422p10le"},
                    "4444": {"label": "ProRes 4444", "profile": "4444", "pix_fmt": "yuva444p10le"},
                    "4444xq": {"label": "ProRes 4444 XQ", "profile": "4444xq", "pix_fmt": "yuva444p10le"},
                },
                "default_profile": "hq",
            }
        },
        "default_codec": "apple_prores",
    },
    "mp4": {
        "label": "mp4",
        "extension": "mp4",
        "codecs": {
            "h264": {
                "label": "H.264",
                "preset": "h264_mp4",
                "profiles": {
                    "high": {"label": "High", "profile": "high", "pix_fmt": "yuv420p"},
                    "main": {"label": "Main", "profile": "main", "pix_fmt": "yuv420p"},
                    "baseline": {"label": "Baseline", "profile": "baseline", "pix_fmt": "yuv420p"},
                },
                "default_profile": "high",
            },
            "h265": {
                "label": "H.265",
                "preset": "h265_mp4",
                "profiles": {
                    "main": {"label": "Main", "profile": None, "pix_fmt": "yuv420p"},
                    "main10": {"label": "Main 10", "profile": None, "pix_fmt": "yuv420p10le"},
                },
                "default_profile": "main",
            },
        },
        "default_codec": "h264",
    },
    "targa": {
        "label": "targa",
        "extension": "tga",
        "sequence": True,
        "codecs": {
            "targa": {
                "label": "Targa",
                "preset": "targa_sequence",
                "profiles": {
                    "rgba": {"label": "RGBA", "pix_fmt": "bgra"},
                    "rgb": {"label": "RGB", "pix_fmt": "rgb24"},
                },
                "default_profile": "rgba",
            }
        },
        "default_codec": "targa",
    },
    "wav": {
        "label": "wav",
        "extension": "wav",
        "codecs": {
            "pcm": {
                "label": "PCM",
                "preset": "wav_pcm",
                "profiles": {
                    "pcm_s24le": {"label": "PCM 24-bit", "audio_codec": "pcm_s24le"},
                    "pcm_s16le": {"label": "PCM 16-bit", "audio_codec": "pcm_s16le"},
                },
                "default_profile": "pcm_s24le",
            }
        },
        "default_codec": "pcm",
    },
}


class TimelineSlider(QSlider):
    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(orientation, parent)
        self.in_marker_ms: int | None = None
        self.out_marker_ms: int | None = None
        self.marker_to_slider_value = None
        self.setMinimumHeight(22)

    def set_marker_value_mapper(self, mapper) -> None:  # noqa: ANN001 - stores UI callback.
        self.marker_to_slider_value = mapper
        self.update()

    def set_in_marker(self, value_ms: int | None) -> None:
        self.in_marker_ms = value_ms
        self.update()

    def set_out_marker(self, value_ms: int | None) -> None:
        self.out_marker_ms = value_ms
        self.update()

    def clear_markers(self) -> None:
        self.in_marker_ms = None
        self.out_marker_ms = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001 - Qt override signature.
        super().paintEvent(event)
        if self.maximum() <= self.minimum():
            return

        painter = QPainter(self)
        try:
            self._draw_marker(painter, self.in_marker_ms, QColor("#66d17a"))
            self._draw_marker(painter, self.out_marker_ms, QColor("#ff6b6b"))
        finally:
            painter.end()

    def _draw_marker(self, painter: QPainter, value_ms: int | None, color: QColor) -> None:
        if value_ms is None:
            return

        minimum = self.minimum()
        maximum = self.maximum()
        slider_value = self.marker_to_slider_value(value_ms) if self.marker_to_slider_value else value_ms
        value = max(minimum, min(maximum, slider_value))
        ratio = (value - minimum) / (maximum - minimum)
        margin = 8
        x = round(margin + ratio * max(self.width() - margin * 2, 1))

        painter.setPen(color)
        painter.setBrush(color)
        painter.drawLine(x, 3, x, self.height() - 3)
        painter.drawPolygon([QPoint(x - 5, 1), QPoint(x + 5, 1), QPoint(x, 7)])


class PreviewLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._source_pixmap = QPixmap()

    def set_source_pixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = QPixmap(pixmap)
        self.setText("")
        self._refresh_scaled_pixmap()

    def clear_source_pixmap(self) -> None:
        self._source_pixmap = QPixmap()
        super().setPixmap(QPixmap())

    def clear(self) -> None:
        self.clear_source_pixmap()
        super().clear()

    def resizeEvent(self, event) -> None:  # noqa: ANN001 - Qt override signature.
        super().resizeEvent(event)
        self._refresh_scaled_pixmap()

    def _refresh_scaled_pixmap(self) -> None:
        if self._source_pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        super().setPixmap(self._source_pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))


class ConvertWorker(QThread):
    progress_changed = Signal(float, str)
    log_line = Signal(str)
    finished_with_code = Signal(int)
    failed = Signal(str)

    def __init__(self, job: ConvertJob, log_path: Path | None = None) -> None:
        super().__init__()
        self.job = job
        self.log_path = log_path

    def run(self) -> None:
        try:
            validate_job(self.job)
            probe_json = probe(self.job.input)
            duration = duration_seconds(probe_json)
            args = build_ffmpeg_args(self.job)
            progress_args = args[:-1] + ["-progress", "pipe:1", "-nostats", args[-1]]
            self.log_line.emit("Command:")
            self.log_line.emit(format_command(progress_args))
            self.log_line.emit("")

            progress_duration = _progress_duration_for_job(self.job, duration)
            total_frames = _progress_total_frames_for_job(self.job)
            use_frame_progress = total_frames is not None
            self.log_line.emit("Settings:")
            self.log_line.emit(f"Input Transform: {_log_transform_value(self.job.preset.filters, 'input')}")
            self.log_line.emit(f"Output Transform: {_log_transform_value(self.job.preset.filters, 'output')}")
            if self.job.preset.filters.get("lut3d"):
                self.log_line.emit(f"OCIO LUT: {self.job.preset.filters['lut3d']}")
            if self.job.preset.filters.get("ocio_lut_method"):
                self.log_line.emit(f"OCIO LUT Method: {self.job.preset.filters['ocio_lut_method']}")
            self.log_line.emit(f"Progress Source: {'frames' if use_frame_progress else 'time'}")
            self.log_line.emit("")
            log_file = None
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                log_file = self.log_path.open("w", encoding="utf-8")
                log_file.write("Command:\n")
                log_file.write(format_command(progress_args))
                log_file.write("\n\nSettings:\n")
                log_file.write(f"Input Transform: {_log_transform_value(self.job.preset.filters, 'input')}\n")
                log_file.write(f"Output Transform: {_log_transform_value(self.job.preset.filters, 'output')}\n")
                if self.job.preset.filters.get("lut3d"):
                    log_file.write(f"OCIO LUT: {self.job.preset.filters['lut3d']}\n")
                if self.job.preset.filters.get("ocio_lut_method"):
                    log_file.write(f"OCIO LUT Method: {self.job.preset.filters['ocio_lut_method']}\n")
                log_file.write(f"Progress Source: {'frames' if use_frame_progress else 'time'}\n")
                log_file.write("\nOutput:\n")
                log_file.flush()

            started = time.monotonic()
            process = subprocess.Popen(
                progress_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            assert process.stdout is not None
            for line in process.stdout:
                if log_file:
                    log_file.write(line)
                    log_file.flush()
                self.log_line.emit(line.rstrip())

                key, _, value = line.strip().partition("=")
                current = _parse_progress_time(key, value)
                if current is not None and not use_frame_progress:
                    percent = _progress_percent(current, progress_duration)
                    elapsed = max(time.monotonic() - started, 0.001)
                    status = f"{current:.2f}s elapsed, {elapsed:.1f}s wall"
                    self.progress_changed.emit(percent, status)
                elif key == "frame" and total_frames:
                    try:
                        frame = int(value)
                    except ValueError:
                        continue
                    percent = _progress_percent(frame, float(total_frames))
                    status = f"{frame}/{total_frames} frames"
                    self.progress_changed.emit(percent, status)
                elif key == "progress" and value == "end":
                    self.progress_changed.emit(100.0, "Finished")

            return_code = process.wait()
            if return_code == 0:
                metadata_result = preserve_exr_metadata_for_job(self.job)
                if metadata_result.enabled:
                    message = f"EXR metadata: {metadata_result.message}"
                    if metadata_result.skipped_frames:
                        message += f", skipped {metadata_result.skipped_frames} frame(s)"
                    self.log_line.emit(message)
                    if log_file:
                        log_file.write(f"{message}\n")
                        log_file.flush()
            if log_file:
                log_file.close()
            self.finished_with_code.emit(return_code)
        except Exception as exc:  # noqa: BLE001 - worker reports concise UI errors.
            log_file = locals().get("log_file")
            if log_file:
                log_file.close()
            self.failed.emit(str(exc))


@dataclass(frozen=True)
class SelectedInput:
    input_path: Path
    preview_path: Path
    is_sequence: bool = False
    sequence_start: int | None = None
    sequence_end: int | None = None
    sequence_frame_count: int | None = None
    sequence_start_text: str | None = None
    sequence_end_text: str | None = None


@dataclass(frozen=True)
class InputListItem:
    label: str
    input_path: Path
    preview_path: Path
    is_sequence: bool
    is_directory: bool = False
    sequence_start: int | None = None
    sequence_end: int | None = None
    sequence_frame_count: int | None = None
    sequence_start_text: str | None = None
    sequence_end_text: str | None = None


class SequenceFileDialog(QDialog):
    def __init__(self, parent: QWidget, start_dir: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open media file")
        self.resize(760, 520)
        self.selected_input: SelectedInput | None = None
        self.current_dir = start_dir if start_dir and start_dir.exists() else Path.cwd()

        layout = QVBoxLayout(self)

        path_row = QHBoxLayout()
        self.dir_edit = QLineEdit(str(self.current_dir))
        self.dir_edit.editingFinished.connect(self.refresh_list)
        icon_provider = QFileIconProvider()
        self.up_button = QPushButton("↑")
        self.up_button.setIcon(icon_provider.icon(QFileIconProvider.IconType.Folder))
        self.up_button.setFixedWidth(42)
        self.up_button.clicked.connect(self.go_up)
        self.seq_check = QCheckBox("seq")
        self.seq_check.toggled.connect(self.refresh_list)
        path_row.addWidget(QLabel("Folder"))
        path_row.addWidget(self.dir_edit, stretch=1)
        path_row.addWidget(self.up_button)
        path_row.addWidget(self.seq_check)
        layout.addLayout(path_row)

        browser_row = QHBoxLayout()
        self.places_list = QListWidget()
        self.places_list.setMaximumWidth(220)
        self.places_list.itemClicked.connect(self.go_to_place)
        self.file_list = QListWidget()
        self.file_list.itemDoubleClicked.connect(lambda _item: self.accept_selection())
        browser_row.addWidget(self.places_list)
        browser_row.addWidget(self.file_list, stretch=1)
        layout.addLayout(browser_row, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.refresh_places()
        self.refresh_list()

    @staticmethod
    def get_input(parent: QWidget, start_dir: Path | None = None) -> SelectedInput | None:
        dialog = SequenceFileDialog(parent, start_dir)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_input

    def go_up(self) -> None:
        self.set_current_dir(self.current_dir.parent)

    def go_to_place(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, Path):
            self.set_current_dir(path)

    def set_current_dir(self, directory: Path) -> None:
        if not directory.exists() or not directory.is_dir():
            return
        self.current_dir = directory
        self.dir_edit.setText(str(self.current_dir))
        self.refresh_list()

    def refresh_places(self) -> None:
        self.places_list.clear()
        for label, path in _navigation_places():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.places_list.addItem(item)

    def refresh_list(self) -> None:
        directory = Path(self.dir_edit.text()).expanduser()
        if directory.exists() and directory.is_dir():
            self.current_dir = directory

        items = _input_list_items(self.current_dir, self.seq_check.isChecked())
        self.file_list.clear()
        for item in items:
            list_item = QListWidgetItem(item.label)
            if item.is_directory:
                list_item.setIcon(QFileIconProvider().icon(QFileIconProvider.IconType.Folder))
            list_item.setData(Qt.ItemDataRole.UserRole, item)
            self.file_list.addItem(list_item)

    def accept_selection(self) -> None:
        current = self.file_list.currentItem()
        if current is None:
            return
        item = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item, InputListItem):
            return
        if item.is_directory:
            self.set_current_dir(item.input_path)
            return
        self.selected_input = SelectedInput(
            item.input_path,
            item.preview_path,
            item.is_sequence,
            item.sequence_start,
            item.sequence_end,
            item.sequence_frame_count,
            item.sequence_start_text,
            item.sequence_end_text,
        )
        self.accept()


def _input_list_items(directory: Path, seq_mode: bool) -> list[InputListItem]:
    directories = _directory_items(directory)
    if seq_mode:
        return directories + [
            InputListItem(label, pattern, preview, True, False, start, end, count, start_text, end_text)
            for label, pattern, preview, count, start, end, start_text, end_text in sequence_groups(directory)
        ]

    if not directory.exists() or not directory.is_dir():
        return []

    items = directories
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        items.append(InputListItem(path.name, path, path, False))
    return items


def _directory_items(directory: Path) -> list[InputListItem]:
    if not directory.exists() or not directory.is_dir():
        return []
    items = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if path.is_dir():
            items.append(InputListItem(path.name, path, path, False, True))
    return items


def _navigation_places() -> list[tuple[str, Path]]:
    home = Path.home()
    places: list[tuple[str, Path]] = [("Home", home), ("Root", Path("/"))]
    for base in [Path("/mnt"), Path("/media"), Path("/run/media") / os.environ.get("USER", "")]:
        if base.exists():
            places.append((str(base), base))
            for child in sorted(base.iterdir(), key=lambda item: item.name.lower()):
                if child.is_dir():
                    places.append((child.name, child))

    seen = set()
    unique = []
    for label, path in places:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append((label, path))
    return unique


def _load_ocio_color_spaces(config_path: str) -> tuple[list[str], str]:
    if not config_path.strip():
        return [], "No OCIO config selected"
    path = Path(config_path).expanduser()
    if not path.exists() or not path.is_file():
        return [], f"OCIO config not found: {path}"
    if ocio is None:
        return [], "PyOpenColorIO is not installed"
    try:
        config = ocio.Config.CreateFromFile(str(path))
        color_spaces = [
            color_space.getName()
            for color_space in config.getColorSpaces()
            if not color_space.isData()
        ]
    except Exception as exc:  # noqa: BLE001 - user-facing config validation.
        return [], f"OCIO config error: {exc}"
    if not color_spaces:
        return [], "OCIO config has no color spaces"
    return color_spaces, f"Loaded {len(color_spaces)} color spaces"


class PreferencesDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        workflow: str,
        ocio_config_path: str,
        ocio_status: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        layout = QVBoxLayout(self)

        color_group = QGroupBox("Color Management")
        form = QFormLayout(color_group)

        self.workflow_combo = QComboBox()
        self.workflow_combo.addItem("Basic", COLOR_WORKFLOW_BASIC)
        self.workflow_combo.addItem("OCIO", COLOR_WORKFLOW_OCIO)
        self._set_combo_data(self.workflow_combo, workflow)
        form.addRow("Workflow", self.workflow_combo)

        path_row = QHBoxLayout()
        self.ocio_config_edit = QLineEdit(ocio_config_path)
        self.ocio_config_edit.setPlaceholderText("Choose config.ocio")
        self.ocio_browse_button = QPushButton("Browse")
        self.ocio_browse_button.clicked.connect(self.browse_ocio_config)
        path_row.addWidget(self.ocio_config_edit, stretch=1)
        path_row.addWidget(self.ocio_browse_button)
        form.addRow("OCIO Config", path_row)

        self.ocio_status_label = QLabel(ocio_status)
        self.ocio_status_label.setWordWrap(True)
        form.addRow("Status", self.ocio_status_label)

        self.workflow_combo.currentIndexChanged.connect(lambda _index: self.refresh_status())
        self.ocio_config_edit.textChanged.connect(lambda _text: self.refresh_status())
        layout.addWidget(color_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_status()

    def selected_workflow(self) -> str:
        return str(self.workflow_combo.currentData())

    def selected_ocio_config_path(self) -> str:
        return self.ocio_config_edit.text().strip()

    def browse_ocio_config(self) -> None:
        start = self.selected_ocio_config_path()
        start_path = str(Path(start).expanduser().parent) if start else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose OCIO config",
            start_path,
            "OCIO config (*.ocio);;All files (*)",
        )
        if path:
            self.ocio_config_edit.setText(path)

    def refresh_status(self) -> None:
        if self.selected_workflow() != COLOR_WORKFLOW_OCIO:
            self.ocio_status_label.setText("Basic workflow uses built-in transforms")
            return
        _color_spaces, status = _load_ocio_color_spaces(self.selected_ocio_config_path())
        self.ocio_status_label.setText(status)

    def _set_combo_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)


class MainWindow(QMainWindow):
    def __init__(self, use_media: bool | None = None, settings: QSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle("7th Convert")
        self.resize(1180, 760)

        self.presets = load_presets()
        self.settings = settings or QSettings("7th Convert", "7th Convert")
        self.color_workflow = str(self.settings.value("color/workflow", COLOR_WORKFLOW_BASIC))
        self.ocio_config_path = str(self.settings.value("ocio/config_path", ""))
        self.ocio_color_spaces: list[str] = []
        self.ocio_status = "No OCIO config selected"
        self.reload_ocio_config()
        self.current_probe_json: dict | None = None
        self.current_worker: ConvertWorker | None = None
        self.use_media = os.environ.get("QT_QPA_PLATFORM") != "offscreen" if use_media is None else use_media
        self.current_fps = 25.0

        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None
        self.video_widget: QVideoWidget | None = None
        self.video_sink: QVideoSink | None = None
        self._last_video_source_pixmap = QPixmap()
        self.preview_source_path: Path | None = None
        self.preview_is_sequence = False
        self.current_input_is_sequence = False
        self.current_input_sequence_start: int | None = None
        self.current_input_sequence_end: int | None = None
        self.current_input_sequence_frame_count: int | None = None
        self.current_input_sequence_start_text: str | None = None
        self.current_input_sequence_end_text: str | None = None
        self.sequence_timer = QTimer(self)
        self.sequence_timer.timeout.connect(self.advance_sequence_frame)
        self.sequence_preview_frames: list[Path] = []
        self.sequence_frame_index = 0

        self.main_widget = QWidget()
        self.main_layout = QVBoxLayout(self.main_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs, stretch=1)
        self.main_layout.addWidget(self._build_status_panel())
        self.setCentralWidget(self.main_widget)

        self._build_convert_tab()
        self._build_media_info_tab()
        self._build_queue_tab()
        self._build_logs_tab()
        self._build_menu()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("Open Input", self)
        open_action.triggered.connect(self.browse_input)
        file_menu.addAction(open_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        preferences_action = QAction("Preferences", self)
        preferences_action.triggered.connect(self.open_preferences)
        edit_menu.addAction(preferences_action)

    def reload_ocio_config(self) -> None:
        if self.color_workflow != COLOR_WORKFLOW_OCIO:
            self.ocio_color_spaces = []
            self.ocio_status = "Basic workflow uses built-in transforms"
            return
        self.ocio_color_spaces, self.ocio_status = _load_ocio_color_spaces(self.ocio_config_path)

    def ocio_workflow_is_active(self) -> bool:
        return self.color_workflow == COLOR_WORKFLOW_OCIO and bool(self.ocio_color_spaces)

    def open_preferences(self) -> None:
        dialog = PreferencesDialog(
            self,
            workflow=self.color_workflow,
            ocio_config_path=self.ocio_config_path,
            ocio_status=self.ocio_status,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.apply_color_preferences(dialog.selected_workflow(), dialog.selected_ocio_config_path())

    def apply_color_preferences(self, workflow: str, ocio_config_path: str) -> None:
        self.color_workflow = workflow if workflow in {COLOR_WORKFLOW_BASIC, COLOR_WORKFLOW_OCIO} else COLOR_WORKFLOW_BASIC
        self.ocio_config_path = ocio_config_path
        self.settings.setValue("color/workflow", self.color_workflow)
        self.settings.setValue("ocio/config_path", self.ocio_config_path)
        self.reload_ocio_config()
        self.refresh_color_transform_options()
        self.refresh_color_transform_defaults()
        self.refresh_preview_display_transform()

    def _build_convert_tab(self) -> None:
        tab = QWidget()
        root = QVBoxLayout(tab)

        input_row = QGridLayout()
        self.input_edit = QLineEdit()
        self.input_range_edit = QLineEdit()
        self.input_range_edit.setReadOnly(True)
        self.input_range_edit.setPlaceholderText("No sequence range selected")
        self.input_browse_button = QPushButton("Browse")
        self.input_browse_button.clicked.connect(self.browse_input)
        input_row.addWidget(QLabel("Input"), 0, 0)
        input_row.addWidget(self.input_edit, 0, 1)
        input_row.addWidget(self.input_browse_button, 0, 2)
        input_row.addWidget(QLabel("Range"), 1, 0)
        input_row.addWidget(self.input_range_edit, 1, 1, 1, 2)
        root.addLayout(input_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_player_panel())
        splitter.addWidget(self._build_settings_panel())
        splitter.setSizes([700, 420])
        root.addWidget(splitter, stretch=1)

        action_row = QHBoxLayout()
        self.build_button = QPushButton("Copy Command")
        self.build_button.clicked.connect(self.copy_command)
        self.convert_button = QPushButton("Convert Now")
        self.convert_button.clicked.connect(self.convert_now)
        action_row.addWidget(self.build_button)
        action_row.addWidget(self.convert_button)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self.tabs.addTab(tab, "Convert")

    def _build_status_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("GlobalStatusPanel")
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(8, 4, 8, 4)
        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("Idle")
        layout.addWidget(QLabel("Progress"))
        layout.addWidget(self.progress_bar, stretch=1)
        layout.addWidget(self.progress_label)
        return panel

    def _build_player_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.preview_container = QWidget()
        self.preview_container.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.preview_layout = QVBoxLayout(self.preview_container)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_placeholder = PreviewLabel("Open a media file to preview")
        self.preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_placeholder.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.preview_placeholder.setMinimumHeight(320)
        self.preview_placeholder.setStyleSheet("background: #111; color: #bbb;")
        self.preview_layout.addWidget(self.preview_placeholder)
        layout.addWidget(self.preview_container, stretch=1)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)
        self.position_slider = TimelineSlider(Qt.Orientation.Horizontal)
        self.play_button.setEnabled(False)
        self.position_slider.setEnabled(False)
        self.position_slider.sliderMoved.connect(self.seek_preview_position)
        self.position_slider.set_marker_value_mapper(self._ms_to_slider_value)
        self.current_frame_label = QLabel("Frame: -")
        self.current_frame_label.setMinimumWidth(92)
        controls.addWidget(self.play_button)
        controls.addWidget(self.position_slider, stretch=1)
        controls.addWidget(self.current_frame_label)
        layout.addLayout(controls)

        range_row = QHBoxLayout()
        self.time_mode_combo = QComboBox()
        self.time_mode_combo.addItem("Seconds", "seconds")
        self.time_mode_combo.addItem("Frames", "frames")
        self.time_mode_combo.currentIndexChanged.connect(lambda _index: self.switch_time_display_mode())
        self.in_edit = QLineEdit()
        self.in_edit.setPlaceholderText("00:00:00.000")
        self.in_edit.editingFinished.connect(self.update_range_markers_from_edits)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("00:00:10.000")
        self.out_edit.editingFinished.connect(self.update_range_markers_from_edits)
        self.set_in_button = QPushButton("Set In")
        self.set_in_button.clicked.connect(self.set_in_point)
        self.set_out_button = QPushButton("Set Out")
        self.set_out_button.clicked.connect(self.set_out_point)
        self.clear_range_button = QPushButton("Clear")
        self.clear_range_button.clicked.connect(self.clear_range)
        range_row.addWidget(self.time_mode_combo)
        range_row.addWidget(QLabel("In"))
        range_row.addWidget(self.in_edit)
        range_row.addWidget(self.set_in_button)
        range_row.addWidget(QLabel("Out"))
        range_row.addWidget(self.out_edit)
        range_row.addWidget(self.set_out_button)
        range_row.addWidget(self.clear_range_button)
        layout.addLayout(range_row)
        for widget in (panel, self.preview_container, self.preview_placeholder, self.play_button, self.position_slider):
            widget.installEventFilter(self)
        return panel

    def _build_settings_panel(self) -> QWidget:
        group = QGroupBox("Conversion Settings")
        layout = QFormLayout(group)

        self.file_type_combo = QComboBox()
        for key, option in OUTPUT_OPTIONS.items():
            self.file_type_combo.addItem(option["label"], key)
        self.file_type_combo.currentIndexChanged.connect(lambda _index: self.refresh_output_controls())
        layout.addRow("File Type", self.file_type_combo)

        self.codec_combo = QComboBox()
        self.codec_combo.currentIndexChanged.connect(lambda _index: self.refresh_profile_controls())
        layout.addRow("Codec", self.codec_combo)

        self.fps_label = QLabel("FPS")
        self.fps_edit = QLineEdit("24")
        self.fps_edit.setPlaceholderText("24")
        layout.addRow(self.fps_label, self.fps_edit)

        self.codec_profile_label = QLabel("Codec Profile")
        self.codec_profile_combo = QComboBox()
        layout.addRow(self.codec_profile_label, self.codec_profile_combo)

        self.jpg_quality_label = QLabel("JPG Quality")
        quality_row = QHBoxLayout()
        self.jpg_quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.jpg_quality_slider.setRange(0, 100)
        self.jpg_quality_slider.setValue(90)
        self.jpg_quality_value_label = QLabel("90")
        self.jpg_quality_value_label.setMinimumWidth(28)
        self.jpg_quality_slider.valueChanged.connect(lambda value: self.jpg_quality_value_label.setText(str(value)))
        quality_row.addWidget(self.jpg_quality_slider, stretch=1)
        quality_row.addWidget(self.jpg_quality_value_label)
        layout.addRow(self.jpg_quality_label, quality_row)

        self.input_transform_combo = QComboBox()
        self.output_transform_combo = QComboBox()
        self.input_transform_combo.currentIndexChanged.connect(lambda _index: self.refresh_preview_display_transform())
        layout.addRow("Input Transform", self.input_transform_combo)
        layout.addRow("Output Transform", self.output_transform_combo)
        self.refresh_color_transform_options()

        self.output_edit = QLineEdit()
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_button = QPushButton("Browse")
        output_button.clicked.connect(self.browse_output)
        output_row.addWidget(output_button)
        layout.addRow("Output", output_row)

        self.overwrite_check = QCheckBox("Overwrite existing output")
        layout.addRow("", self.overwrite_check)

        self.summary_text = QPlainTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMinimumHeight(180)
        layout.addRow("Summary", self.summary_text)

        self.refresh_output_controls()
        return group

    def _build_media_info_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.media_info_text = QPlainTextEdit()
        self.media_info_text.setReadOnly(True)
        layout.addWidget(self.media_info_text)
        self.tabs.addTab(tab, "Media Info")

    def _build_queue_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.queue_table = QTableWidget(0, 5)
        self.queue_table.setHorizontalHeaderLabels(["Input", "Output", "Preset", "Status", "Progress"])
        self.queue_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.queue_table)
        self.tabs.addTab(tab, "Queue")

    def _build_logs_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        self.tabs.addTab(tab, "Logs")

    def browse_input(self) -> None:
        selected = SequenceFileDialog.get_input(self, self._input_dialog_start_dir())
        if not selected:
            return
        self.input_edit.setText(str(selected.input_path))
        self.current_input_is_sequence = selected.is_sequence
        self.current_input_sequence_start = selected.sequence_start
        self.current_input_sequence_end = selected.sequence_end
        self.current_input_sequence_frame_count = selected.sequence_frame_count
        self.current_input_sequence_start_text = selected.sequence_start_text
        self.current_input_sequence_end_text = selected.sequence_end_text
        self.input_range_edit.setText(self._selected_range_text())
        self._prepare_preview_source(selected.preview_path, selected.is_sequence)
        self.output_edit.setText(_default_output_path(
            selected.input_path,
            self.current_preset(),
            sequence_start=selected.sequence_start,
            sequence_end=selected.sequence_end,
            sequence_start_text=selected.sequence_start_text,
            sequence_end_text=selected.sequence_end_text,
        ))
        self.refresh_color_transform_defaults()
        self.refresh_fps_control_visibility()
        self.probe_input(selected.preview_path)

    def _selected_range_text(self) -> str:
        if not self.current_input_is_sequence:
            return ""
        start = self.current_input_sequence_start_text
        end = self.current_input_sequence_end_text
        count = self.current_input_sequence_frame_count
        if start and end and count:
            return f"{start}-{end}, {count} frames"
        return "Full sequence"

    def _input_dialog_start_dir(self) -> Path:
        text = self.input_edit.text().strip()
        if text:
            path = Path(text).expanduser()
            if path.parent.exists():
                return path.parent
        return Path.cwd()

    def browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Choose output file")
        if path:
            self.output_edit.setText(path)

    def probe_input(self, input_path: Path | None = None) -> None:
        input_path = input_path or Path(self.input_edit.text()).expanduser()
        try:
            self.current_probe_json = probe(input_path)
        except Exception as exc:  # noqa: BLE001 - concise UI message.
            QMessageBox.warning(self, "Analyze failed", str(exc))
            return

        raw = json.dumps(self.current_probe_json, indent=2, ensure_ascii=False)
        self.current_fps = _fps_from_probe(self.current_probe_json) or self.current_fps
        self.media_info_text.setPlainText(raw)
        self.summary_text.setPlainText(_media_summary(
            self.current_probe_json,
            input_path=Path(self.input_edit.text()).expanduser(),
            fps=self.current_fps,
            sequence_frame_count=self.current_input_sequence_frame_count,
        ))
        self._sync_video_timeline_from_probe()
        self.tabs.setCurrentIndex(0)

    def _sync_video_timeline_from_probe(self) -> None:
        if self.preview_is_sequence or self.sequence_preview_frames or not self.preview_source_path:
            return
        if not self.use_media or self.current_probe_json is None:
            return
        duration = duration_seconds(self.current_probe_json)
        if duration is None or duration <= 0:
            return
        self.position_slider.setMaximum(round(duration * 1000))
        self.position_slider.setEnabled(True)

    def refresh_output_controls(self) -> None:
        file_type = self.selected_file_type()
        option = OUTPUT_OPTIONS[file_type]
        previous_codec = self.codec_combo.currentData()

        self.codec_combo.blockSignals(True)
        self.codec_combo.clear()
        for codec_key, codec_option in option["codecs"].items():
            self.codec_combo.addItem(codec_option["label"], codec_key)
        default_codec = previous_codec if previous_codec in option["codecs"] else option["default_codec"]
        self.codec_combo.setCurrentIndex(max(self.codec_combo.findData(default_codec), 0))
        self.codec_combo.blockSignals(False)

        self.refresh_profile_controls()
        self.refresh_fps_control_visibility()
        self.refresh_jpg_quality_visibility()
        self.refresh_output_path_extension()
        self.refresh_color_transform_defaults()

    def refresh_profile_controls(self) -> None:
        file_type = self.selected_file_type()
        codec = self.selected_codec()
        codec_option = OUTPUT_OPTIONS[file_type]["codecs"][codec]
        previous_profile = self.codec_profile_combo.currentData()

        self.codec_profile_combo.blockSignals(True)
        self.codec_profile_combo.clear()
        self.codec_profile_label.setText(codec_option.get("profile_label", "Codec Profile"))
        for profile_key, profile_option in codec_option["profiles"].items():
            self.codec_profile_combo.addItem(profile_option["label"], profile_key)
            if profile_option.get("enabled", True) is False:
                item = self.codec_profile_combo.model().item(self.codec_profile_combo.count() - 1)
                if item:
                    item.setEnabled(False)
        default_profile = previous_profile if previous_profile in codec_option["profiles"] else codec_option["default_profile"]
        self.codec_profile_combo.setCurrentIndex(max(self.codec_profile_combo.findData(default_profile), 0))
        self.codec_profile_combo.blockSignals(False)

    def refresh_output_path_extension(self) -> None:
        if not self.input_edit.text().strip():
            return
        current_output = self.output_edit.text().strip()
        if not current_output:
            self.output_edit.setText(_default_output_path(Path(self.input_edit.text()), self.current_preset()))
            return
        output_path = Path(current_output)
        extension = self.current_preset().output.get("extension", output_path.suffix.lstrip("."))
        input_stem = _clean_sequence_stem(Path(self.input_edit.text()).expanduser().stem)
        if (
            not self.current_preset().output.get("requires_pattern")
            and self.current_input_sequence_start is not None
            and self.current_input_sequence_end is not None
        ):
            input_stem = _stem_with_sequence_range(
                input_stem,
                self.current_input_sequence_start,
                self.current_input_sequence_end,
                self.current_input_sequence_start_text,
                self.current_input_sequence_end_text,
            )
        if self.current_preset().output.get("requires_pattern"):
            stem = input_stem if self.current_input_sequence_start is not None else output_path.stem.split(".")[0]
            self.output_edit.setText(str(output_path.with_name(f"{stem}.%04d.{extension}")))
        else:
            stem = input_stem if self.current_input_sequence_start is not None else _clean_sequence_stem(output_path.stem)
            self.output_edit.setText(str(output_path.with_name(f"{stem}.{extension}")))

    def selected_file_type(self) -> str:
        return str(self.file_type_combo.currentData())

    def selected_codec(self) -> str:
        return str(self.codec_combo.currentData())

    def selected_profile(self) -> str:
        return str(self.codec_profile_combo.currentData())

    def selected_input_transform(self) -> str:
        return str(self.input_transform_combo.currentData())

    def selected_output_transform(self) -> str:
        return str(self.output_transform_combo.currentData())

    def refresh_color_transform_options(self) -> None:
        previous_input = self.input_transform_combo.currentData()
        previous_output = self.output_transform_combo.currentData()

        self.input_transform_combo.blockSignals(True)
        self.output_transform_combo.blockSignals(True)
        self.input_transform_combo.clear()
        self.output_transform_combo.clear()

        if self.ocio_workflow_is_active():
            for color_space in self.ocio_color_spaces:
                value = f"ocio:{color_space}"
                self.input_transform_combo.addItem(color_space, value)
                self.output_transform_combo.addItem(color_space, value)
        else:
            for label, value in COLOR_TRANSFORM_OPTIONS:
                self.input_transform_combo.addItem(label, value)
                self.output_transform_combo.addItem(label, value)

        if previous_input:
            self._set_combo_data(self.input_transform_combo, str(previous_input))
        if previous_output:
            self._set_combo_data(self.output_transform_combo, str(previous_output))

        self.input_transform_combo.blockSignals(False)
        self.output_transform_combo.blockSignals(False)

    def refresh_color_transform_defaults(self) -> None:
        if self.ocio_workflow_is_active():
            return
        input_path = Path(self.input_edit.text()).expanduser() if self.input_edit.text().strip() else None
        input_default = _default_input_color_space(input_path)
        output_default = OUTPUT_COLOR_DEFAULT_BY_FILE_TYPE.get(self.selected_file_type(), "none")
        self._set_combo_data(self.input_transform_combo, input_default)
        self._set_combo_data(self.output_transform_combo, output_default)

    def _set_combo_data(self, combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def current_preset(self) -> Preset:
        file_type = self.selected_file_type()
        codec = self.selected_codec()
        profile = self.selected_profile()
        codec_option = OUTPUT_OPTIONS[file_type]["codecs"][codec]
        profile_option = codec_option["profiles"][profile]
        preset = deepcopy(get_preset(codec_option["preset"]))

        video = deepcopy(preset.video)
        audio = deepcopy(preset.audio)
        filters = deepcopy(preset.filters)
        output = deepcopy(preset.output)

        output["extension"] = OUTPUT_OPTIONS[file_type]["extension"]

        if "profile" in profile_option:
            if profile_option["profile"] is None:
                video.pop("profile", None)
            else:
                video["profile"] = profile_option["profile"]
        if "pix_fmt" in profile_option:
            video["pix_fmt"] = profile_option["pix_fmt"]
        if "compression" in profile_option:
            video["compression"] = profile_option["compression"]
        if "quality" in profile_option:
            video["quality"] = profile_option["quality"]
        if file_type == "jpg":
            video["quality"] = _jpeg_quality_percent_to_qscale(self.jpg_quality_slider.value())
        if "audio_codec" in profile_option:
            audio["codec"] = profile_option["audio_codec"]
        selected_input_transform = self.selected_input_transform()
        selected_output_transform = self.selected_output_transform()
        filters["input_color_space"] = _command_color_space_value(selected_input_transform)
        filters["output_color_space"] = _command_color_space_value(selected_output_transform)
        ocio_input_color_space = _ocio_color_space_name(selected_input_transform)
        ocio_output_color_space = _ocio_color_space_name(selected_output_transform)
        if ocio_input_color_space:
            filters["ocio_input_color_space"] = ocio_input_color_space
        if ocio_output_color_space:
            filters["ocio_output_color_space"] = ocio_output_color_space
        ocio_lut = _ocio_lut_path(
            selected_input_transform,
            selected_output_transform,
            self.ocio_config_path,
        )
        if ocio_lut:
            filters["lut3d"] = str(ocio_lut)
            filters["ocio_lut_method"] = "PyOpenColorIO Baker iridas_cube"
        elif _ocio_lut_is_required(selected_input_transform, selected_output_transform):
            filters["ocio_lut_error"] = "OCIO conversion selected, but LUT generation failed"
        if self.should_show_fps_control():
            fps = _parse_positive_float(self.fps_edit.text())
            filters["fps"] = fps if fps else "source"

        return Preset(
            id=f"{file_type}_{codec}_{profile}",
            name=f"{OUTPUT_OPTIONS[file_type]['label']} / {codec_option['label']} / {profile_option['label']}",
            group=preset.group,
            output=output,
            video=video,
            audio=audio,
            filters=filters,
        )

    def should_show_fps_control(self) -> bool:
        file_type = self.selected_file_type()
        output_is_sequence = bool(OUTPUT_OPTIONS[file_type].get("sequence"))
        return self.input_is_sequence() and not output_is_sequence

    def input_is_sequence(self) -> bool:
        if self.current_input_is_sequence:
            return True
        input_text = self.input_edit.text().strip()
        if not input_text:
            return False
        return sequence_start_number(Path(input_text).expanduser()) is not None

    def refresh_fps_control_visibility(self) -> None:
        show_fps = self.should_show_fps_control()
        self.fps_label.setVisible(show_fps)
        self.fps_edit.setVisible(show_fps)

    def refresh_jpg_quality_visibility(self) -> None:
        show_quality = self.selected_file_type() == "jpg"
        self.jpg_quality_label.setVisible(show_quality)
        self.jpg_quality_slider.setVisible(show_quality)
        self.jpg_quality_value_label.setVisible(show_quality)

    def build_job(self) -> ConvertJob:
        input_path = Path(self.input_edit.text()).expanduser()
        output_path = Path(self.output_edit.text()).expanduser()
        if not output_path:
            raise ValueError("Output path is required")
        if same_input_and_output(input_path, output_path):
            raise ValueError("Output path must be different from input path")
        preset = self.current_preset()
        if preset.filters.get("ocio_lut_error"):
            raise ValueError(str(preset.filters["ocio_lut_error"]))
        input_start_number = self.current_input_sequence_start
        if input_start_number is None:
            input_start_number = sequence_start_number(input_path)
        in_point, out_point = self._range_points_for_job()
        return ConvertJob(
            input=input_path,
            output=output_path,
            preset=preset,
            input_start_number=input_start_number,
            output_start_number=self._output_sequence_start_number(preset, input_start_number),
            in_point=in_point,
            out_point=out_point,
            overwrite=self.overwrite_check.isChecked(),
        )

    def _output_sequence_start_number(self, preset: Preset, input_start_number: int | None) -> int | None:
        if input_start_number is None or not preset.output.get("requires_pattern"):
            return None
        in_ms = self._edit_value_to_ms(self.in_edit.text().strip())
        if in_ms is None:
            return input_start_number
        return input_start_number + _ms_to_frame(in_ms, self.current_fps)

    def _range_points_for_job(self) -> tuple[str | None, str | None]:
        in_ms = self._edit_value_to_ms(self.in_edit.text().strip())
        out_ms = self._edit_value_to_ms(self.out_edit.text().strip())
        if in_ms == 0 and out_ms == 0:
            return None, None
        if out_ms == 0:
            out_ms = None
        if in_ms is not None and out_ms is not None and out_ms <= in_ms:
            raise ValueError("Out point must be greater than In point")
        return (
            _ms_to_time(in_ms) if in_ms is not None else None,
            _ms_to_time(out_ms) if out_ms is not None else None,
        )

    def copy_command(self) -> None:
        try:
            command = build_ffmpeg_args(self.build_job())
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Copy failed", str(exc))
            return
        QApplication.clipboard().setText(format_command(command))
        self.progress_label.setText("Command copied")

    def convert_now(self) -> None:
        try:
            job = self.build_job()
            build_ffmpeg_args(job)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Convert failed", str(exc))
            return

        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        for column, value in enumerate([str(job.input), str(job.output), job.preset.id, "Running", "0%"]):
            self.queue_table.setItem(row, column, QTableWidgetItem(value))

        log_path = Path("logs") / f"{job.output.stem}.log"
        self.progress_bar.setValue(0)
        self.progress_label.setText("Running")
        self.tabs.setCurrentIndex(3)

        self.current_worker = ConvertWorker(job, log_path)
        self.current_worker.progress_changed.connect(lambda value, status: self._update_progress(row, value, status))
        self.current_worker.log_line.connect(self.append_log)
        self.current_worker.finished_with_code.connect(lambda code: self._conversion_finished(row, code))
        self.current_worker.failed.connect(lambda message: self._conversion_failed(row, message))
        self.current_worker.start()

    def _update_progress(self, row: int, value: float, status: str) -> None:
        percent = max(0, min(100, int(value)))
        self.progress_bar.setValue(percent)
        self.progress_label.setText(status)
        self.queue_table.setItem(row, 4, QTableWidgetItem(f"{percent}%"))

    def _conversion_finished(self, row: int, code: int) -> None:
        status = "Finished" if code == 0 else f"Failed ({code})"
        self.progress_label.setText(status)
        self.queue_table.setItem(row, 3, QTableWidgetItem(status))
        self.append_log("")
        self.append_log(status)

    def _conversion_failed(self, row: int, message: str) -> None:
        self.progress_label.setText("Failed")
        self.queue_table.setItem(row, 3, QTableWidgetItem("Failed"))
        self.append_log(f"ERROR: {message}")
        QMessageBox.warning(self, "Convert failed", message)

    def append_log(self, line: str) -> None:
        self.log_text.appendPlainText(line)

    def _prepare_preview_source(self, path: Path, is_sequence: bool = False) -> None:
        self.sequence_timer.stop()
        self.sequence_preview_frames = []
        self.sequence_frame_index = 0
        self.preview_source_path = path
        self.preview_is_sequence = is_sequence
        self.position_slider.setValue(0)
        self.position_slider.setMaximum(0)
        self.position_slider.clear_markers()
        self.update_current_frame_label()
        self.play_button.setText("Play")

        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.deleteLater()
            self.player = None
            self.audio_output = None

        if self.video_widget:
            self.preview_layout.removeWidget(self.video_widget)
            self.video_widget.hide()
            self.video_widget.deleteLater()
            self.video_widget = None
        if self.video_sink:
            self.video_sink.deleteLater()
            self.video_sink = None
        self._last_video_source_pixmap = QPixmap()

        self.preview_placeholder.clear()
        self.preview_placeholder.setText("Ready to preview. Press Play.")
        self.preview_placeholder.show()
        if self.preview_layout.indexOf(self.preview_placeholder) == -1:
            self.preview_layout.addWidget(self.preview_placeholder)

        if is_sequence:
            self.sequence_preview_frames = sequence_frames(Path(self.input_edit.text()).expanduser())
            if not self.sequence_preview_frames:
                self.sequence_preview_frames = [path] if path.exists() else []
            self.position_slider.setMaximum(max(len(self.sequence_preview_frames) - 1, 0))
            self.position_slider.setEnabled(len(self.sequence_preview_frames) > 1)
            self.play_button.setEnabled(len(self.sequence_preview_frames) > 1)
            self.show_sequence_frame(0)
            return

        if _is_still_image_path(path):
            self.sequence_preview_frames = [path] if path.exists() else []
            self.position_slider.setEnabled(False)
            self.play_button.setEnabled(False)
            self.show_sequence_frame(0)
            return

        if not self.use_media:
            self.preview_placeholder.setText("Preview disabled in offscreen mode")
            return

        self.play_button.setEnabled(True)
        self._start_preview_source()

    def _start_preview_source(self) -> None:
        if not self.use_media or not self.preview_source_path:
            return
        if self.player:
            return

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.video_sink = QVideoSink(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoSink(self.video_sink)
        self.player.mediaStatusChanged.connect(self.handle_media_status)
        self.video_sink.videoFrameChanged.connect(self.handle_video_frame_changed)
        self.preview_placeholder.show()
        if self.preview_layout.indexOf(self.preview_placeholder) == -1:
            self.preview_layout.addWidget(self.preview_placeholder)

        self.player.positionChanged.connect(self.handle_player_position_changed)
        self.player.durationChanged.connect(self.position_slider.setMaximum)
        self.position_slider.setEnabled(True)

        self.player.setSource(QUrl.fromLocalFile(str(self.preview_source_path)))

    def handle_video_frame_changed(self, frame: QVideoFrame) -> None:
        image = frame.toImage()
        if image.isNull():
            return
        self._last_video_source_pixmap = QPixmap.fromImage(image)
        self._refresh_video_display_transform()

    def handle_player_position_changed(self, position_ms: int) -> None:
        self.position_slider.setValue(position_ms)
        self.update_current_frame_label()

    def toggle_playback(self) -> None:
        if self.preview_is_sequence:
            self.toggle_sequence_playback()
            return
        if not self.player and self.preview_source_path:
            self._start_preview_source()
        if not self.player:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_button.setText("Play")
        else:
            self.player.play()
            self.play_button.setText("Pause")

    def toggle_sequence_playback(self) -> None:
        if len(self.sequence_preview_frames) <= 1:
            return
        if self.sequence_timer.isActive():
            self.sequence_timer.stop()
            self.play_button.setText("Play")
            return
        interval_ms = max(round(1000 / (self.current_fps if self.current_fps > 0 else 25.0)), 1)
        self.sequence_timer.start(interval_ms)
        self.play_button.setText("Pause")

    def advance_sequence_frame(self) -> None:
        if not self.sequence_preview_frames:
            self.sequence_timer.stop()
            self.play_button.setText("Play")
            return
        next_index = self.sequence_frame_index + 1
        if next_index >= len(self.sequence_preview_frames):
            self.sequence_timer.stop()
            self.play_button.setText("Play")
            return
        self.show_sequence_frame(next_index)

    def seek_preview_position(self, value: int) -> None:
        if self.preview_is_sequence:
            self.show_sequence_frame(value)
        elif self.player:
            self.player.setPosition(value)
        else:
            if self.preview_source_path and self.use_media:
                self._start_preview_source()
            if self.player:
                self.player.setPosition(value)
            else:
                self.position_slider.setValue(value)
                self.update_current_frame_label()

    def show_sequence_frame(self, index: int) -> None:
        if not self.sequence_preview_frames:
            self.preview_placeholder.setText("Image sequence selected")
            self.update_current_frame_label()
            return
        self.sequence_frame_index = max(0, min(index, len(self.sequence_preview_frames) - 1))
        frame = self.sequence_preview_frames[self.sequence_frame_index]
        pixmap = QPixmap(str(frame))
        self.preview_placeholder.clear()
        if pixmap.isNull():
            self.preview_placeholder.setText(frame.name)
            self.update_current_frame_label()
            return
        pixmap = self._display_pixmap_for_selected_input_transform(pixmap)
        self.preview_placeholder.set_source_pixmap(pixmap)
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(self.sequence_frame_index)
        self.position_slider.blockSignals(False)
        self.update_current_frame_label()

    def refresh_preview_display_transform(self) -> None:
        if self.sequence_preview_frames:
            self.show_sequence_frame(self.sequence_frame_index)
        elif not self._last_video_source_pixmap.isNull():
            self._refresh_video_display_transform()

    def _refresh_video_display_transform(self) -> None:
        if self._last_video_source_pixmap.isNull():
            return
        pixmap = _display_pixmap_for_input_transform(
            self._last_video_source_pixmap,
            self.selected_input_transform(),
            self.ocio_config_path,
        )
        self.preview_placeholder.set_source_pixmap(pixmap)

    def _display_pixmap_for_selected_input_transform(self, pixmap: QPixmap) -> QPixmap:
        return _display_pixmap_for_input_transform(
            pixmap,
            self.selected_input_transform(),
            self.ocio_config_path,
        )

    def handle_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.reset_preview_after_finished()

    def reset_preview_after_finished(self) -> None:
        self.sequence_timer.stop()
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.deleteLater()
            self.player = None
            self.audio_output = None

        if self.video_widget:
            self.preview_layout.removeWidget(self.video_widget)
            self.video_widget.hide()
            self.video_widget.deleteLater()
            self.video_widget = None
        if self.video_sink:
            self.video_sink.deleteLater()
            self.video_sink = None
        self._last_video_source_pixmap = QPixmap()

        self.preview_placeholder.setText("Playback finished. Press Play to preview again.")
        self.preview_placeholder.show()
        if self.preview_layout.indexOf(self.preview_placeholder) == -1:
            self.preview_layout.addWidget(self.preview_placeholder)
        self.position_slider.setValue(0)
        self.update_current_frame_label()
        self.play_button.setText("Play")
        self.play_button.setEnabled(self.preview_source_path is not None and self.use_media)
        self.position_slider.setEnabled(False)

    def step_preview_frames(self, delta: int) -> None:
        if not self.preview_source_path or delta == 0:
            return
        if self.preview_is_sequence or self.sequence_preview_frames:
            target = self.sequence_frame_index + delta
            self.show_sequence_frame(target)
            return
        target_ms = self._current_position_ms() + _frame_to_ms(delta, self.current_fps)
        if self.position_slider.maximum() > 0:
            target_ms = min(target_ms, self.position_slider.maximum())
        target_ms = max(target_ms, 0)
        if self.player:
            self.player.setPosition(target_ms)
        else:
            self.position_slider.setValue(target_ms)
            self.update_current_frame_label()

    def update_current_frame_label(self) -> None:
        self.current_frame_label.setText(f"Frame: {self._current_frame_label_value()}")

    def _current_frame_label_value(self) -> str:
        if self.sequence_preview_frames:
            frame = self.sequence_preview_frames[max(0, min(self.sequence_frame_index, len(self.sequence_preview_frames) - 1))]
            frame_number = _frame_number_from_path(frame)
            if frame_number is not None:
                return str(frame_number)
            return str(self.sequence_frame_index)
        if self.preview_source_path:
            return str(_ms_to_frame(self._current_position_ms(), self.current_fps))
        return "-"

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._handle_preview_key(event):
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            return self._handle_preview_key(event)
        return super().eventFilter(watched, event)

    def _handle_preview_key(self, event: QKeyEvent) -> bool:
        key_steps = {
            Qt.Key.Key_Left: -1,
            Qt.Key.Key_Right: 1,
            Qt.Key.Key_Up: 10,
            Qt.Key.Key_Down: -10,
        }
        step = key_steps.get(event.key())
        if step is None:
            return False
        self.step_preview_frames(step)
        event.accept()
        return True

    def clear_range(self) -> None:
        self.in_edit.clear()
        self.out_edit.clear()
        self.position_slider.clear_markers()

    def set_in_point(self) -> None:
        value_ms = self._current_position_ms()
        self.in_edit.setText(self._ms_to_display_value(value_ms))
        self.position_slider.set_in_marker(value_ms)

    def set_out_point(self) -> None:
        value_ms = self._current_position_ms()
        self.out_edit.setText(self._ms_to_display_value(value_ms))
        self.position_slider.set_out_marker(value_ms)

    def update_range_markers_from_edits(self) -> None:
        self.position_slider.set_in_marker(self._edit_value_to_ms(self.in_edit.text().strip()))
        self.position_slider.set_out_marker(self._edit_value_to_ms(self.out_edit.text().strip()))

    def switch_time_display_mode(self) -> None:
        in_ms = self.position_slider.in_marker_ms
        out_ms = self.position_slider.out_marker_ms
        if in_ms is not None:
            self.in_edit.setText(self._ms_to_display_value(in_ms))
        if out_ms is not None:
            self.out_edit.setText(self._ms_to_display_value(out_ms))

    def _time_mode(self) -> str:
        return str(self.time_mode_combo.currentData())

    def _current_position_ms(self) -> int:
        if self.preview_is_sequence:
            return _frame_to_ms(self.sequence_frame_index, self.current_fps)
        if self.player:
            return self.player.position()
        return self._slider_value_to_ms(self.position_slider.value())

    def _slider_value_to_ms(self, value: int) -> int:
        if self.preview_is_sequence:
            return _frame_to_ms(value, self.current_fps)
        return value

    def _ms_to_slider_value(self, value_ms: int) -> int:
        if self.preview_is_sequence:
            return _ms_to_frame(value_ms, self.current_fps)
        return value_ms

    def _ms_to_display_value(self, value_ms: int) -> str:
        if self._time_mode() == "frames":
            return str(_ms_to_frame(value_ms, self.current_fps))
        return _ms_to_time(value_ms)

    def _edit_value_to_ms(self, value: str) -> int | None:
        if self._time_mode() == "frames":
            return _frame_text_to_ms(value, self.current_fps)
        return _time_to_ms(value)

    def _edit_value_to_ffmpeg_time(self, value: str) -> str | None:
        value_ms = self._edit_value_to_ms(value)
        if value_ms is None:
            return None
        return _ms_to_time(value_ms)


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


def apply_theme(app: QApplication) -> None:
    app.setStyleSheet(
        """
        QMainWindow,
        QWidget {
            background: #323232;
            color: #e6e6e6;
        }

        QMenuBar,
        QMenu {
            background: #323232;
            color: #e6e6e6;
            border: 1px solid #444;
        }

        QMenuBar::item:selected,
        QMenu::item:selected {
            background: #454545;
        }

        QTabWidget::pane {
            border: 1px solid #4a4a4a;
            background: #323232;
        }

        QTabBar::tab {
            background: #3a3a3a;
            color: #d8d8d8;
            border: 1px solid #4a4a4a;
            padding: 7px 14px;
        }

        QTabBar::tab:selected {
            background: #505050;
            color: #ffffff;
        }

        QGroupBox {
            border: 1px solid #4a4a4a;
            margin-top: 10px;
            padding: 10px;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
        }

        QLineEdit,
        QPlainTextEdit,
        QComboBox,
        QTableWidget {
            background: #242424;
            color: #eeeeee;
            border: 1px solid #555;
            selection-background-color: #5a6f8f;
            selection-color: #ffffff;
        }

        QLineEdit,
        QComboBox {
            min-height: 26px;
            padding: 2px 6px;
        }

        QPlainTextEdit {
            padding: 6px;
        }

        QPushButton {
            background: #464646;
            color: #f0f0f0;
            border: 1px solid #666;
            padding: 6px 12px;
        }

        QPushButton:hover {
            background: #555;
        }

        QPushButton:pressed {
            background: #3d3d3d;
        }

        QPushButton:disabled {
            background: #383838;
            color: #8c8c8c;
            border-color: #484848;
        }

        QProgressBar {
            background: #242424;
            color: #eeeeee;
            border: 1px solid #555;
            text-align: center;
        }

        QProgressBar::chunk {
            background: #6f8fb8;
        }

        QHeaderView::section {
            background: #3a3a3a;
            color: #eeeeee;
            border: 1px solid #555;
            padding: 4px;
        }

        QSlider::groove:horizontal {
            height: 6px;
            background: #242424;
            border: 1px solid #555;
        }

        QSlider::handle:horizontal {
            width: 14px;
            margin: -5px 0;
            background: #8fa8c8;
            border: 1px solid #adc2dc;
        }
        """
    )


def _progress_percent(current: float, duration: float | None) -> float:
    if not duration or duration <= 0:
        return 0.0
    return min(current / duration, 1.0) * 100


def _parse_progress_time(key: str, value: str) -> float | None:
    if key in {"out_time_ms", "out_time_us"}:
        try:
            return int(value) / 1_000_000
        except ValueError:
            return None
    if key == "out_time":
        parsed_ms = _time_to_ms(value)
        return parsed_ms / 1000 if parsed_ms is not None else None
    return None


def _progress_duration_for_job(job: ConvertJob, probed_duration: float | None) -> float | None:
    total_frames = _progress_total_frames_for_job(job)
    fps = _job_fps(job)
    if total_frames and fps:
        return total_frames / fps
    if probed_duration and probed_duration > 0:
        return probed_duration
    return None


def _progress_total_frames_for_job(job: ConvertJob) -> int | None:
    frames = sequence_frames(job.input)
    return len(frames) if frames else None


def _job_fps(job: ConvertJob) -> float | None:
    fps = job.preset.filters.get("fps")
    if isinstance(fps, (int, float)) and fps > 0:
        return float(fps)
    return None


def _ms_to_time(ms: int) -> str:
    total = max(ms, 0) / 1000
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def _time_to_ms(value: str) -> int | None:
    if not value:
        return None

    parts = value.split(":")
    try:
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
        elif len(parts) == 2:
            hours = 0
            minutes = int(parts[0])
            seconds = float(parts[1])
        else:
            hours = 0
            minutes = 0
            seconds = float(parts[0])
    except ValueError:
        return None

    if hours < 0 or minutes < 0 or seconds < 0:
        return None
    return round(((hours * 60 + minutes) * 60 + seconds) * 1000)


def _ms_to_frame(value_ms: int, fps: float) -> int:
    safe_fps = fps if fps > 0 else 25.0
    return round((value_ms / 1000) * safe_fps)


def _frame_to_ms(frame: int, fps: float) -> int:
    safe_fps = fps if fps > 0 else 25.0
    return round((frame / safe_fps) * 1000)


def _frame_text_to_ms(value: str, fps: float) -> int | None:
    if not value:
        return None
    try:
        frame = int(value)
    except ValueError:
        return None
    if frame < 0:
        return None
    return _frame_to_ms(frame, fps)


def _parse_positive_float(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _jpeg_quality_percent_to_qscale(value: int) -> int:
    clamped = max(0, min(100, value))
    return round(31 - (clamped / 100) * 29)


def _is_still_image_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in STILL_IMAGE_EXTENSIONS


def _command_color_space_value(value: str) -> str:
    return "none" if value.startswith("ocio:") else value


def _log_transform_value(filters: dict, side: str) -> str:
    ocio_key = f"ocio_{side}_color_space"
    basic_key = f"{side}_color_space"
    return str(filters.get(ocio_key, filters.get(basic_key, "none")))


def _display_pixmap_for_input_transform(
    pixmap: QPixmap,
    input_transform: str,
    ocio_config_path: str = "",
) -> QPixmap:
    if pixmap.isNull():
        return pixmap
    if input_transform.startswith("ocio:"):
        return _ocio_display_pixmap(pixmap, input_transform.removeprefix("ocio:"), ocio_config_path)
    if input_transform == "linear":
        source_space = QColorSpace(QColorSpace.NamedColorSpace.SRgbLinear)
    elif input_transform == "rec709":
        source_space = QColorSpace(QColorSpace.Primaries.SRgb, QColorSpace.TransferFunction.Bt2020)
    else:
        return pixmap
    image = pixmap.toImage()
    image.setColorSpace(source_space)
    converted = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    if converted.isNull():
        return pixmap
    return QPixmap.fromImage(converted)


def _ocio_display_pixmap(pixmap: QPixmap, input_color_space: str, config_path: str) -> QPixmap:
    if ocio is None or not input_color_space or not config_path.strip():
        return pixmap
    path = Path(config_path).expanduser()
    if not path.exists() or not path.is_file():
        return pixmap
    try:
        config = ocio.Config.CreateFromFile(str(path))
        display_color_space = _ocio_display_color_space(config)
        if not display_color_space:
            return pixmap
        processor = config.getProcessor(input_color_space, display_color_space).getDefaultCPUProcessor()
    except Exception:  # noqa: BLE001 - preview fallback must not break playback.
        return pixmap

    if pixmap.width() > OCIO_PREVIEW_MAX_SIZE.width() or pixmap.height() > OCIO_PREVIEW_MAX_SIZE.height():
        pixmap = pixmap.scaled(
            OCIO_PREVIEW_MAX_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            transformed = processor.applyRGBA([
                color.redF(),
                color.greenF(),
                color.blueF(),
                color.alphaF(),
            ])
            image.setPixelColor(x, y, QColor.fromRgbF(
                _clamp_float(transformed[0]),
                _clamp_float(transformed[1]),
                _clamp_float(transformed[2]),
                _clamp_float(transformed[3]),
            ))
    return QPixmap.fromImage(image)


def _ocio_display_color_space(config) -> str | None:  # noqa: ANN001 - optional OCIO type.
    for name in ("Output - sRGB", "Utility - sRGB - Texture", "Utility - Curve - sRGB"):
        if config.getColorSpace(name):
            return name
    try:
        role_color_space = config.getRoleColorSpace("color_picking")
    except Exception:  # noqa: BLE001 - compatible with different OCIO configs.
        return None
    return role_color_space or None


def _ocio_lut_path(input_transform: str, output_transform: str, config_path: str, size: int = 32) -> Path | None:
    input_color_space = _ocio_color_space_name(input_transform)
    output_color_space = _ocio_color_space_name(output_transform)
    if not input_color_space or not output_color_space or input_color_space == output_color_space:
        return None
    if ocio is None or not config_path.strip():
        return None
    path = Path(config_path).expanduser()
    if not path.exists() or not path.is_file():
        return None
    cache_key = hashlib.sha1(
        f"ocio-baker-v1|{path.resolve()}|{input_color_space}|{output_color_space}|{size}".encode("utf-8")
    ).hexdigest()
    lut_path = Path(tempfile.gettempdir()) / "7th-convert" / "ocio_luts" / f"{cache_key}.cube"
    if lut_path.exists():
        return lut_path
    try:
        config = ocio.Config.CreateFromFile(str(path))
        _write_ocio_baker_cube_lut(lut_path, config, input_color_space, output_color_space, size)
    except Exception:  # noqa: BLE001 - invalid OCIO conversion should fall back to no LUT.
        return None
    return lut_path


def _ocio_lut_is_required(input_transform: str, output_transform: str) -> bool:
    input_color_space = _ocio_color_space_name(input_transform)
    output_color_space = _ocio_color_space_name(output_transform)
    return bool(input_color_space and output_color_space and input_color_space != output_color_space)


def _ocio_color_space_name(value: str) -> str | None:
    if not value.startswith("ocio:"):
        return None
    name = value.removeprefix("ocio:").strip()
    return name or None


def _write_ocio_baker_cube_lut(
    path: Path,
    config,  # noqa: ANN001 - optional OCIO type.
    input_color_space: str,
    output_color_space: str,
    size: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    baker = ocio.Baker()
    baker.setConfig(config)
    baker.setFormat("iridas_cube")
    baker.setInputSpace(input_color_space)
    baker.setTargetSpace(output_color_space)
    baker.setCubeSize(size)
    path.write_text(baker.bake(), encoding="utf-8")


def _clamp_float(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _default_input_color_space(path: Path | None) -> str:
    if path is None:
        return "none"
    return INPUT_COLOR_DEFAULT_BY_EXTENSION.get(path.suffix.lower(), "none")


def _fps_from_probe(probe_json: dict) -> float | None:
    for stream in probe_json.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        for key in ("avg_frame_rate", "r_frame_rate"):
            fps = _fraction_to_float(stream.get(key))
            if fps:
                return fps
    return None


def _fraction_to_float(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    if "/" not in value:
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None

    numerator_text, denominator_text = value.split("/", 1)
    try:
        numerator = float(numerator_text)
        denominator = float(denominator_text)
    except ValueError:
        return None
    if denominator == 0:
        return None
    parsed = numerator / denominator
    return parsed if parsed > 0 else None


def _default_output_path(
    input_path: Path,
    preset: Preset,
    sequence_start: int | None = None,
    sequence_end: int | None = None,
    sequence_start_text: str | None = None,
    sequence_end_text: str | None = None,
) -> str:
    extension = preset.output.get("extension", "mp4")
    stem = _clean_sequence_stem(input_path.stem)
    if not preset.output.get("requires_pattern") and sequence_start is not None and sequence_end is not None:
        stem = _stem_with_sequence_range(stem, sequence_start, sequence_end, sequence_start_text, sequence_end_text)
    if preset.output.get("requires_pattern"):
        return str(input_path.with_name(f"{stem}.%04d.{extension}"))
    return str(input_path.with_name(f"{stem}_{preset.id}.{extension}"))


def _stem_with_sequence_range(
    stem: str,
    sequence_start: int,
    sequence_end: int,
    sequence_start_text: str | None = None,
    sequence_end_text: str | None = None,
) -> str:
    start = sequence_start_text or str(sequence_start)
    end = sequence_end_text or str(sequence_end)
    return f"{stem}_{start}-{end}"


def _clean_sequence_stem(stem: str) -> str:
    return re.sub(r"[_\-.]?%0?\d*d", "", stem).rstrip("_-.") or stem


def _media_summary(
    probe_json: dict,
    input_path: Path | None = None,
    fps: float | None = None,
    sequence_frame_count: int | None = None,
) -> str:
    lines: list[str] = []
    fmt = probe_json.get("format", {})
    if fmt:
        lines.append(f"Format: {fmt.get('format_name', 'unknown')}")
        lines.append(_duration_summary_line(
            probe_json,
            input_path=input_path,
            fps=fps,
            sequence_frame_count=sequence_frame_count,
        ))
        lines.append(f"Size: {_format_file_size(_summary_size_value(fmt.get('size'), input_path))}")

    for stream in probe_json.get("streams", []):
        stream_type = stream.get("codec_type", "stream")
        codec = stream.get("codec_name", "unknown")
        if stream_type == "video":
            lines.append("")
            lines.append("Video")
            lines.append(f"Codec: {codec}")
            lines.append(f"Size: {stream.get('width')}x{stream.get('height')}")
            lines.append(f"FPS: {stream.get('avg_frame_rate', 'unknown')}")
            lines.append(f"Pixel format: {stream.get('pix_fmt', 'unknown')}")
        elif stream_type == "audio":
            lines.append("")
            lines.append("Audio")
            lines.append(f"Codec: {codec}")
            lines.append(f"Sample rate: {stream.get('sample_rate', 'unknown')}")
            lines.append(f"Channels: {stream.get('channels', 'unknown')}")
    return "\n".join(lines)


def _duration_summary_line(
    probe_json: dict,
    input_path: Path | None = None,
    fps: float | None = None,
    sequence_frame_count: int | None = None,
) -> str:
    if sequence_frame_count and sequence_frame_count > 1:
        safe_fps = fps if fps and fps > 0 else _fps_from_probe(probe_json)
        if safe_fps:
            duration = sequence_frame_count / safe_fps
            return f"Duration: {duration:.3f}s / {sequence_frame_count} frames"
        return f"Duration: unknown / {sequence_frame_count} frames"

    if input_path is not None:
        frames = sequence_frames(input_path)
        if len(frames) > 1:
            safe_fps = fps if fps and fps > 0 else _fps_from_probe(probe_json)
            if safe_fps:
                duration = len(frames) / safe_fps
                return f"Duration: {duration:.3f}s / {len(frames)} frames"
            return f"Duration: unknown / {len(frames)} frames"

    duration = duration_seconds(probe_json)
    if duration is None:
        return "Duration: unknown"

    fps = _fps_from_probe(probe_json)
    if fps:
        frames = round(duration * fps)
        return f"Duration: {duration:.3f}s / {frames} frames"
    return f"Duration: {duration:.3f}s / unknown frames"


def _format_file_size(value: object) -> str:
    try:
        size = int(value)  # ffprobe returns format.size as a string.
    except (TypeError, ValueError):
        return "unknown"
    if size < 0:
        return "unknown"
    if size < 1024 * 1024:
        return f"{size / 1024:.2f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def _summary_size_value(format_size: object, input_path: Path | None = None) -> object:
    if input_path is not None:
        frames = sequence_frames(input_path)
        if len(frames) > 1:
            total = 0
            for frame in frames:
                try:
                    total += frame.stat().st_size
                except OSError:
                    return format_size
            return total
    return format_size


def _frame_number_from_path(path: Path) -> int | None:
    match = split_sequence_name(path.name)
    if match is None:
        return None
    return int(match[1])


if __name__ == "__main__":
    raise SystemExit(main())
