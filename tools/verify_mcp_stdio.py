"""Smoke-check the MCP stdio initialize/tools-list handshake."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spritesheet.mcp_server import TOOL_DEFS


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "spritesheet.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "verify", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    assert proc.stdin is not None and proc.stdout is not None
    for message in messages:
        proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    tool_names = []
    try:
        for _ in range(8):
            line = proc.stdout.readline()
            if not line:
                break
            payload = json.loads(line)
            if payload.get("id") == 2:
                tool_names = [item.get("name") for item in payload.get("result", {}).get("tools", [])]
                break
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    expected = [name for name, _description in TOOL_DEFS]
    if tool_names != expected:
        raise SystemExit(f"MCP tools mismatch: got {len(tool_names)}, expected {len(expected)}")
    print(f"MCP STDIO OK ({len(tool_names)} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
