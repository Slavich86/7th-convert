from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from seventh_convert.command_builder import ConvertJob, build_ffmpeg_args
from seventh_convert.presets import get_preset
from seventh_convert.ui import _output_color_metadata, _video_color_metadata_for_output_transform


class StillImageColorMetadataTests(unittest.TestCase):
    def test_png_srgb_metadata_is_full_range_srgb(self) -> None:
        self.assertEqual(
            _video_color_metadata_for_output_transform("srgb", "png"),
            {
                "color_primaries": "bt709",
                "color_trc": "iec61966-2-1",
                "colorspace": "bt709",
                "color_range": "pc",
            },
        )

    def test_jpg_srgb_metadata_is_full_range_srgb(self) -> None:
        self.assertEqual(
            _video_color_metadata_for_output_transform("srgb", "jpg"),
            {
                "color_primaries": "bt709",
                "color_trc": "iec61966-2-1",
                "colorspace": "bt709",
                "color_range": "pc",
            },
        )

    def test_ocio_srgb_texture_metadata_is_full_range_srgb(self) -> None:
        self.assertEqual(
            _video_color_metadata_for_output_transform("ocio:srgb_texture", "png"),
            {
                "color_primaries": "bt709",
                "color_trc": "iec61966-2-1",
                "colorspace": "bt709",
                "color_range": "pc",
            },
        )

    def test_manual_srgb_policy_overrides_unknown_ocio_name(self) -> None:
        self.assertEqual(
            _output_color_metadata("srgb", "ocio:studio_custom_output", "png"),
            {
                "color_primaries": "bt709",
                "color_trc": "iec61966-2-1",
                "colorspace": "bt709",
                "color_range": "pc",
            },
        )

    def test_preserve_policy_does_not_override_source_metadata(self) -> None:
        self.assertEqual(_output_color_metadata("preserve", "ocio:srgb_texture", "png"), {})

    def test_video_srgb_metadata_does_not_force_full_range(self) -> None:
        self.assertEqual(
            _video_color_metadata_for_output_transform("srgb", "mp4"),
            {
                "color_primaries": "bt709",
                "color_trc": "iec61966-2-1",
                "colorspace": "bt709",
            },
        )

    def test_png_linear_to_srgb_args_include_transfer_and_metadata(self) -> None:
        preset = get_preset("png_sequence")
        metadata = _video_color_metadata_for_output_transform("srgb", "png")
        preset = replace(
            preset,
            video={**preset.video, **metadata},
            filters={**preset.filters, "input_color_space": "linear", "output_color_space": "srgb"},
        )
        args = build_ffmpeg_args(
            ConvertJob(
                input=Path("input.%04d.exr"),
                output=Path("output.%04d.png"),
                preset=preset,
                overwrite=True,
            )
        )

        self.assertIn("-color_primaries", args)
        self.assertIn("bt709", args)
        self.assertIn("-color_trc", args)
        self.assertIn("iec61966-2-1", args)
        self.assertIn("-colorspace", args)
        self.assertIn("-color_range", args)
        self.assertIn("pc", args)
        self.assertIn(
            "zscale=transferin=linear:transfer=iec61966-2-1,"
            "setparams=color_primaries=bt709:color_trc=iec61966-2-1:colorspace=bt709:range=pc",
            args,
        )


if __name__ == "__main__":
    unittest.main()
