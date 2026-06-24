from __future__ import annotations

import json
import hashlib
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from importlib import resources
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QColorSpace, QImage, QKeyEvent, QKeySequence, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .command_builder import ConvertJob, build_ffmpeg_args
from .converter import format_command, output_exists_for_job, same_input_and_output, validate_job
from .exr_metadata import preserve_exr_metadata_for_job
from .ffprobe import duration_seconds, probe
from .presets import Preset, get_preset, load_presets
from .sequence import sequence_frames, sequence_groups, sequence_start_number, split_sequence_name

try:
    import PyOpenColorIO as ocio
except Exception:  # noqa: BLE001 - optional runtime dependency.
    ocio = None

try:
    import numpy as np
except Exception:  # noqa: BLE001 - optional runtime dependency for faster OCIO preview.
    np = None


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
COLOR_WORKFLOW_BUILTIN_OCIO = "builtin_ocio"
COLOR_WORKFLOW_OCIO = "ocio"
OCIO_PREVIEW_MAX_SIZE = QSize(960, 960)
PIXEL_ASPECT_AUTO = "auto"
PIXEL_ASPECT_MANUAL = "manual"
ANAMORPH_OUTPUT_PRESERVE = "preserve"
ANAMORPH_OUTPUT_BAKE = "bake"


@dataclass(frozen=True)
class BuiltinOcioConfig:
    id: str
    label: str
    config_relative_path: str
    default_input_by_extension: dict[str, str]
    default_output_by_file_type: dict[str, str]


BUILTIN_OCIO_CONFIGS = {
    "nuke-default": BuiltinOcioConfig(
        id="nuke-default",
        label="Nuke Default",
        config_relative_path="color_configs/nuke-default/config.ocio",
        default_input_by_extension={
            ".cin": "Cineon",
            ".cineon": "Cineon",
            ".dpx": "Cineon",
            ".exr": "linear",
            ".gif": "sRGB",
            ".jpeg": "sRGB",
            ".jpg": "sRGB",
            ".mov": "rec709",
            ".mp4": "sRGB",
            ".png": "sRGB",
            ".targa": "sRGB",
            ".tga": "sRGB",
        },
        default_output_by_file_type={
            "exr": "linear",
            "gif": "sRGB",
            "jpg": "sRGB",
            "mov": "rec709",
            "mp4": "sRGB",
            "png": "sRGB",
            "targa": "sRGB",
        },
    )
}

INPUT_COLOR_DEFAULT_BY_EXTENSION = {
    ".exr": "linear",
    ".gif": "srgb",
    ".jpeg": "srgb",
    ".jpg": "srgb",
    ".mov": "rec709",
    ".mp4": "srgb",
    ".png": "srgb",
    ".targa": "srgb",
    ".tga": "srgb",
}

OUTPUT_COLOR_DEFAULT_BY_FILE_TYPE = {
    "exr": "linear",
    "gif": "srgb",
    "jpg": "srgb",
    "mov": "rec709",
    "mp4": "srgb",
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
            "h264_nvenc": {
                "label": "H.264 NVENC",
                "preset": "h264_mp4",
                "profile_label": "Quality",
                "video": {"codec": "h264_nvenc", "crf": None},
                "profiles": {
                    "balanced": {
                        "label": "Balanced",
                        "profile": "high",
                        "pix_fmt": "yuv420p",
                        "encoder_options": {"preset": "p5", "tune": "hq", "cq": 20},
                    },
                    "quality": {
                        "label": "Quality",
                        "profile": "high",
                        "pix_fmt": "yuv420p",
                        "encoder_options": {"preset": "p7", "tune": "hq", "cq": 18},
                    },
                    "fast": {
                        "label": "Fast",
                        "profile": "high",
                        "pix_fmt": "yuv420p",
                        "encoder_options": {"preset": "p3", "tune": "hq", "cq": 23},
                    },
                },
                "default_profile": "balanced",
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
            "h265_nvenc": {
                "label": "H.265 NVENC",
                "preset": "h265_mp4",
                "profile_label": "Quality",
                "video": {"codec": "hevc_nvenc", "crf": None},
                "profiles": {
                    "balanced": {
                        "label": "Balanced",
                        "profile": "main",
                        "pix_fmt": "yuv420p",
                        "encoder_options": {"preset": "p5", "tune": "hq", "cq": 22},
                    },
                    "quality": {
                        "label": "Quality",
                        "profile": "main",
                        "pix_fmt": "yuv420p",
                        "encoder_options": {"preset": "p7", "tune": "hq", "cq": 19},
                    },
                    "fast": {
                        "label": "Fast",
                        "profile": "main",
                        "pix_fmt": "yuv420p",
                        "encoder_options": {"preset": "p3", "tune": "hq", "cq": 25},
                    },
                },
                "default_profile": "balanced",
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
                "profile_label": "Bit Depth / Sample Rate",
                "profiles": {
                    "pcm_s16le_44100": {"label": "PCM 16-bit / 44.1 kHz", "audio_codec": "pcm_s16le", "sample_rate": 44100},
                    "pcm_s16le_48000": {"label": "PCM 16-bit / 48 kHz", "audio_codec": "pcm_s16le", "sample_rate": 48000},
                    "pcm_s16le_24000": {"label": "PCM 16-bit / 24 kHz", "audio_codec": "pcm_s16le", "sample_rate": 24000},
                    "pcm_s16le_14000": {"label": "PCM 16-bit / 14 kHz", "audio_codec": "pcm_s16le", "sample_rate": 14000},
                    "pcm_s16le_8000": {"label": "PCM 16-bit / 8 kHz", "audio_codec": "pcm_s16le", "sample_rate": 8000},
                },
                "default_profile": "pcm_s16le_48000",
            }
        },
        "default_codec": "pcm",
    },
    "mp3": {
        "label": "mp3",
        "extension": "mp3",
        "codecs": {
            "mp3": {
                "label": "MP3",
                "preset": "mp3_audio",
                "profile_label": "Bitrate / Sample Rate",
                "profiles": {
                    "mp3_256k_48000": {"label": "256 kb/s / 48 kHz", "audio_codec": "libmp3lame", "bitrate": "256k", "sample_rate": 48000},
                    "mp3_256k_44100": {"label": "256 kb/s / 44.1 kHz", "audio_codec": "libmp3lame", "bitrate": "256k", "sample_rate": 44100},
                    "mp3_192k_48000": {"label": "192 kb/s / 48 kHz", "audio_codec": "libmp3lame", "bitrate": "192k", "sample_rate": 48000},
                    "mp3_192k_44100": {"label": "192 kb/s / 44.1 kHz", "audio_codec": "libmp3lame", "bitrate": "192k", "sample_rate": 44100},
                    "mp3_128k_44100": {"label": "128 kb/s / 44.1 kHz", "audio_codec": "libmp3lame", "bitrate": "128k", "sample_rate": 44100},
                    "mp3_96k_24000": {"label": "96 kb/s / 24 kHz", "audio_codec": "libmp3lame", "bitrate": "96k", "sample_rate": 24000},
                    "mp3_64k_14000": {"label": "64 kb/s / 14 kHz", "audio_codec": "libmp3lame", "bitrate": "64k", "sample_rate": 14000},
                    "mp3_32k_8000": {"label": "32 kb/s / 8 kHz", "audio_codec": "libmp3lame", "bitrate": "32k", "sample_rate": 8000},
                },
                "default_profile": "mp3_192k_48000",
            }
        },
        "default_codec": "mp3",
    },
    "aac": {
        "label": "aac",
        "extension": "aac",
        "codecs": {
            "aac": {
                "label": "AAC",
                "preset": "aac_audio",
                "profile_label": "Bitrate / Sample Rate",
                "profiles": {
                    "aac_256k_48000": {"label": "256 kb/s / 48 kHz", "audio_codec": "aac", "bitrate": "256k", "sample_rate": 48000},
                    "aac_256k_44100": {"label": "256 kb/s / 44.1 kHz", "audio_codec": "aac", "bitrate": "256k", "sample_rate": 44100},
                    "aac_192k_48000": {"label": "192 kb/s / 48 kHz", "audio_codec": "aac", "bitrate": "192k", "sample_rate": 48000},
                    "aac_192k_44100": {"label": "192 kb/s / 44.1 kHz", "audio_codec": "aac", "bitrate": "192k", "sample_rate": 44100},
                    "aac_128k_44100": {"label": "128 kb/s / 44.1 kHz", "audio_codec": "aac", "bitrate": "128k", "sample_rate": 44100},
                    "aac_96k_24000": {"label": "96 kb/s / 24 kHz", "audio_codec": "aac", "bitrate": "96k", "sample_rate": 24000},
                    "aac_64k_14000": {"label": "64 kb/s / 14 kHz", "audio_codec": "aac", "bitrate": "64k", "sample_rate": 14000},
                    "aac_32k_8000": {"label": "32 kb/s / 8 kHz", "audio_codec": "aac", "bitrate": "32k", "sample_rate": 8000},
                },
                "default_profile": "aac_192k_48000",
            }
        },
        "default_codec": "aac",
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
        self._source_pixel_aspect = 1.0

    def set_source_pixmap(self, pixmap: QPixmap, pixel_aspect: float = 1.0) -> None:
        self._source_pixmap = QPixmap(pixmap)
        self._source_pixel_aspect = _normalized_pixel_aspect(pixel_aspect)
        self.setText("")
        self._refresh_scaled_pixmap()

    def clear_source_pixmap(self) -> None:
        self._source_pixmap = QPixmap()
        self._source_pixel_aspect = 1.0
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
        source_width = self._source_pixmap.width() * self._source_pixel_aspect
        source_height = self._source_pixmap.height()
        if source_width <= 0 or source_height <= 0:
            return
        container_width = self.width()
        container_height = self.height()
        container_ratio = container_width / container_height
        source_ratio = source_width / source_height
        if source_ratio >= container_ratio:
            display_width = container_width
            display_height = max(1, round(container_width / source_ratio))
        else:
            display_height = container_height
            display_width = max(1, round(container_height * source_ratio))
        super().setPixmap(self._source_pixmap.scaled(
            display_width,
            display_height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))


class MetadataTable(QTableWidget):
    def contextMenuEvent(self, event) -> None:  # noqa: ANN001 - Qt override signature.
        index = self.indexAt(event.pos())
        row_is_selected = any(selected.row() == index.row() for selected in self.selectedIndexes()) if index.isValid() else False
        if index.isValid() and not row_is_selected:
            self.selectRow(index.row())

        menu = QMenu(self)
        copy_action = menu.addAction("Copy")
        copy_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Copy))
        copy_action.setEnabled(bool(self.selectedIndexes()))
        copy_action.triggered.connect(self.copy_selected_rows)
        menu.exec(event.globalPos())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selected_rows()
            return
        super().keyPressEvent(event)

    def copy_selected_rows(self) -> None:
        rows = sorted({index.row() for index in self.selectedIndexes() if not self.isRowHidden(index.row())})
        if not rows:
            return
        lines = []
        for row in rows:
            values = []
            for column in range(self.columnCount()):
                item = self.item(row, column)
                values.append(item.text() if item else "")
            lines.append("\t".join(values))
        QApplication.clipboard().setText("\n".join(lines))


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


def _builtin_ocio_config_path(config_id: str) -> str:
    config = BUILTIN_OCIO_CONFIGS.get(config_id) or BUILTIN_OCIO_CONFIGS["nuke-default"]
    path = resources.files("seventh_convert").joinpath(config.config_relative_path)
    return str(path)


def _builtin_ocio_config(config_id: str) -> BuiltinOcioConfig:
    return BUILTIN_OCIO_CONFIGS.get(config_id) or BUILTIN_OCIO_CONFIGS["nuke-default"]


class PreferencesDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        workflow: str,
        ocio_config_path: str,
        builtin_ocio_config: str,
        ocio_status: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        layout = QVBoxLayout(self)

        color_group = QGroupBox("Color Management")
        form = QFormLayout(color_group)

        self.workflow_combo = QComboBox()
        self.workflow_combo.addItem("Nuke", COLOR_WORKFLOW_BUILTIN_OCIO)
        self.workflow_combo.addItem("OCIO", COLOR_WORKFLOW_OCIO)
        self._custom_ocio_config_path = ocio_config_path
        self._set_combo_data(
            self.workflow_combo,
            workflow if workflow in {COLOR_WORKFLOW_BUILTIN_OCIO, COLOR_WORKFLOW_OCIO} else COLOR_WORKFLOW_BUILTIN_OCIO,
        )
        form.addRow("Color Management", self.workflow_combo)

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
        self.ocio_config_edit.textChanged.connect(self.handle_ocio_config_text_changed)
        layout.addWidget(color_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_status()

    def selected_workflow(self) -> str:
        return str(self.workflow_combo.currentData())

    def selected_ocio_config_path(self) -> str:
        if self.selected_workflow() != COLOR_WORKFLOW_OCIO:
            return self._custom_ocio_config_path.strip()
        return self.ocio_config_edit.text().strip()

    def selected_builtin_ocio_config(self) -> str:
        return "nuke-default"

    def handle_ocio_config_text_changed(self, text: str) -> None:
        if self.selected_workflow() == COLOR_WORKFLOW_OCIO:
            self._custom_ocio_config_path = text.strip()
        self.refresh_status()

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
        workflow = self.selected_workflow()
        self.ocio_config_edit.setEnabled(workflow == COLOR_WORKFLOW_OCIO)
        self.ocio_browse_button.setEnabled(workflow == COLOR_WORKFLOW_OCIO)
        if workflow == COLOR_WORKFLOW_BUILTIN_OCIO:
            config = _builtin_ocio_config(self.selected_builtin_ocio_config())
            if self.ocio_config_edit.text() != config.label:
                self.ocio_config_edit.blockSignals(True)
                self.ocio_config_edit.setText(config.label)
                self.ocio_config_edit.blockSignals(False)
            _color_spaces, status = _load_ocio_color_spaces(_builtin_ocio_config_path(config.id))
            self.ocio_status_label.setText(f"{config.label}: {status}")
            return
        if self.ocio_config_edit.text() == _builtin_ocio_config(self.selected_builtin_ocio_config()).label:
            self.ocio_config_edit.blockSignals(True)
            self.ocio_config_edit.setText(self._custom_ocio_config_path)
            self.ocio_config_edit.blockSignals(False)
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
        self.builtin_ocio_config = str(self.settings.value("ocio/builtin_config", "nuke-default"))
        self.ocio_color_spaces: list[str] = []
        self.ocio_status = "No OCIO config selected"
        self.reload_ocio_config()
        self.current_probe_json: dict | None = None
        self.current_worker: ConvertWorker | None = None
        self.use_media = os.environ.get("QT_QPA_PLATFORM") != "offscreen" if use_media is None else use_media
        self.current_fps = 25.0
        self.current_source_raster_size: QSize | None = None
        self._updating_scale_controls = False

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
        self.input_edit_refresh_timer = QTimer(self)
        self.input_edit_refresh_timer.setSingleShot(True)
        self.input_edit_refresh_timer.timeout.connect(self.refresh_from_manual_input_path)
        self.output_path_is_manual = False
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
        if self.color_workflow == COLOR_WORKFLOW_BASIC:
            self.ocio_color_spaces = []
            self.ocio_status = "Basic workflow uses built-in transforms"
            return
        if self.color_workflow == COLOR_WORKFLOW_BUILTIN_OCIO:
            config = _builtin_ocio_config(self.builtin_ocio_config)
            self.ocio_color_spaces, status = _load_ocio_color_spaces(_builtin_ocio_config_path(config.id))
            self.ocio_status = f"{config.label}: {status}"
            return
        if self.color_workflow == COLOR_WORKFLOW_OCIO:
            self.ocio_color_spaces, self.ocio_status = _load_ocio_color_spaces(self.ocio_config_path)
            return
        self.color_workflow = COLOR_WORKFLOW_BASIC
        self.ocio_color_spaces = []
        self.ocio_status = "Basic workflow uses built-in transforms"

    def ocio_workflow_is_active(self) -> bool:
        return self.color_workflow in {COLOR_WORKFLOW_BUILTIN_OCIO, COLOR_WORKFLOW_OCIO} and bool(self.ocio_color_spaces)

    def active_ocio_config_path(self) -> str:
        if self.color_workflow == COLOR_WORKFLOW_BUILTIN_OCIO:
            return _builtin_ocio_config_path(self.builtin_ocio_config)
        if self.color_workflow == COLOR_WORKFLOW_OCIO:
            return self.ocio_config_path
        return ""

    def open_preferences(self) -> None:
        dialog = PreferencesDialog(
            self,
            workflow=self.color_workflow,
            ocio_config_path=self.ocio_config_path,
            builtin_ocio_config=self.builtin_ocio_config,
            ocio_status=self.ocio_status,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.apply_color_preferences(
            dialog.selected_workflow(),
            dialog.selected_ocio_config_path(),
            dialog.selected_builtin_ocio_config(),
        )

    def apply_color_preferences(
        self,
        workflow: str,
        ocio_config_path: str,
        builtin_ocio_config: str | None = None,
    ) -> None:
        valid_workflows = {COLOR_WORKFLOW_BASIC, COLOR_WORKFLOW_BUILTIN_OCIO, COLOR_WORKFLOW_OCIO}
        self.color_workflow = workflow if workflow in valid_workflows else COLOR_WORKFLOW_BASIC
        self.ocio_config_path = ocio_config_path
        if builtin_ocio_config:
            self.builtin_ocio_config = builtin_ocio_config
        self.settings.setValue("color/workflow", self.color_workflow)
        self.settings.setValue("ocio/config_path", self.ocio_config_path)
        self.settings.setValue("ocio/builtin_config", self.builtin_ocio_config)
        self.reload_ocio_config()
        self.refresh_color_transform_options()
        self.refresh_color_transform_defaults()
        self.refresh_preview_display_transform()

    def _build_convert_tab(self) -> None:
        tab = QWidget()
        root = QVBoxLayout(tab)

        input_row = QGridLayout()
        self.input_edit = QLineEdit()
        self.input_edit.textChanged.connect(lambda _text: self.schedule_manual_input_refresh())
        self.input_range_edit = QLineEdit()
        self.input_range_edit.setReadOnly(True)
        self.input_range_edit.setPlaceholderText("No sequence range selected")
        self.input_browse_button = QPushButton("Browse")
        self.input_browse_button.clicked.connect(self.browse_input)
        self.audio_input_edit = QLineEdit()
        self.audio_input_edit.textChanged.connect(lambda _text: self.refresh_audio_controls())
        self.audio_input_browse_button = QPushButton("Browse")
        self.audio_input_browse_button.clicked.connect(self.browse_audio_input)
        input_row.addWidget(QLabel("Input"), 0, 0)
        input_row.addWidget(self.input_edit, 0, 1)
        input_row.addWidget(self.input_browse_button, 0, 2)
        input_row.addWidget(QLabel("Range"), 1, 0)
        input_row.addWidget(self.input_range_edit, 1, 1, 1, 2)
        input_row.addWidget(QLabel("Audio Input"), 2, 0)
        input_row.addWidget(self.audio_input_edit, 2, 1)
        input_row.addWidget(self.audio_input_browse_button, 2, 2)
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

        geometry_group = QGroupBox("Image")
        geometry_layout = QFormLayout(geometry_group)

        self.scale_label = QLabel("1.000")
        scale_row = QHBoxLayout()
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(0, 2000)
        self.scale_slider.setValue(1000)
        self.scale_slider.valueChanged.connect(self.handle_scale_slider_changed)
        self.scale_label.setMinimumWidth(44)
        scale_row.addWidget(self.scale_slider, stretch=1)
        scale_row.addWidget(self.scale_label)
        geometry_layout.addRow("Scale", scale_row)

        size_row = QHBoxLayout()
        self.output_width_spin = QSpinBox()
        self.output_width_spin.setRange(1, 99999)
        self.output_width_spin.setSuffix(" px")
        self.output_width_spin.valueChanged.connect(self.handle_output_width_changed)
        self.output_height_spin = QSpinBox()
        self.output_height_spin.setRange(1, 99999)
        self.output_height_spin.setSuffix(" px")
        self.output_height_spin.valueChanged.connect(self.handle_output_height_changed)
        size_row.addWidget(self.output_width_spin)
        size_row.addWidget(QLabel("x"))
        size_row.addWidget(self.output_height_spin)
        geometry_layout.addRow("Output Size", size_row)

        self.preview_pixel_aspect_combo = QComboBox()
        self.preview_pixel_aspect_combo.addItem("Auto", PIXEL_ASPECT_AUTO)
        self.preview_pixel_aspect_combo.addItem("Manual", PIXEL_ASPECT_MANUAL)
        self.preview_pixel_aspect_combo.currentIndexChanged.connect(lambda _index: self.refresh_pixel_aspect_controls())
        geometry_layout.addRow("Pixel Aspect", self.preview_pixel_aspect_combo)

        self.manual_pixel_aspect_edit = QLineEdit("1.0")
        self.manual_pixel_aspect_edit.setPlaceholderText("1.0")
        self.manual_pixel_aspect_edit.textChanged.connect(self.handle_manual_pixel_aspect_changed)
        self.manual_pixel_aspect_edit.editingFinished.connect(self.handle_manual_pixel_aspect_changed)
        geometry_layout.addRow("Manual PAR", self.manual_pixel_aspect_edit)

        self.anamorph_output_combo = QComboBox()
        self.anamorph_output_combo.addItem("Keep Original Pixels", ANAMORPH_OUTPUT_PRESERVE)
        self.anamorph_output_combo.addItem("Resize to Square Pixels", ANAMORPH_OUTPUT_BAKE)
        self.anamorph_output_combo.currentIndexChanged.connect(lambda _index: self.refresh_scale_controls())
        geometry_layout.addRow("Pixel Aspect Output", self.anamorph_output_combo)
        layout.addRow(geometry_group)

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
        self.fps_edit.textChanged.connect(lambda _text: self.refresh_media_info_summary())
        layout.addRow(self.fps_label, self.fps_edit)

        self.codec_profile_label = QLabel("Codec Profile")
        self.codec_profile_combo = QComboBox()
        layout.addRow(self.codec_profile_label, self.codec_profile_combo)

        self.audio_group = QGroupBox("Audio")
        audio_layout = QFormLayout(self.audio_group)
        self.audio_format_combo = QComboBox()
        for file_type in ("wav", "mp3", "aac"):
            self.audio_format_combo.addItem(OUTPUT_OPTIONS[file_type]["label"], file_type)
        self.audio_format_combo.setCurrentIndex(self.audio_format_combo.findData("aac"))
        self.audio_format_combo.currentIndexChanged.connect(lambda _index: self.refresh_external_audio_profile_controls())
        audio_layout.addRow("Audio Format", self.audio_format_combo)
        self.audio_profile_label = QLabel("Audio Profile")
        self.audio_profile_combo = QComboBox()
        audio_layout.addRow(self.audio_profile_label, self.audio_profile_combo)
        self.copy_video_add_audio_check = QCheckBox("Add Audio Without Re-encoding Video")
        self.copy_video_add_audio_check.toggled.connect(lambda _checked: self.refresh_video_copy_audio_controls())
        audio_layout.addRow("", self.copy_video_add_audio_check)
        layout.addRow(self.audio_group)

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
        self.output_transform_combo.currentIndexChanged.connect(lambda _index: self.refresh_media_info_summary())
        layout.addRow("Input Transform", self.input_transform_combo)
        layout.addRow("Output Transform", self.output_transform_combo)
        self.refresh_color_transform_options()

        self.output_edit = QLineEdit()
        self.output_edit.textEdited.connect(lambda _text: self.mark_output_path_manual())
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
        layout.addRow("Media Info", self.summary_text)

        self.refresh_output_controls()
        self.refresh_pixel_aspect_controls()
        return group

    def _build_media_info_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.metadata_table = MetadataTable(0, 2)
        self.metadata_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.metadata_table.horizontalHeader().setStretchLastSection(True)
        self.metadata_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.metadata_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.metadata_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.metadata_table)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search metadata for"))
        self.metadata_search_edit = QLineEdit()
        self.metadata_search_edit.textChanged.connect(self.filter_metadata_table)
        search_row.addWidget(self.metadata_search_edit, stretch=1)
        layout.addLayout(search_row)
        self.tabs.addTab(tab, "Metadata")

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
        self.log_text.setObjectName("logText")
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        self.tabs.addTab(tab, "Logs")

    def browse_input(self) -> None:
        selected = SequenceFileDialog.get_input(self, self._input_dialog_start_dir())
        if not selected:
            return
        self.input_edit_refresh_timer.stop()
        self.input_edit.blockSignals(True)
        self.input_edit.setText(str(selected.input_path))
        self.input_edit.blockSignals(False)
        self.current_input_is_sequence = selected.is_sequence
        self.current_input_sequence_start = selected.sequence_start
        self.current_input_sequence_end = selected.sequence_end
        self.current_input_sequence_frame_count = selected.sequence_frame_count
        self.current_input_sequence_start_text = selected.sequence_start_text
        self.current_input_sequence_end_text = selected.sequence_end_text
        self.input_range_edit.setText(self._selected_range_text())
        self._prepare_preview_source(selected.preview_path, selected.is_sequence)
        if not self.output_path_is_manual or not self.output_edit.text().strip():
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

    def browse_audio_input(self) -> None:
        current = self.audio_input_edit.text().strip()
        start_path = Path(current).expanduser().parent if current else Path.home()
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose audio input",
            str(start_path),
            "Audio files (*.wav *.mp3 *.aac *.m4a *.flac *.ogg);;All files (*)",
        )
        if path:
            self.audio_input_edit.setText(path)

    def schedule_manual_input_refresh(self) -> None:
        self.input_edit_refresh_timer.start(120)

    def mark_output_path_manual(self) -> None:
        self.output_path_is_manual = bool(self.output_edit.text().strip())

    def refresh_from_manual_input_path(self) -> None:
        input_text = self.input_edit.text().strip()
        if not input_text:
            return
        input_path = Path(input_text).expanduser()
        sequence_start = sequence_start_number(input_path)
        frames = sequence_frames(input_path) if sequence_start is not None else []
        sequence_end = None
        sequence_start_text = None
        sequence_end_text = None
        if frames:
            start_match = split_sequence_name(frames[0].name)
            end_match = split_sequence_name(frames[-1].name)
            if start_match and end_match:
                sequence_start = int(start_match[1])
                sequence_end = int(end_match[1])
                sequence_start_text = start_match[1]
                sequence_end_text = end_match[1]

        self.current_input_is_sequence = sequence_start is not None
        self.current_input_sequence_start = sequence_start
        self.current_input_sequence_end = sequence_end
        self.current_input_sequence_frame_count = len(frames) if frames else None
        self.current_input_sequence_start_text = sequence_start_text
        self.current_input_sequence_end_text = sequence_end_text
        self.input_range_edit.setText(self._selected_range_text())
        self.refresh_color_transform_defaults()
        self.refresh_fps_control_visibility()
        if not self.output_path_is_manual or not self.output_edit.text().strip():
            self.output_edit.setText(_default_output_path(
                input_path,
                self.current_preset(),
                sequence_start=sequence_start,
                sequence_end=sequence_end,
                sequence_start_text=sequence_start_text,
                sequence_end_text=sequence_end_text,
            ))
        preview_path = frames[0] if frames else input_path
        if preview_path.exists():
            self._prepare_preview_source(preview_path, is_sequence=bool(frames))
            self.probe_input(preview_path)

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
            self.output_path_is_manual = True

    def probe_input(self, input_path: Path | None = None) -> None:
        input_path = input_path or Path(self.input_edit.text()).expanduser()
        try:
            self.current_probe_json = probe(input_path)
        except Exception as exc:  # noqa: BLE001 - concise UI message.
            QMessageBox.warning(self, "Analyze failed", str(exc))
            return

        self.current_fps = _fps_from_probe(self.current_probe_json) or self.current_fps
        self.update_source_raster_size(_video_size_from_probe(self.current_probe_json))
        self.refresh_media_info_summary()
        self.refresh_metadata_table(input_path)
        self._sync_video_timeline_from_probe()
        self.tabs.setCurrentIndex(0)

    def refresh_media_info_summary(self) -> None:
        if not hasattr(self, "summary_text") or self.current_probe_json is None:
            return
        self.summary_text.setPlainText(_media_summary(
            self.current_probe_json,
            input_path=Path(self.input_edit.text()).expanduser(),
            fps=self.current_fps,
            sequence_frame_count=self.current_input_sequence_frame_count,
            output_resolution=self.selected_output_raster_size(),
            output_codec=self.selected_output_codec_label(),
            output_color_space=self.selected_output_transform_label(),
        ))

    def refresh_metadata_table(self, input_path: Path | None = None) -> None:
        if not hasattr(self, "metadata_table") or self.current_probe_json is None:
            return
        rows = _metadata_rows(
            self.current_probe_json,
            input_path or Path(self.input_edit.text()).expanduser(),
        )
        self.metadata_table.setRowCount(len(rows))
        for row_index, (source, group, key, value) in enumerate(rows):
            key_item = QTableWidgetItem(_metadata_display_key(source, group, key))
            key_item.setData(Qt.ItemDataRole.UserRole, _metadata_full_key(source, group, key))
            self.metadata_table.setItem(row_index, 0, key_item)
            self.metadata_table.setItem(row_index, 1, QTableWidgetItem(value))
        self.metadata_table.resizeColumnsToContents()
        self.filter_metadata_table()

    def filter_metadata_table(self) -> None:
        if not hasattr(self, "metadata_table") or not hasattr(self, "metadata_search_edit"):
            return
        needle = self.metadata_search_edit.text().strip().casefold()
        for row in range(self.metadata_table.rowCount()):
            key_item = self.metadata_table.item(row, 0)
            value_item = self.metadata_table.item(row, 1)
            full_key = key_item.data(Qt.ItemDataRole.UserRole) if key_item else ""
            haystack = " ".join((
                key_item.text() if key_item else "",
                str(full_key or ""),
                value_item.text() if value_item else "",
            )).casefold()
            self.metadata_table.setRowHidden(row, bool(needle and needle not in haystack))

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
        self.refresh_scale_controls()
        self.refresh_audio_controls()
        self.refresh_video_copy_audio_controls()
        self.refresh_media_info_summary()

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
        self.refresh_video_copy_audio_controls()
        self.refresh_media_info_summary()

    def refresh_audio_controls(self) -> None:
        if not hasattr(self, "audio_group"):
            return
        has_audio_input = self.external_audio_enabled()
        self.audio_group.setVisible(has_audio_input)
        self.audio_group.setEnabled(has_audio_input)
        if has_audio_input and self.audio_profile_combo.count() == 0:
            self.refresh_external_audio_profile_controls()
        self.copy_video_add_audio_check.setVisible(self.selected_file_type() == "mp4")
        self.copy_video_add_audio_check.setEnabled(has_audio_input and self.selected_file_type() == "mp4")
        if not has_audio_input or self.selected_file_type() != "mp4":
            self.copy_video_add_audio_check.blockSignals(True)
            self.copy_video_add_audio_check.setChecked(False)
            self.copy_video_add_audio_check.blockSignals(False)
        self.refresh_video_copy_audio_controls()

    def refresh_external_audio_profile_controls(self) -> None:
        if not hasattr(self, "audio_profile_combo"):
            return
        file_type = self.selected_external_audio_format()
        option = OUTPUT_OPTIONS[file_type]
        codec_key = option["default_codec"]
        codec_option = option["codecs"][codec_key]
        previous_profile = self.audio_profile_combo.currentData()

        self.audio_profile_combo.blockSignals(True)
        self.audio_profile_combo.clear()
        self.audio_profile_label.setText(codec_option.get("profile_label", "Audio Profile"))
        for profile_key, profile_option in codec_option["profiles"].items():
            self.audio_profile_combo.addItem(profile_option["label"], profile_key)
        default_profile = previous_profile if previous_profile in codec_option["profiles"] else codec_option["default_profile"]
        self.audio_profile_combo.setCurrentIndex(max(self.audio_profile_combo.findData(default_profile), 0))
        self.audio_profile_combo.blockSignals(False)
        self.refresh_media_info_summary()

    def refresh_video_copy_audio_controls(self) -> None:
        if not hasattr(self, "copy_video_add_audio_check"):
            return
        mux_mode = self.video_copy_audio_mux_enabled()
        for widget in (
            self.codec_combo,
            self.codec_profile_combo,
            self.input_transform_combo,
            self.output_transform_combo,
            self.preview_pixel_aspect_combo,
            self.anamorph_output_combo,
            self.jpg_quality_slider,
        ):
            widget.setEnabled(not mux_mode)
        self.jpg_quality_label.setEnabled(not mux_mode)
        self.jpg_quality_value_label.setEnabled(not mux_mode)
        self.refresh_fps_control_visibility()
        self.refresh_pixel_aspect_controls()
        self.refresh_scale_controls()
        self.refresh_media_info_summary()

    def refresh_output_path_extension(self) -> None:
        if not self.input_edit.text().strip():
            return
        current_output = self.output_edit.text().strip()
        if not current_output:
            self.output_path_is_manual = False
            self.output_edit.setText(_default_output_path(Path(self.input_edit.text()), self.current_preset()))
            return
        if self.output_path_is_manual:
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

    def external_audio_enabled(self) -> bool:
        return bool(hasattr(self, "audio_input_edit") and self.audio_input_edit.text().strip())

    def selected_external_audio_format(self) -> str:
        return str(self.audio_format_combo.currentData() or "aac")

    def selected_external_audio_profile(self) -> str:
        return str(self.audio_profile_combo.currentData())

    def video_copy_audio_mux_enabled(self) -> bool:
        return (
            hasattr(self, "copy_video_add_audio_check")
            and self.selected_file_type() == "mp4"
            and self.external_audio_enabled()
            and self.copy_video_add_audio_check.isChecked()
        )

    def selected_external_audio_settings(self) -> dict:
        file_type = self.selected_external_audio_format()
        option = OUTPUT_OPTIONS[file_type]
        codec_key = option["default_codec"]
        codec_option = option["codecs"][codec_key]
        profile_key = self.selected_external_audio_profile() or codec_option["default_profile"]
        profile_option = codec_option["profiles"].get(profile_key) or codec_option["profiles"][codec_option["default_profile"]]
        audio = deepcopy(get_preset(codec_option["preset"]).audio)
        if "audio_codec" in profile_option:
            audio["codec"] = profile_option["audio_codec"]
        if "sample_rate" in profile_option:
            audio["sample_rate"] = profile_option["sample_rate"]
        if "bitrate" in profile_option:
            audio["bitrate"] = profile_option["bitrate"]
        audio["shortest"] = True
        return audio

    def selected_input_transform(self) -> str:
        return str(self.input_transform_combo.currentData())

    def selected_output_transform(self) -> str:
        return str(self.output_transform_combo.currentData())

    def selected_output_transform_label(self) -> str:
        return self.output_transform_combo.currentText() or self.selected_output_transform()

    def selected_output_codec_label(self) -> str:
        codec = self.codec_combo.currentText()
        profile = self.codec_profile_combo.currentText()
        if codec and profile:
            return f"{codec} / {profile}"
        return codec or profile or "unknown"

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
            input_path = Path(self.input_edit.text()).expanduser() if self.input_edit.text().strip() else None
            input_default = self._default_ocio_input_color_space(input_path)
            output_default = self._default_ocio_output_color_space(self.selected_file_type())
            self._set_combo_data(self.input_transform_combo, input_default)
            self._set_combo_data(self.output_transform_combo, output_default)
            return
        input_path = Path(self.input_edit.text()).expanduser() if self.input_edit.text().strip() else None
        input_default = _default_input_color_space(input_path)
        output_default = OUTPUT_COLOR_DEFAULT_BY_FILE_TYPE.get(self.selected_file_type(), "none")
        self._set_combo_data(self.input_transform_combo, input_default)
        self._set_combo_data(self.output_transform_combo, output_default)

    def _default_ocio_input_color_space(self, input_path: Path | None) -> str:
        if self.color_workflow == COLOR_WORKFLOW_BUILTIN_OCIO:
            config = _builtin_ocio_config(self.builtin_ocio_config)
            name = config.default_input_by_extension.get(input_path.suffix.lower() if input_path else "", "sRGB")
            return f"ocio:{name}"
        current = self.input_transform_combo.currentData()
        return str(current) if current else f"ocio:{self.ocio_color_spaces[0]}"

    def _default_ocio_output_color_space(self, file_type: str) -> str:
        if self.color_workflow == COLOR_WORKFLOW_BUILTIN_OCIO:
            config = _builtin_ocio_config(self.builtin_ocio_config)
            name = config.default_output_by_file_type.get(file_type, "sRGB")
            return f"ocio:{name}"
        current = self.output_transform_combo.currentData()
        return str(current) if current else f"ocio:{self.ocio_color_spaces[0]}"

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

        if "video" in codec_option:
            video.update(deepcopy(codec_option["video"]))
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
        if "encoder_options" in profile_option:
            video["encoder_options"] = deepcopy(profile_option["encoder_options"])
        if file_type == "jpg":
            video["quality"] = _jpeg_quality_percent_to_qscale(self.jpg_quality_slider.value())
        if "audio_codec" in profile_option:
            audio["codec"] = profile_option["audio_codec"]
        if "sample_rate" in profile_option:
            audio["sample_rate"] = profile_option["sample_rate"]
        if "bitrate" in profile_option:
            audio["bitrate"] = profile_option["bitrate"]

        external_audio = self.external_audio_enabled()
        mux_without_video_encode = self.video_copy_audio_mux_enabled()
        if external_audio:
            audio = self.selected_external_audio_settings()
        if mux_without_video_encode:
            video = {"enabled": True, "codec": "copy"}
            filters = {
                "scale": "keep",
                "fps": "source",
                "force_even_dimensions": False,
                "copy_video_with_external_audio": True,
            }
            return Preset(
                id=f"{file_type}_copy_video_add_audio_{self.selected_external_audio_format()}",
                name=f"{OUTPUT_OPTIONS[file_type]['label']} / Copy Video + {self.audio_format_combo.currentText()} Audio",
                group=preset.group,
                output=output,
                video=video,
                audio=audio,
                filters=filters,
            )

        selected_input_transform = self.selected_input_transform()
        selected_output_transform = self.selected_output_transform()
        video.update(_video_color_metadata_for_output_transform(selected_output_transform, file_type))
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
            self.active_ocio_config_path(),
        )
        if ocio_lut:
            filters["lut3d"] = str(ocio_lut)
            filters["ocio_lut_method"] = "PyOpenColorIO Baker iridas_cube"
        elif _ocio_lut_is_required(selected_input_transform, selected_output_transform):
            filters["ocio_lut_error"] = "OCIO conversion selected, but LUT generation failed"
        if self.should_show_fps_control():
            fps = _parse_positive_float(self.fps_edit.text())
            filters["fps"] = fps if fps else "source"
        pixel_aspect = self.selected_pixel_aspect_for_path(self._current_preview_frame_path())
        filters["output_pixel_aspect"] = pixel_aspect
        filters["anamorph_output"] = self.selected_anamorph_output_mode()
        filters["preserve_pixel_aspect"] = self.selected_anamorph_output_mode() == ANAMORPH_OUTPUT_PRESERVE
        target_size = self.selected_output_raster_size()
        base_size = self.base_output_raster_size()
        if target_size and base_size and target_size != base_size:
            filters["scale"] = {
                "mode": "dimensions",
                "width": target_size.width(),
                "height": target_size.height(),
            }

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
        enabled = show_fps and not self.video_copy_audio_mux_enabled()
        self.fps_label.setEnabled(enabled)
        self.fps_edit.setEnabled(enabled)

    def refresh_jpg_quality_visibility(self) -> None:
        show_quality = self.selected_file_type() == "jpg"
        self.jpg_quality_label.setVisible(show_quality)
        self.jpg_quality_slider.setVisible(show_quality)
        self.jpg_quality_value_label.setVisible(show_quality)
        enabled = show_quality and not self.video_copy_audio_mux_enabled()
        self.jpg_quality_label.setEnabled(enabled)
        self.jpg_quality_slider.setEnabled(enabled)
        self.jpg_quality_value_label.setEnabled(enabled)

    def refresh_scale_controls(self) -> None:
        if not hasattr(self, "scale_slider"):
            return
        base_size = self.base_output_raster_size()
        enabled = base_size is not None and not self.video_copy_audio_mux_enabled()
        for widget in (self.scale_slider, self.output_width_spin, self.output_height_spin):
            widget.setEnabled(enabled)
        if not enabled:
            self.scale_label.setText("1.000")
            return
        self._set_output_size_from_scale(self.scale_slider.value() / 1000)

    def handle_scale_slider_changed(self, value: int) -> None:
        if self._updating_scale_controls:
            return
        self._set_output_size_from_scale(value / 1000)

    def handle_output_width_changed(self, value: int) -> None:
        if self._updating_scale_controls:
            return
        base_size = self.base_output_raster_size()
        if not base_size:
            return
        multiple = self.output_dimension_multiple()
        width = _rounded_output_dimension(value, multiple)
        scale = width / base_size.width()
        height = _rounded_output_dimension(width * base_size.height() / base_size.width(), multiple)
        self._set_scale_controls(width, height, scale)

    def handle_output_height_changed(self, value: int) -> None:
        if self._updating_scale_controls:
            return
        base_size = self.base_output_raster_size()
        if not base_size:
            return
        multiple = self.output_dimension_multiple()
        height = _rounded_output_dimension(value, multiple)
        scale = height / base_size.height()
        width = _rounded_output_dimension(height * base_size.width() / base_size.height(), multiple)
        self._set_scale_controls(width, height, scale)

    def _set_output_size_from_scale(self, scale: float) -> None:
        base_size = self.base_output_raster_size()
        if not base_size:
            return
        multiple = self.output_dimension_multiple()
        width = _rounded_output_dimension(base_size.width() * scale, multiple)
        height = _rounded_output_dimension(base_size.height() * scale, multiple)
        self._set_scale_controls(width, height, scale)

    def _set_scale_controls(self, width: int, height: int, scale: float) -> None:
        self._updating_scale_controls = True
        try:
            self.output_width_spin.setValue(width)
            self.output_height_spin.setValue(height)
            slider_value = max(self.scale_slider.minimum(), min(self.scale_slider.maximum(), round(scale * 1000)))
            self.scale_slider.setValue(slider_value)
            self.scale_label.setText(f"{slider_value / 1000:.3f}")
        finally:
            self._updating_scale_controls = False
        self.refresh_media_info_summary()

    def selected_output_raster_size(self) -> QSize | None:
        if not self.base_output_raster_size():
            return None
        return QSize(self.output_width_spin.value(), self.output_height_spin.value())

    def base_output_raster_size(self) -> QSize | None:
        if not self.current_source_raster_size:
            return None
        width = self.current_source_raster_size.width()
        height = self.current_source_raster_size.height()
        if width <= 0 or height <= 0:
            return None
        if self.selected_anamorph_output_mode() == ANAMORPH_OUTPUT_BAKE:
            pixel_aspect = self.selected_pixel_aspect_for_path(self._current_preview_frame_path())
            width = _rounded_output_dimension(width * pixel_aspect, 1)
        return QSize(width, height)

    def output_dimension_multiple(self) -> int:
        return 8 if self.selected_file_type() == "mp4" else 1

    def update_source_raster_size(self, size: QSize | None) -> None:
        if size is None or size.width() <= 0 or size.height() <= 0:
            return
        if self.current_source_raster_size == size:
            return
        self.current_source_raster_size = QSize(size)
        self.refresh_scale_controls()

    def refresh_pixel_aspect_controls(self) -> None:
        controls_enabled = not self.video_copy_audio_mux_enabled()
        manual = controls_enabled and self.selected_preview_pixel_aspect_mode() == PIXEL_ASPECT_MANUAL
        self.preview_pixel_aspect_combo.setEnabled(controls_enabled)
        self.anamorph_output_combo.setEnabled(controls_enabled)
        self.manual_pixel_aspect_edit.setEnabled(manual)
        if not manual:
            self._set_pixel_aspect_edit_value(self.selected_pixel_aspect_for_path(self._current_preview_frame_path()))
        self.refresh_preview_display_transform()
        self.refresh_scale_controls()

    def handle_manual_pixel_aspect_changed(self) -> None:
        self.refresh_preview_display_transform()
        self.refresh_scale_controls()

    def selected_preview_pixel_aspect_mode(self) -> str:
        return str(self.preview_pixel_aspect_combo.currentData())

    def selected_anamorph_output_mode(self) -> str:
        return str(self.anamorph_output_combo.currentData())

    def selected_pixel_aspect_for_path(self, path: Path | None) -> float:
        if self.selected_preview_pixel_aspect_mode() == PIXEL_ASPECT_MANUAL:
            return _normalized_pixel_aspect(_parse_positive_float(self.manual_pixel_aspect_edit.text()) or 1.0)
        return _pixel_aspect_for_path(path)

    def _sync_auto_pixel_aspect_edit(self, path: Path | None) -> None:
        if self.selected_preview_pixel_aspect_mode() == PIXEL_ASPECT_AUTO:
            self._set_pixel_aspect_edit_value(self.selected_pixel_aspect_for_path(path))

    def _set_pixel_aspect_edit_value(self, value: float) -> None:
        self.manual_pixel_aspect_edit.blockSignals(True)
        self.manual_pixel_aspect_edit.setText(_format_pixel_aspect(value))
        self.manual_pixel_aspect_edit.blockSignals(False)

    def _current_preview_frame_path(self) -> Path | None:
        if self.sequence_preview_frames:
            return self.sequence_preview_frames[max(0, min(self.sequence_frame_index, len(self.sequence_preview_frames) - 1))]
        return self.preview_source_path

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
            audio_input=Path(self.audio_input_edit.text()).expanduser() if self.external_audio_enabled() else None,
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
            if output_exists_for_job(job) and not job.overwrite:
                if not self.confirm_overwrite(job):
                    self.progress_label.setText("Cancelled")
                    return
                job = replace(job, overwrite=True)
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

    def confirm_overwrite(self, job: ConvertJob) -> bool:
        if "%" in job.output.name:
            message = f"Output sequence frames already exist:\n{job.output}\n\nOverwrite?"
        else:
            message = f"Output file already exists:\n{job.output}\n\nOverwrite?"
        result = QMessageBox.question(
            self,
            "Overwrite output?",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

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
        self.current_source_raster_size = None
        self.refresh_scale_controls()
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
        self.update_source_raster_size(self._last_video_source_pixmap.size())
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
        self.update_source_raster_size(pixmap.size())
        self._sync_auto_pixel_aspect_edit(frame)
        pixmap = self._display_pixmap_for_selected_input_transform(pixmap)
        self.preview_placeholder.set_source_pixmap(pixmap, self.selected_pixel_aspect_for_path(frame))
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
            self.active_ocio_config_path(),
        )
        self.preview_placeholder.set_source_pixmap(pixmap, self.selected_pixel_aspect_for_path(self._current_preview_frame_path()))

    def _display_pixmap_for_selected_input_transform(self, pixmap: QPixmap) -> QPixmap:
        return _display_pixmap_for_input_transform(
            pixmap,
            self.selected_input_transform(),
            self.active_ocio_config_path(),
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

        QPlainTextEdit#logText {
            background: #000000;
            color: #a8a8a8;
            border: 1px solid #3a3a3a;
            selection-background-color: #404040;
            selection-color: #e0e0e0;
        }

        QLabel:disabled {
            color: #8c8c8c;
        }

        QLineEdit:disabled,
        QComboBox:disabled,
        QSpinBox:disabled {
            background: #3a3a3a;
            color: #8c8c8c;
            border-color: #484848;
        }

        QComboBox:disabled::drop-down {
            border-color: #484848;
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

        QSlider::groove:horizontal:disabled {
            background: #383838;
            border-color: #484848;
        }

        QSlider::handle:horizontal:disabled {
            background: #5a5a5a;
            border-color: #666666;
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
    if _is_still_image_path(job.input):
        return None
    if probed_duration and probed_duration > 0:
        return probed_duration
    return None


def _progress_total_frames_for_job(job: ConvertJob) -> int | None:
    if "%" not in job.input.name:
        return None
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


def _rounded_output_dimension(value: float, multiple: int = 1) -> int:
    rounded = max(1, math.floor(float(value) + 0.5))
    if multiple <= 1:
        return rounded
    return max(multiple, (rounded // multiple) * multiple)


def _normalized_pixel_aspect(value: float | None) -> float:
    if value is None or not math.isfinite(value) or value <= 0:
        return 1.0
    return max(0.01, min(100.0, float(value)))


def _format_pixel_aspect(value: float) -> str:
    return f"{_normalized_pixel_aspect(value):.6g}"


def _pixel_aspect_for_path(path: Path | None) -> float:
    if path is None or path.suffix.lower() != ".exr" or not path.exists():
        return 1.0
    try:
        stat = path.stat()
    except OSError:
        return 1.0
    return _cached_exr_pixel_aspect_ratio(str(path), stat.st_mtime_ns)


@lru_cache(maxsize=1024)
def _cached_exr_pixel_aspect_ratio(path: str, _mtime_ns: int) -> float:
    try:
        import OpenEXR  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - optional preview dependency.
        return 1.0

    try:
        file_object = OpenEXR.InputFile(path)
    except Exception:  # noqa: BLE001 - invalid/unreadable EXR header should not break preview.
        return 1.0

    try:
        header = file_object.header()
        return _normalized_pixel_aspect(header.get("pixelAspectRatio", 1.0))
    except Exception:  # noqa: BLE001 - invalid/unreadable EXR header should not break preview.
        return 1.0
    finally:
        close = getattr(file_object, "close", None)
        if callable(close):
            close()


def _jpeg_quality_percent_to_qscale(value: int) -> int:
    clamped = max(0, min(100, value))
    return round(31 - (clamped / 100) * 29)


def _is_still_image_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in STILL_IMAGE_EXTENSIONS


def _command_color_space_value(value: str) -> str:
    return "none" if value.startswith("ocio:") else value


def _video_color_metadata_for_output_transform(output_transform: str, file_type: str) -> dict[str, str]:
    if file_type not in {"mov", "mp4"}:
        return {}
    color_space = _ocio_color_space_name(output_transform) or output_transform
    normalized = color_space.lower()
    if normalized in {"rec709", "rec.709", "bt709"}:
        return {
            "color_primaries": "bt709",
            "color_trc": "bt709",
            "colorspace": "bt709",
        }
    if normalized in {"srgb", "output - srgb"}:
        return {
            "color_primaries": "bt709",
            "color_trc": "iec61966-2-1",
            "colorspace": "bt709",
        }
    if normalized in {"linear"}:
        return {
            "color_primaries": "bt709",
            "color_trc": "linear",
            "colorspace": "bt709",
        }
    return {}


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
        processor = _ocio_preview_processor(
            str(path.resolve()),
            path.stat().st_mtime_ns,
            input_color_space,
        )
    except Exception:  # noqa: BLE001 - preview fallback must not break playback.
        return pixmap

    if pixmap.width() > OCIO_PREVIEW_MAX_SIZE.width() or pixmap.height() > OCIO_PREVIEW_MAX_SIZE.height():
        pixmap = pixmap.scaled(
            OCIO_PREVIEW_MAX_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    if np is None:
        return pixmap
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    try:
        return _ocio_display_pixmap_with_numpy(image, processor)
    except Exception:  # noqa: BLE001 - preview fallback must not break playback.
        return pixmap


def _ocio_display_pixmap_with_numpy(image: QImage, processor) -> QPixmap:  # noqa: ANN001 - optional OCIO type.
    width = image.width()
    height = image.height()
    bytes_per_line = image.bytesPerLine()
    raw = np.frombuffer(image.bits(), dtype=np.uint8, count=bytes_per_line * height)
    rows = raw.reshape((height, bytes_per_line))
    rgba_u8 = rows[:, : width * 4].reshape((height, width, 4))
    rgba = np.ascontiguousarray(rgba_u8, dtype=np.float32) / 255.0
    processor.applyRGBA(rgba)
    np.clip(rgba, 0.0, 1.0, out=rgba)
    out_u8 = np.rint(rgba * 255.0).astype(np.uint8)
    out_image = QImage(out_u8.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(out_image.copy())


@lru_cache(maxsize=32)
def _ocio_preview_processor(config_path: str, config_mtime_ns: int, input_color_space: str):  # noqa: ANN001, ARG001
    config = ocio.Config.CreateFromFile(config_path)
    display_color_space = _ocio_display_color_space(config)
    if not display_color_space:
        raise ValueError("OCIO display color space not found")
    return config.getProcessor(input_color_space, display_color_space).getDefaultCPUProcessor()


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


def _video_size_from_probe(probe_json: dict) -> QSize | None:
    for stream in probe_json.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        try:
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return QSize(width, height)
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
    output_resolution: QSize | None = None,
    output_codec: str | None = None,
    output_color_space: str | None = None,
) -> str:
    lines: list[str] = ["Input"]
    fmt = probe_json.get("format", {})
    video_stream = _first_stream(probe_json, "video")
    audio_stream = _first_stream(probe_json, "audio")
    resolution = _stream_resolution(video_stream)
    if resolution:
        lines.append(f"Resolution: {resolution}")
    else:
        lines.append("Resolution: unknown")
    lines.append(f"FPS: {_format_summary_fps(fps or _fps_from_probe(probe_json), video_stream)}")
    lines.append(f"Codec: {_summary_codec(video_stream or audio_stream)}")
    if fmt:
        lines.append(f"Format: {fmt.get('format_name', 'unknown')}")
        lines.append(_duration_summary_line(
            probe_json,
            input_path=input_path,
            fps=fps,
            sequence_frame_count=sequence_frame_count,
        ))
        lines.append(f"Size: {_format_file_size(_summary_size_value(fmt.get('size'), input_path))}")
    lines.append(f"Created by: {_summary_created_by(probe_json, input_path)}")

    if audio_stream:
        lines.append("")
        lines.append("Audio")
        lines.append(f"Codec: {_summary_codec(audio_stream)}")
        lines.append(f"Sample rate: {audio_stream.get('sample_rate', 'unknown')}")
        lines.append(f"Channels: {audio_stream.get('channels', 'unknown')}")

    if output_resolution is not None or output_codec or output_color_space:
        lines.append("")
        lines.append("Output")
        if output_resolution is not None:
            lines.append(f"Resolution: {output_resolution.width()} x {output_resolution.height()} px")
        else:
            lines.append("Resolution: unknown")
        lines.append(f"FPS: {_format_summary_fps(fps, None)}")
        lines.append(f"Codec: {output_codec or 'unknown'}")
        lines.append(f"Color Space: {output_color_space or 'unknown'}")
    return "\n".join(lines)


def _first_stream(probe_json: dict, stream_type: str) -> dict | None:
    for stream in probe_json.get("streams", []):
        if stream.get("codec_type") == stream_type:
            return stream
    return None


def _stream_resolution(stream: dict | None) -> str | None:
    if not stream:
        return None
    width = stream.get("width")
    height = stream.get("height")
    if width is None or height is None:
        return None
    return f"{width} x {height} px"


def _summary_codec(stream: dict | None) -> str:
    if not stream:
        return "unknown"
    return str(stream.get("codec_long_name") or stream.get("codec_name") or "unknown")


def _format_summary_fps(fps: float | None, stream: dict | None) -> str:
    safe_fps = fps
    if not safe_fps and stream:
        safe_fps = _fraction_to_float(stream.get("avg_frame_rate"))
    if not safe_fps:
        return "unknown"
    return f"{safe_fps:.3f}".rstrip("0").rstrip(".")


def _summary_created_by(probe_json: dict, input_path: Path | None = None) -> str:
    for tags in _summary_tag_sources(probe_json):
        for key in ("encoded_by", "encoder", "writing_application", "software", "creation_app"):
            value = tags.get(key)
            if value:
                return str(value)
    header = _openexr_header(_metadata_source_path(input_path))
    if header:
        for key in ("software", "cameraSoftwarePackageName", "owner"):
            value = header.get(key)
            if value:
                return _metadata_value_to_string(value)
    return "unknown"


def _summary_tag_sources(probe_json: dict) -> list[dict]:
    sources: list[dict] = []
    fmt = probe_json.get("format", {})
    if isinstance(fmt.get("tags"), dict):
        sources.append(fmt["tags"])
    for stream in probe_json.get("streams", []):
        if isinstance(stream.get("tags"), dict):
            sources.append(stream["tags"])
    return sources


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


def _metadata_rows(probe_json: dict, input_path: Path | None = None) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    exr_path = _metadata_source_path(input_path)
    if exr_path and exr_path.suffix.lower() == ".exr":
        rows.extend(_openexr_metadata_rows(exr_path))
    rows.extend(_flatten_metadata_rows("ffprobe", "root", probe_json))
    return rows


def _metadata_display_key(source: str, group: str, key: str) -> str:
    return key


def _metadata_full_key(source: str, group: str, key: str) -> str:
    parts = [part for part in (source, "" if group == "root" else group, key) if part]
    return ".".join(parts)


def _metadata_source_path(input_path: Path | None) -> Path | None:
    if input_path is None:
        return None
    frames = sequence_frames(input_path)
    if frames:
        return frames[0]
    if input_path.exists():
        return input_path
    return None


def _openexr_metadata_rows(path: Path) -> list[tuple[str, str, str, str]]:
    header = _openexr_header(path)
    if not header:
        return []
    rows: list[tuple[str, str, str, str]] = []
    for key in sorted(header):
        rows.append(("OpenEXR", "header", str(key), _metadata_value_to_string(header[key])))
    return rows


def _openexr_header(path: Path | None) -> dict | None:
    if path is None or path.suffix.lower() != ".exr" or not path.exists():
        return None
    try:
        import OpenEXR  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - OpenEXR is optional for metadata display.
        return None
    try:
        file_object = OpenEXR.InputFile(str(path))
    except Exception:  # noqa: BLE001 - unreadable EXR metadata should not break probe.
        return None
    try:
        return dict(file_object.header())
    except Exception:  # noqa: BLE001 - unreadable EXR metadata should not break probe.
        return None
    finally:
        close = getattr(file_object, "close", None)
        if callable(close):
            close()


def _flatten_metadata_rows(
    source: str,
    group: str,
    value: object,
    key_prefix: str = "",
) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            child = value[key]
            key_text = str(key)
            if isinstance(child, dict):
                rows.extend(_flatten_metadata_rows(source, _join_metadata_path(group, key_text), child))
            elif isinstance(child, list):
                rows.extend(_flatten_metadata_rows(source, _join_metadata_path(group, key_text), child))
            else:
                rows.append((source, group, f"{key_prefix}{key_text}", _metadata_value_to_string(child)))
        return rows
    if isinstance(value, list):
        for index, child in enumerate(value):
            indexed_group = f"{group}[{index}]"
            if isinstance(child, (dict, list)):
                rows.extend(_flatten_metadata_rows(source, indexed_group, child))
            else:
                rows.append((source, group, str(index), _metadata_value_to_string(child)))
        return rows
    rows.append((source, group, key_prefix.rstrip(".") or "value", _metadata_value_to_string(value)))
    return rows


def _join_metadata_path(group: str, key: str) -> str:
    if not group or group == "root":
        return key
    return f"{group}.{key}"


def _metadata_value_to_string(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value)
    return str(value)


def _frame_number_from_path(path: Path) -> int | None:
    match = split_sequence_name(path.name)
    if match is None:
        return None
    return int(match[1])


if __name__ == "__main__":
    raise SystemExit(main())
