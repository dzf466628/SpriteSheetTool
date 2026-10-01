# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""打印 .ico 内嵌的尺寸表，并检查是否都是标准方形尺寸。"""
import struct
import sys
from pathlib import Path

CANONICAL = {16, 24, 32, 48, 64, 128, 256}


def sizes(path: Path):
    data = path.read_bytes()
    if data[:4] != b"\x00\x00\x01\x00":
        raise SystemExit(f"{path} 不是 ICO")
    count = struct.unpack_from("<H", data, 4)[0]
    out = []
    for i in range(count):
        off = 6 + i * 16
        w = data[off] or 256
        h = data[off + 1] or 256
        bits = struct.unpack_from("<H", data, off + 6)[0]
        out.append((w, h, bits))
    return out


def main() -> int:
    for p in sys.argv[1:]:
        path = Path(p)
        rows = sizes(path)
        square = all(w == h for w, h, _ in rows)
        canonical = all(w == h and w in CANONICAL for w, h, _ in rows)
        print(f"  {path.name}")
        print(f"    张数 {len(rows)}: " + ", ".join(f"{w}x{h}" for w, h, _ in rows))
        print(f"    全部方形: {square}    全部标准尺寸: {canonical}")
        if not canonical and square:
            odd = sorted({w for w, h, _ in rows if w not in CANONICAL})
            print(f"    非标准尺寸: {odd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
