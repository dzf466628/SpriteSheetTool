# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Assemble a new module from a hand-written header plus extracted blocks.

Usage: python tools/assemble.py <header.py> <body.py> <output.py>
Both inputs are read as UTF-8 and the result is written as UTF-8 (no BOM).
"""
import sys
from pathlib import Path


def main() -> int:
    header_path, body_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    header = Path(header_path).read_text(encoding="utf-8")
    body = Path(body_path).read_text(encoding="utf-8")
    text = header + body
    Path(out_path).write_text(text, encoding="utf-8")
    print(f"{out_path}: {len(text.splitlines())} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
