from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_publish


class PublishTests(unittest.TestCase):
    def test_regeneration_removes_stale_files_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "converter.py"
            source.write_text("print('converter')\n")
            output = root / "publish"
            output.mkdir()
            (output / "old.log").write_text("stale")
            (output / ".venv").mkdir()
            with patch.object(prepare_publish, "ROOT", root), \
                 patch.object(prepare_publish, "DESTINATION", output), \
                 patch.object(prepare_publish, "source_files", return_value=[Path("converter.py")]), \
                 patch.object(prepare_publish.subprocess, "run"), \
                 patch("sys.argv", ["prepare_publish"]), redirect_stdout(io.StringIO()):
                self.assertEqual(prepare_publish.main(), 0)
                self.assertEqual([p.name for p in output.iterdir()], ["converter.py"])
                self.assertEqual(source.read_bytes(), (output / source.name).read_bytes())
                (output / "empty-cache").mkdir()
                with redirect_stderr(io.StringIO()):
                    self.assertFalse(prepare_publish.verify([Path("converter.py")]))

    def test_failed_copy_keeps_previous_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "publish"
            output.mkdir()
            (output / "converter.py").write_text("previous working version")
            with patch.object(prepare_publish, "ROOT", root), \
                 patch.object(prepare_publish, "DESTINATION", output), \
                 patch.object(prepare_publish, "source_files", return_value=[Path("missing.py")]), \
                 patch.object(prepare_publish.subprocess, "run"), \
                 patch("sys.argv", ["prepare_publish"]), redirect_stderr(io.StringIO()):
                self.assertEqual(prepare_publish.main(), 1)
                self.assertEqual((output / "converter.py").read_text(), "previous working version")

    def test_check_never_builds_or_changes_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "converter.py"
            source.write_text("current")
            output = root / "publish"
            output.mkdir()
            (output / source.name).write_text("previous")
            with patch.object(prepare_publish, "ROOT", root), \
                 patch.object(prepare_publish, "DESTINATION", output), \
                 patch.object(prepare_publish, "source_files", return_value=[Path(source.name)]), \
                 patch.object(prepare_publish.subprocess, "run") as build, \
                 patch("sys.argv", ["prepare_publish", "--check"]), redirect_stderr(io.StringIO()):
                self.assertEqual(prepare_publish.main(), 1)
                build.assert_not_called()
                self.assertEqual((output / source.name).read_text(), "previous")
