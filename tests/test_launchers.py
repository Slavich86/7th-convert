from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Bash required')
class LauncherTests(unittest.TestCase):
    def run_launcher(self, *, mac=False, build_status=0):
        with tempfile.TemporaryDirectory(prefix='converter with spaces ') as directory:
            root = Path(directory)
            app = root / 'Published converter'
            app.mkdir()
            for name in ('7th-vfx-convertor.sh', '7th-vfx-convertor.command'):
                shutil.copy2(ROOT / name, app)
            (app / 'requirements.txt').write_text('')
            interpreter = app / '.venv/bin/python'
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" >> "$LAUNCH_LOG"\n'
                                   'if [ "$2" = seventh_convert.build_player ]; then exit "$BUILD_STATUS"; fi\nexit 0\n')
            interpreter.chmod(0o755)
            tools = root / 'tools'; tools.mkdir()
            (tools / 'dirname').symlink_to(shutil.which('dirname'))
            for name, body in {
                'uname': 'if [ "$1" = -s ]; then echo Darwin; else echo arm64; fi' if mac else 'echo Linux',
                'ffmpeg': 'exit 0', 'ffprobe': 'exit 0',
                'python3': 'echo "Wrong Python selected" >&2; exit 99',
            }.items():
                path = tools / name
                path.write_text('#!/bin/sh\n' + body + '\n'); path.chmod(0o755)
            log = root / 'calls.txt'
            environment = {**os.environ, 'HOME': str(root), 'PATH': str(tools),
                           'LAUNCH_LOG': str(log), 'BUILD_STATUS': str(build_status)}
            entry = '7th-vfx-convertor.command' if mac else '7th-vfx-convertor.sh'
            result = subprocess.run(['/bin/bash', str(app / entry), 'a file with spaces.mov'],
                                    cwd=root, env=environment, input='', text=True, capture_output=True)
            return result, log.read_text(), str(app)

    def test_relocated_folder_uses_venv_without_uv_and_keeps_arguments(self):
        result, calls, app = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(app, calls)
        self.assertIn('seventh_convert.ui\na file with spaces.mov\n', calls)
        self.assertNotIn('Wrong Python', result.stderr)

    def test_finder_entry_uses_sibling_launcher_from_another_directory(self):
        result, calls, app = self.run_launcher(mac=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(app, calls)
        self.assertIn('seventh_convert.ui\na file with spaces.mov\n', calls)

    def test_failed_player_build_stops_before_ui(self):
        result, calls, _ = self.run_launcher(build_status=23)
        self.assertEqual(result.returncode, 23)
        self.assertNotIn('seventh_convert.ui', calls)
