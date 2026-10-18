from __future__ import annotations

import json
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from seventh_convert.player import player_executable


class Engine:
    def __init__(self):
        self.process = subprocess.Popen([str(player_executable())], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.events = queue.Queue()
        self.generation = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while line := self.process.stdout.readline():
                header = json.loads(line)
                payload = self.process.stdout.read(header.get("bytes", 0))
                self.events.put((header, payload))
        except Exception as exc:
            self.events.put(({"event": "error", "message": str(exc)}, b""))

    def send(self, command, **kwargs):
        if command in {"open", "seek", "scrub", "step", "close"}:
            self.generation += 1
        self.process.stdin.write(json.dumps({"command": command, "generation": self.generation, **kwargs}).encode() + b"\n")
        self.process.stdin.flush()

    def receive(self, kind="frame", timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            header, pixels = self.events.get(timeout=max(deadline - time.monotonic(), .01))
            if header.get("generation") != self.generation:
                continue
            if header["event"] == "error" and kind != "error":
                raise AssertionError(header["message"])
            if header["event"] == kind:
                if kind == "frame":
                    self.send("ack")
                return header, pixels
        raise AssertionError(f"No {kind} event")

    def close(self):
        self.send("quit")
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise AssertionError("Engine did not shut down")
        self.reader.join(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()
        self.process.stderr.close()


@unittest.skipUnless(player_executable() and shutil.which("ffmpeg"), "Build Rust player and install FFmpeg")
class RustPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video = cls.root / "source.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=32x24:rate=10", "-c:v", "ffv1", str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.engine = Engine()
        self.source = {"path": str(self.video), "files": [], "fps_num": 10, "fps_den": 1, "filter": ""}

    def tearDown(self):
        self.engine.close()

    def test_playback_seek_step_and_restart(self):
        self.engine.send("open", source=self.source)
        header, pixels = self.engine.receive()
        self.assertEqual((header["position_us"], len(pixels)), (0, 32 * 24 * 4))
        self.engine.send("seek", position_us=500_000)
        self.assertEqual(self.engine.receive()[0]["position_us"], 500_000)
        self.engine.send("step", delta=-1)
        self.assertEqual(self.engine.receive()[0]["position_us"], 400_000)
        self.engine.send("step", delta=1)
        self.assertEqual(self.engine.receive()[0]["position_us"], 500_000)
        self.engine.send("play")
        stamps = [self.engine.receive()[0]["position_us"] for _ in range(4)]
        self.assertEqual(stamps, [600_000, 700_000, 800_000, 900_000])
        while self.engine.receive("state")[0]["state"] != "ended":
            pass
        self.engine.send("play")
        self.assertEqual(self.engine.receive()[0]["position_us"], 0)

    def test_preview_scaling_preserves_source_geometry_and_timestamps(self):
        self.engine.send("open", source={**self.source, "max_width": 16, "max_height": 16})
        header, pixels = self.engine.receive()
        self.assertEqual((header["width"], header["height"]), (16, 12))
        self.assertEqual((header["source_width"], header["source_height"]), (32, 24))
        self.assertEqual(len(pixels), 16 * 12 * 4)
        self.engine.send("seek", position_us=500_000)
        self.assertEqual(self.engine.receive()[0]["position_us"], 500_000)
        self.engine.send("open", source={**self.source, "max_width": 64, "max_height": 64})
        header, _ = self.engine.receive()
        self.assertEqual((header["width"], header["height"]), (32, 24))
        self.engine.send("open", source={**self.source, "max_width": 999999, "max_height": 64})
        self.assertIn("Preview bounds", self.engine.receive("error")[0]["message"])

    def test_scrub_forward_cache_backwards_then_exact_seek_and_play(self):
        self.engine.send("open", source=self.source)
        self.engine.receive()
        self.engine.send("scrub", position_us=100_000)
        first, pixels = self.engine.receive()
        self.assertEqual(first["position_us"], 100_000)
        self.engine.send("scrub", position_us=300_000)
        self.assertEqual(self.engine.receive()[0]["position_us"], 300_000)
        self.engine.send("scrub", position_us=100_000)
        self.assertEqual(self.engine.receive()[1], pixels)
        # Release always performs an exact seek, even if dragging used a nearby
        # cached preview. Ordinary play must then deliver every following frame.
        self.engine.send("seek", position_us=500_000)
        self.assertEqual(self.engine.receive()[0]["position_us"], 500_000)
        self.engine.send("play")
        self.assertEqual([self.engine.receive()[0]["position_us"] for _ in range(4)],
                         [600_000, 700_000, 800_000, 900_000])

    def test_seek_past_end_holds_last_frame(self):
        self.engine.send("open", source=self.source)
        self.engine.receive()
        self.engine.send("seek", position_us=1_000_000)
        self.assertEqual(self.engine.receive()[0]["position_us"], 900_000)

    def test_reopen_rejects_old_frames_and_reports_bad_file(self):
        self.engine.send("open", source=self.source)
        self.engine.send("seek", position_us=700_000)
        self.assertEqual(self.engine.receive()[0]["position_us"], 700_000)
        bad = {**self.source, "path": str(self.root / "missing.mkv")}
        self.engine.send("open", source=bad)
        header, _ = self.engine.receive("error")
        self.assertIn("missing.mkv", header["message"])
        self.engine.send("open", source=self.source)
        self.assertEqual(self.engine.receive()[0]["position_us"], 0)

    def test_sequence_with_gaps_quotes_and_fractional_fps(self):
        files = []
        for index in (1001, 1003, 1008):
            path = self.root / f"quote' frame.{index:04d}.png"
            if not path.exists():
                subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.video), "-frames:v", "1", str(path)], check=True)
            files.append(str(path))
        source = {**self.source, "path": files[0], "files": files, "fps_num": 24000, "fps_den": 1001}
        self.engine.send("open", source=source)
        self.assertEqual(self.engine.receive()[0]["index"], 0)
        self.engine.send("step", delta=1)
        header, _ = self.engine.receive()
        self.assertEqual((header["index"], header["position_us"]), (1, 41708))
        self.engine.send("step", delta=-1)
        self.assertEqual(self.engine.receive()[0]["index"], 0)
        self.engine.send("play")
        self.assertEqual(self.engine.receive()[0]["index"], 1)
        self.assertEqual(self.engine.receive()[0]["index"], 2)

    def test_color_pixels_match_ffmpeg_before_and_during_play(self):
        filter_spec = "lutrgb=r=val*0.5:g=val*0.75:b=val"
        self.engine.send("open", source={**self.source, "filter": filter_spec})
        _, pixels = self.engine.receive()
        expected = subprocess.run(["ffmpeg", "-v", "error", "-i", str(self.video), "-vf", filter_spec, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"], check=True, capture_output=True).stdout
        self.assertEqual(pixels, expected)
        self.engine.send("play")
        header, pixels = self.engine.receive()
        expected = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(header["position_us"] / 1e6), "-i", str(self.video), "-vf", filter_spec, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"], check=True, capture_output=True).stdout
        self.assertEqual(pixels, expected)

    def test_vfr_timestamps_and_step_at_end(self):
        video = self.root / "variable.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(self.video), "-vf", "setpts=if(lt(N\\,5)\\,N\\,5+(N-5)*2)/(10*TB)", "-fps_mode", "vfr", "-c:v", "ffv1", str(video)], check=True)
        self.engine.send("open", source={**self.source, "path": str(video)})
        stamps = [self.engine.receive()[0]["position_us"]]
        self.engine.send("play")
        stamps += [self.engine.receive()[0]["position_us"] for _ in range(9)]
        self.assertEqual(stamps, [0, 100000, 200000, 300000, 400000, 500000, 700000, 900000, 1100000, 1300000])
        self.engine.send("step", delta=1)
        self.assertEqual(self.engine.receive()[0]["position_us"], 1300000)
        self.engine.send("step", delta=-1)
        self.assertEqual(self.engine.receive()[0]["position_us"], 1100000)

    def test_float_exr_color_and_straight_alpha(self):
        import numpy as np
        import OpenEXR
        from dataclasses import replace
        from seventh_convert.presets import get_preset
        from seventh_convert.command_builder import build_player_color_filter
        source = self.root / "linear.exr"
        channels = {name: np.full((4, 4), value, dtype=np.float32) for name, value in (("R", .25), ("G", .5), ("B", .75), ("A", .5))}
        OpenEXR.File({"pixelAspectRatio": 2.0}, channels).write(str(source))
        lut = self.root / "test.cube"
        # Synthetic test-only LUT, generated here: scale channels by one half.
        lut.write_text("LUT_3D_SIZE 2\n" + "".join(f"{r*.5} {g*.5} {b*.5}\n" for b in range(2) for g in range(2) for r in range(2)))
        preset = replace(get_preset("png_sequence"), filters={"lut3d": str(lut)})
        transform = build_player_color_filter(preset)
        self.engine.send("open", source={**self.source, "path": str(source), "files": [str(source), str(source)], "filter": transform})
        header, pixels = self.engine.receive()
        expected = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-vf", transform, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"], check=True, capture_output=True).stdout
        self.assertEqual(pixels, expected)
        self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(pixels[:4], (32, 64, 96, 128))), list(pixels[:4]))
        self.engine.send("play")
        self.assertEqual(self.engine.receive()[1], expected)

    def test_scaled_lut_keeps_hdr_precision_and_alpha_on_pause_and_play(self):
        import numpy as np
        import OpenEXR

        source = self.root / "scaled-hdr.exr"
        # Original synthetic fixture: alternating HDR pixels expose clipping
        # before downsampling, and the nonlinear LUT exposes filter ordering.
        yy, xx = np.indices((8, 8))
        values = np.where((xx + yy) % 2, 1.4, .1).astype(np.float32)
        channels = {"R": values, "G": values * .6, "B": values * .3,
                    "A": np.full((8, 8), .5, dtype=np.float32)}
        OpenEXR.File({}, channels).write(str(source))
        lut = self.root / "nonlinear.cube"
        # Generated test-only LUT; no external assets.
        lut.write_text("LUT_3D_SIZE 3\n" + "".join(
            f"{(r/2)**2} {(g/2)**2} {(b/2)**2}\n"
            for b in range(3) for g in range(3) for r in range(3)))
        transform = f"lut3d=file='{lut}':interp=tetrahedral"

        def reference(filters):
            return subprocess.run([
                "ffmpeg", "-v", "error", "-filter_threads", "8", "-i", str(source),
                "-vf", filters, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"
            ], check=True, capture_output=True).stdout

        expected = reference(f"scale=4:4,format=gbrapf32le,{transform}")
        self.assertNotEqual(expected, reference(f"scale=4:4,format=rgba,{transform}"))
        self.assertNotEqual(expected, reference(f"{transform},scale=4:4"))
        self.engine.send("open", source={**self.source, "path": str(source),
                         "files": [str(source), str(source)], "filter": transform,
                         "max_width": 4, "max_height": 4})
        header, pixels = self.engine.receive()
        self.assertEqual((header["source_width"], header["source_height"]), (8, 8))
        self.assertEqual((header["width"], header["height"]), (4, 4))
        self.assertEqual(pixels, expected)
        self.assertTrue(all(abs(a - 128) <= 1 for a in pixels[3::4]))
        self.engine.send("play")
        self.assertEqual(self.engine.receive()[1], expected)

    def test_backpressure_and_close_during_decode(self):
        self.engine.send("open", source=self.source)
        self.engine.send("play")
        # Without an acknowledgement the engine must send at most one frame.
        first = None
        while first is None:
            header, _ = self.engine.events.get(timeout=5)
            if header["event"] == "frame":
                first = header
        self.engine.send("close")
        self.assertEqual(self.engine.receive("state")[0]["state"], "paused")
