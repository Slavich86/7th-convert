from pathlib import Path
import unittest

from seventh_convert.command_builder import ConvertJob, build_ffmpeg_args
from seventh_convert.presets import get_preset


class CommandBuilderTests(unittest.TestCase):
    def test_h264_command_uses_args_not_shell_string(self):
        job = ConvertJob(
            input=Path("input file.mov"),
            output=Path("output file.mp4"),
            preset=get_preset("h264_mp4"),
            overwrite=True,
        )

        args = build_ffmpeg_args(job)

        self.assertEqual(args[0], "ffmpeg")
        self.assertEqual(args[1], "-hide_banner")
        self.assertIn("-i", args)
        self.assertIn("input file.mov", args)
        self.assertIn("-c:v", args)
        self.assertIn("libx264", args)
        self.assertEqual("output file.mp4", args[-1])

    def test_prores_hq_command_sets_profile_and_pix_fmt(self):
        job = ConvertJob(
            input=Path("input.mov"),
            output=Path("output.mov"),
            preset=get_preset("prores_hq_mov"),
        )

        args = build_ffmpeg_args(job)

        self.assertEqual(args[args.index("-c:v") + 1], "prores_ks")
        self.assertEqual(args[args.index("-profile:v") + 1], "hq")
        self.assertEqual(args[args.index("-pix_fmt") + 1], "yuv422p10le")

    def test_wav_preset_disables_video(self):
        job = ConvertJob(
            input=Path("input.mov"),
            output=Path("output.wav"),
            preset=get_preset("wav_pcm"),
        )

        args = build_ffmpeg_args(job)

        self.assertIn("-vn", args)
        self.assertEqual(args[args.index("-c:a") + 1], "pcm_s24le")

    def test_image_sequence_start_number_is_input_option_before_i(self):
        job = ConvertJob(
            input=Path("DSCF0358_%04d.jpg"),
            output=Path("output.mp4"),
            preset=get_preset("h264_mp4"),
            input_start_number=1000,
        )

        args = build_ffmpeg_args(job)

        self.assertLess(args.index("-start_number"), args.index("-i"))
        self.assertEqual(args[args.index("-start_number") + 1], "1000")

    def test_image_sequence_output_start_number_is_output_option(self):
        job = ConvertJob(
            input=Path("DSCF0358_%04d.jpg"),
            output=Path("DSCF0358.%04d.png"),
            preset=get_preset("png_sequence"),
            input_start_number=1000,
            output_start_number=1145,
        )

        args = build_ffmpeg_args(job)
        start_number_indices = [index for index, arg in enumerate(args) if arg == "-start_number"]

        self.assertEqual(len(start_number_indices), 2)
        self.assertLess(start_number_indices[0], args.index("-i"))
        self.assertGreater(start_number_indices[1], args.index("-i"))
        self.assertLess(start_number_indices[1], len(args) - 1)
        self.assertEqual(args[start_number_indices[0] + 1], "1000")
        self.assertEqual(args[start_number_indices[1] + 1], "1145")
        self.assertEqual(args[-1], "DSCF0358.%04d.png")

    def test_exr_command_uses_half_float_and_compression(self):
        job = ConvertJob(
            input=Path("input.mov"),
            output=Path("output.%04d.exr"),
            preset=get_preset("exr_sequence"),
        )

        args = build_ffmpeg_args(job)

        self.assertEqual(args[args.index("-c:v") + 1], "exr")
        self.assertEqual(args[args.index("-format") + 1], "half")
        self.assertEqual(args[args.index("-compression") + 1], "zip1")

    def test_color_transform_adds_zscale_filter(self):
        preset = get_preset("exr_sequence")
        preset.filters["input_color_space"] = "srgb"
        preset.filters["output_color_space"] = "linear"
        job = ConvertJob(
            input=Path("input.%04d.jpg"),
            output=Path("output.%04d.exr"),
            preset=preset,
        )

        args = build_ffmpeg_args(job)

        self.assertEqual(args[args.index("-vf") + 1], "zscale=transferin=iec61966-2-1:transfer=linear")

    def test_rec709_to_srgb_transform_adds_zscale_filter(self):
        preset = get_preset("h264_mp4")
        preset.filters["input_color_space"] = "rec709"
        preset.filters["output_color_space"] = "srgb"
        job = ConvertJob(
            input=Path("input.mov"),
            output=Path("output.mp4"),
            preset=preset,
        )

        args = build_ffmpeg_args(job)

        self.assertTrue(args[args.index("-vf") + 1].startswith("zscale=transferin=bt709:transfer=iec61966-2-1"))


if __name__ == "__main__":
    unittest.main()
