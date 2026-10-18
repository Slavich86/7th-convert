from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from seventh_convert.command_builder import (
    ConvertJob,
    build_ffmpeg_args,
    build_preview_frame_args,
    build_preview_proxy_args,
)
from seventh_convert.presets import get_preset


class ColorPreviewCommandTests(unittest.TestCase):
    def test_preview_frame_reuses_export_filter_chain(self) -> None:
        preset = get_preset("png_sequence")
        preset = replace(
            preset,
            filters={
                **preset.filters,
                "lut3d": "/tmp/look.cube",
                "input_color_space": "rec709",
                "output_color_space": "srgb",
            },
        )
        export_args = build_ffmpeg_args(
            ConvertJob(
                input=Path("input.mov"),
                output=Path("output.%04d.png"),
                preset=preset,
            )
        )
        preview_args = build_preview_frame_args(Path("input.mov"), Path("preview.png"), preset)

        self.assertEqual(preview_args[preview_args.index("-vf") + 1], export_args[export_args.index("-vf") + 1])

    def test_preview_frame_seek_inserts_ss_before_input(self) -> None:
        preset = get_preset("png_sequence")
        args = build_preview_frame_args(Path("input.mov"), Path("preview.png"), preset, position_ms=1500)

        self.assertEqual(args[args.index("-ss") + 1], "00:00:01.500")
        self.assertLess(args.index("-ss"), args.index("-i"))

    def test_preview_frame_without_position_has_no_seek(self) -> None:
        preset = get_preset("png_sequence")
        args = build_preview_frame_args(Path("input.mov"), Path("preview.png"), preset)

        self.assertNotIn("-ss", args)

    def test_preview_frame_without_output_uses_stdout_pipe(self) -> None:
        preset = get_preset("png_sequence")
        args = build_preview_frame_args(Path("input.mov"), None, preset)

        self.assertIn("-f", args)
        self.assertEqual(args[args.index("-f") + 1], "image2pipe")
        self.assertEqual(args[-1], "pipe:1")

    def test_preview_proxy_reuses_export_filter_chain_before_proxy_scale(self) -> None:
        preset = get_preset("png_sequence")
        preset = replace(preset, filters={**preset.filters, "lut3d": "/tmp/look.cube"})
        export_args = build_ffmpeg_args(
            ConvertJob(
                input=Path("input.mov"),
                output=Path("output.%04d.png"),
                preset=preset,
            )
        )
        proxy_args = build_preview_proxy_args(Path("input.mov"), Path("preview.mp4"), preset, 1280)

        export_filter = export_args[export_args.index("-vf") + 1]
        proxy_filter = proxy_args[proxy_args.index("-vf") + 1]
        self.assertTrue(proxy_filter.startswith(f"{export_filter},"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
    def test_preview_frame_extracts_requested_position(self) -> None:
        preset = get_preset("png_sequence")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mkv"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-y", "-f", "lavfi",
                    "-i", "testsrc=duration=2:size=32x32:rate=25", "-c:v", "ffv1", str(source),
                ],
                check=True, capture_output=True, text=True,
            )
            completed = subprocess.run(
                build_preview_frame_args(source, None, preset, position_ms=1000),
                capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
            self.assertTrue(completed.stdout)


if __name__ == "__main__":
    unittest.main()
