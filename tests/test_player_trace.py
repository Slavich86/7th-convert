from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from seventh_convert.player_trace import PlayerTrace


class PlayerTraceTests(unittest.TestCase):
    def test_async_records_are_drained_on_close(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SEVENTH_PLAYER_TRACE": "1"}):
            trace = PlayerTrace(Path(directory))
            for i in range(100):
                trace.record("frame", pts_us=i * 40_000)
            trace.close()
            records = [json.loads(line) for line in trace.path.read_text().splitlines()]
            self.assertEqual(len([r for r in records if r["event"] == "frame"]), 100)
            self.assertEqual(records[-1]["event"], "trace_close")
            self.assertEqual(records[-1]["lost_records"], 0)
            self.assertFalse(trace._worker.is_alive())

    def test_disabled_trace_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SEVENTH_PLAYER_TRACE": "0"}):
            root = Path(directory) / "disabled"
            trace = PlayerTrace(root)
            trace.record("frame")
            trace.close()
            self.assertFalse(root.exists())

    def test_default_trace_does_not_create_files_or_writer(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory) / "logs"
            trace = PlayerTrace(root)
            trace.record("frame")
            trace.close()
            self.assertFalse(trace.enabled)
            self.assertIsNone(trace._worker)
            self.assertFalse(root.exists())

    def test_unwritable_destination_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"SEVENTH_PLAYER_TRACE": "1"}):
            root = Path(directory) / "file"
            root.write_text("occupied")
            trace = PlayerTrace(root)
            self.assertFalse(trace.enabled)
            self.assertTrue(trace.error)
            trace.close()
