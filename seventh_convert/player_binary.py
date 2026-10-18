"""Check native executable headers before reusing a copied player build."""
from __future__ import annotations

from pathlib import Path
import platform
import sys


def compatible_player(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(64)
    except OSError:
        return False
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if sys.platform == "win32":
        return header[:2] == b"MZ"
    if sys.platform.startswith("linux"):
        if len(header) < 20 or header[:4] != b"\x7fELF" or header[5] not in (1, 2):
            return False
        cpu = int.from_bytes(header[18:20], "little" if header[5] == 1 else "big")
        return cpu == (183 if arm else 62) if machine in {"arm64", "aarch64", "x86_64", "amd64"} else False
    if sys.platform == "darwin":
        expected_cpu = 0x0100000C if arm else 0x01000007
        if header[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
            order = "little" if header[0] == 0xCF else "big"
            return int.from_bytes(header[4:8], order) == expected_cpu
        if header[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
            # Universal Mach-O: inspect the architecture table, not the payload.
            count = int.from_bytes(header[4:8], "big")
            if not 1 <= count <= 32:
                return False
            stride = 32 if header[3] == 0xBF else 20
            with path.open("rb") as handle:
                handle.seek(8)
                table = handle.read(count * stride)
            return any(int.from_bytes(table[i:i + 4], "big") == expected_cpu
                       for i in range(0, len(table), stride))
    return False
