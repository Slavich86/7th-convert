from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from seventh_convert.player_binary import compatible_player


class PlayerBinaryTests(unittest.TestCase):
    def test_linux_copy_is_rejected_on_macos_and_accepted_on_linux(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'seventh-player'
            header = bytearray(64)
            header[:6] = b'\x7fELF\x02\x01'
            header[18:20] = (62).to_bytes(2, 'little')
            path.write_bytes(header)
            with patch('seventh_convert.player_binary.platform.machine', return_value='x86_64'):
                with patch('seventh_convert.player_binary.sys.platform', 'darwin'):
                    self.assertFalse(compatible_player(path))
                with patch('seventh_convert.player_binary.sys.platform', 'linux'):
                    self.assertTrue(compatible_player(path))

    def test_macos_rejects_another_cpu_and_accepts_native_or_universal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'seventh-player'
            path.write_bytes(b'\xcf\xfa\xed\xfe' + (0x0100000C).to_bytes(4, 'little') + bytes(56))
            with patch('seventh_convert.player_binary.sys.platform', 'darwin'):
                with patch('seventh_convert.player_binary.platform.machine', return_value='arm64'):
                    self.assertTrue(compatible_player(path))
                with patch('seventh_convert.player_binary.platform.machine', return_value='x86_64'):
                    self.assertFalse(compatible_player(path))
                    path.write_bytes(b'\xca\xfe\xba\xbe' + (2).to_bytes(4, 'big') +
                                     (0x0100000C).to_bytes(4, 'big') + bytes(16) +
                                     (0x01000007).to_bytes(4, 'big') + bytes(16))
                    self.assertTrue(compatible_player(path))
