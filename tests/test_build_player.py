from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from seventh_convert import build_player


@unittest.skipUnless(sys.platform == "linux" and shutil.which("sleep") and shutil.which("true"),
                     "Checks Linux replacement of an executable currently in use")
class BuildPlayerTests(unittest.TestCase):
    def test_install_while_old_player_is_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "seventh_convert"
            destination = package / "bin" / "seventh-player"
            destination.parent.mkdir(parents=True)
            manifest = root / "rust" / "player" / "Cargo.toml"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("# Synthetic manifest; Cargo execution is mocked.\n")
            manifest.with_name("Cargo.lock").write_text("")
            release = manifest.parent / "target" / "release" / destination.name
            release.parent.mkdir(parents=True)
            shutil.copy2(shutil.which("sleep"), destination)
            shutil.copy2(shutil.which("true"), release)
            os.utime(destination, (1, 1))
            old_player = subprocess.Popen([str(destination), "60"])
            try:
                with patch.object(build_player, "__file__", str(package / "build_player.py")), \
                     patch.object(sys, "argv", ["build_player"]), \
                     patch.object(build_player.shutil, "which", return_value="cargo"), \
                     patch.object(build_player.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                    self.assertEqual(build_player.main(), 0)
                self.assertIsNone(old_player.poll())
                self.assertEqual(destination.read_bytes(), release.read_bytes())
                self.assertEqual(list(destination.parent.iterdir()), [destination])
            finally:
                old_player.terminate()
                old_player.wait(timeout=5)
