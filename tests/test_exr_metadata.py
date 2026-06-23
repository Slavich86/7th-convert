from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from seventh_convert.command_builder import ConvertJob
from seventh_convert.exr_metadata import copy_exr_header_metadata, exr_metadata_frame_pairs
from seventh_convert.presets import get_preset


class ExrMetadataTests(unittest.TestCase):
    def test_exr_sequence_metadata_pairs_use_output_start_as_source_frame_after_in_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            for frame in range(1000, 1006):
                (source / f"shot.{frame:04d}.exr").touch()
            for frame in range(1003, 1006):
                (output / f"shot_rec709.{frame:04d}.exr").touch()

            job = ConvertJob(
                input=source / "shot.%04d.exr",
                output=output / "shot_rec709.%04d.exr",
                preset=get_preset("exr_sequence"),
                input_start_number=1000,
                output_start_number=1003,
            )

            pairs = exr_metadata_frame_pairs(job)

            self.assertEqual(
                [(src.name, dst.name) for src, dst in pairs],
                [
                    ("shot.1003.exr", "shot_rec709.1003.exr"),
                    ("shot.1004.exr", "shot_rec709.1004.exr"),
                    ("shot.1005.exr", "shot_rec709.1005.exr"),
                ],
            )

    def test_copy_exr_header_metadata_preserves_camera_tags_without_replacing_output_core_header(self):
        try:
            import OpenEXR
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"OpenEXR unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.exr"
            output = root / "output.exr"
            _write_test_exr(OpenEXR, source, cameraModel=b"ARRI ALEXA Mini")
            _write_test_exr(OpenEXR, output, writer=b"lavc")

            copy_exr_header_metadata(source, output)

            header = OpenEXR.InputFile(str(output)).header()
            self.assertEqual(header["cameraModel"], b"ARRI ALEXA Mini")
            self.assertEqual(header["writer"], b"lavc")
            self.assertIn("compression", header)


def _write_test_exr(OpenEXR, path: Path, **metadata) -> None:  # noqa: ANN001
    header = OpenEXR.Header(1, 1)
    header.update(metadata)
    data = struct.pack("f", 0.5)
    writer = OpenEXR.OutputFile(str(path), header)
    try:
        writer.writePixels({"R": data, "G": data, "B": data})
    finally:
        close = getattr(writer, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    unittest.main()
