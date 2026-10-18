from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from seventh_convert.command_builder import ConvertJob, build_ffmpeg_args
from seventh_convert.presets import get_preset


class FrameStepCommandTests(unittest.TestCase):
    def test_frame_step_constrains_global_multiples_to_the_selected_range(self) -> None:
        args = build_ffmpeg_args(
            ConvertJob(
                input=Path("input.mp4"),
                output=Path("output.%04d.png"),
                preset=get_preset("png_sequence"),
                frame_step=5,
                source_frame_start=102,
            )
        )

        self.assertIn(
            r"setpts=N+1,select='gte(n\,102)*(eq(n\,102)+not(mod(n+1\,5)))'",
            args,
        )
        self.assertIn("-frame_pts", args)
        self.assertIn("-fps_mode", args)
        self.assertEqual(args[args.index("-fps_mode") + 1], "passthrough")
        self.assertNotIn("-ss", args)
        self.assertNotIn("-to", args)

    def test_unsampled_job_keeps_its_time_range_arguments(self) -> None:
        args = build_ffmpeg_args(
            ConvertJob(
                input=Path("input.mp4"),
                output=Path("output.%04d.png"),
                preset=get_preset("png_sequence"),
                in_point="00:00:01.000",
                out_point="00:00:02.000",
            )
        )

        self.assertIn("-ss", args)
        self.assertIn("-to", args)
        self.assertNotIn("-frame_pts", args)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
    def test_frame_step_writes_source_frame_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mkv"
            output = root / "output.%04d.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=duration=5:size=16x16:rate=24",
                    "-c:v",
                    "ffv1",
                    str(source),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(
                build_ffmpeg_args(
                    ConvertJob(
                        input=source,
                        output=output,
                        preset=get_preset("png_sequence"),
                        frame_step=5,
                        source_frame_start=102,
                        source_frame_end=120,
                        overwrite=True,
                    )
                ),
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(
                [path.name for path in sorted(root.glob("output.*.png"))],
                ["output.0103.png", "output.0105.png", "output.0110.png", "output.0115.png", "output.0120.png"],
            )


if __name__ == "__main__":
    unittest.main()
