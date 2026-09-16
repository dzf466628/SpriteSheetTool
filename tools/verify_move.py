"""Prove that blocks moved out of the backup are AST-identical (pure move).

Usage: python tools/verify_move.py <backup.py> <new_module.py> <name> [<name> ...]
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "block_tool.py"


def main() -> int:
    backup, newfile = sys.argv[1], sys.argv[2]
    names = sys.argv[3:]
    ok = True
    for name in names:
        r = subprocess.run(
            [sys.executable, str(TOOL), "verify", backup, name, newfile, name],
            capture_output=True, text=True, encoding="utf-8",
        )
        status = "OK  " if r.returncode == 0 else "FAIL"
        if r.returncode != 0:
            ok = False
        detail = (r.stdout or r.stderr).strip().replace("\n", " ")
        print(f"  [{status}] {name:<24} {detail}")
    print("PURE MOVE VERIFIED" if ok else "MISMATCH FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
