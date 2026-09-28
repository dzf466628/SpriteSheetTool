# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""MCP 设置弹窗：编辑 → MCP 设置…。

内容：
  - 「启用 MCP 服务器」开关（默认开，改动即时写 QSettings，无需确定/应用）；
  - 启动命令（只读显示 + 一键复制）：开发环境为 `python -m spritesheet.mcp_server`，
    打包版为 `SpriteSheetTool.exe --mcp`（启动路径自动区分）；
  - 已注册工具列表（名称 + 描述，来自 mcp_server.TOOL_DEFS —— 单一注册源，
    永远和实际注册一致）；
  - 「复制 Claude / Cursor 配置」：把 mcpServers 配置 JSON 复制到剪贴板；
  - 「测试连接」：起一个 stdio 子进程做 MCP initialize 握手，成功显示协议版本
    与工具清单，失败显示原因（可行动的报错）。

深色主题由全局 APP_QSS + main.py 的 install_dark_titlebars(app) 自动覆盖，
本对话框不需要额外处理标题栏。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from spritesheet.mcp_server import TOOL_DEFS
from spritesheet.settings import mcp_enabled, set_mcp_enabled


def mcp_launch_command() -> tuple[str, list[str]]:
    """返回 MCP 启动方式 (命令, 参数列表)，与当前运行形态一致。

    打包版（PyInstaller）→ 本 exe + --mcp；
    开发环境 → 当前 python + -m spritesheet.mcp_server。
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        return str(exe), ["--mcp"]
    python = Path(sys.executable)
    return str(python), ["-m", "spritesheet.mcp_server"]


def mcp_client_config_json() -> str:
    """生成可直接粘贴进 Claude Desktop / Cursor mcpServers 的配置 JSON。"""
    command, args = mcp_launch_command()
    config = {
        "mcpServers": {
            "spritesheet": {
                "command": command,
                "args": args,
            }
        }
    }
    return json.dumps(config, ensure_ascii=False, indent=2)


async def _probe_mcp(command: str, args: list[str]) -> tuple[bool, str]:
    """子进程 stdio 握手：initialize + 列出工具。返回 (是否成功, 信息)。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command, args=args)
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                info = await session.initialize()
                result = await session.list_tools()
                names = sorted(t.name for t in result.tools)
        version = getattr(info, "protocolVersion", "?")
        return True, (
            f"握手成功（协议 {version}），已注册 {len(names)} 个工具：\n"
            + "、".join(names)
        )
    except Exception as exc:  # noqa: BLE001 - 握手失败要把原因给用户看
        return False, f"连接失败：{exc}"


class _ProbeThread(QThread):
    """后台跑握手的线程：不卡界面，结果经信号回主线程。"""

    result = pyqtSignal(bool, str)

    def __init__(self, command: str, args: list[str]) -> None:
        super().__init__()
        self._command = command
        self._args = args

    def run(self) -> None:
        ok, message = asyncio.run(_probe_mcp(self._command, self._args))
        self.result.emit(ok, message)


class McpSettingsDialog(QDialog):
    """MCP 设置：开关即时保存；测试连接在主线程外跑。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MCP 设置")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._probe_thread: _ProbeThread | None = None

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ---- 启用开关 ----
        self.cb_enabled = QCheckBox("启用 MCP 服务器（让 Claude / Cursor 等 AI 工具读写精灵图工程）")
        self.cb_enabled.setChecked(mcp_enabled())
        self.cb_enabled.toggled.connect(set_mcp_enabled)
        root.addWidget(self.cb_enabled)

        # ---- 启动命令 ----
        command, args = mcp_launch_command()
        command_text = " ".join([f'"{command}"'] + args)
        cmd_box = QGroupBox("启动命令（MCP 客户端按这个启动本服务器）")
        cmd_layout = QGridLayout(cmd_box)
        cmd_layout.setColumnStretch(0, 1)
        self.cmd_label = QLabel(command_text)
        self.cmd_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cmd_layout.addWidget(self.cmd_label, 0, 0)
        btn_copy_cmd = QPushButton("复制")
        btn_copy_cmd.setFixedWidth(64)
        btn_copy_cmd.clicked.connect(lambda: self._copy(command_text))
        cmd_layout.addWidget(btn_copy_cmd, 0, 1)
        hint = QLabel("开发环境显示 python -m 启动；打包版显示 本程序.exe --mcp。")
        hint.setStyleSheet("color: #999;")
        cmd_layout.addWidget(hint, 1, 0, 1, 2)
        root.addWidget(cmd_box)

        # ---- 已注册工具 ----
        tools_box = QGroupBox(f"已注册工具（{len(TOOL_DEFS)} 个）")
        tools_layout = QVBoxLayout(tools_box)
        tools_view = QPlainTextEdit()
        tools_view.setReadOnly(True)
        tools_view.setMaximumHeight(150)
        tools_view.setPlainText(
            "\n".join(f"• {name} —— {desc}" for name, desc in TOOL_DEFS)
        )
        tools_layout.addWidget(tools_view)
        root.addWidget(tools_box)

        # ---- 复制配置 ----
        config_box = QGroupBox("复制客户端配置")
        config_layout = QVBoxLayout(config_box)
        config_hint = QLabel(
            "把下面的 JSON 粘贴到 Claude Desktop 的 claude_desktop_config.json 的 "
            "mcpServers 里，或 Cursor 的 .cursor/mcp.json 里（结构相同）。"
        )
        config_hint.setWordWrap(True)
        config_hint.setStyleSheet("color: #999;")
        config_layout.addWidget(config_hint)
        self.config_text = QPlainTextEdit()
        self.config_text.setPlainText(mcp_client_config_json())
        self.config_text.setMaximumHeight(120)
        config_layout.addWidget(self.config_text)
        btn_copy_cfg = QPushButton("复制配置")
        btn_copy_cfg.setFixedWidth(120)
        btn_copy_cfg.clicked.connect(
            lambda: self._copy(self.config_text.toPlainText()))
        config_layout.addWidget(btn_copy_cfg, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(config_box)

        # ---- 测试连接 + 关闭 ----
        bottom = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setFixedWidth(120)
        self.btn_test.clicked.connect(self._test_connection)
        bottom.addWidget(self.btn_test)
        self.probe_state = QLabel("")
        self.probe_state.setWordWrap(True)
        self.probe_state.setStyleSheet("color: #999;")
        bottom.addWidget(self.probe_state, 1)
        btn_close = QPushButton("关闭")
        btn_close.setFixedWidth(96)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    def _copy(self, text: str) -> None:
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
        self.probe_state.setText("已复制到剪贴板。")
        self.probe_state.setStyleSheet("color: #4caf50;")

    def _test_connection(self) -> None:
        if self._probe_thread is not None and self._probe_thread.isRunning():
            return
        command, args = mcp_launch_command()
        self.btn_test.setEnabled(False)
        self.btn_test.setText("连接中…")
        self.probe_state.setStyleSheet("color: #999;")
        self.probe_state.setText("正在握手 initialize（首次启动可能要多等几秒）…")
        thread = _ProbeThread(command, args)
        thread.result.connect(self._on_probe_done)
        thread.finished.connect(thread.deleteLater)
        self._probe_thread = thread
        thread.start()

    def _on_probe_done(self, ok: bool, message: str) -> None:
        self.btn_test.setEnabled(True)
        self.btn_test.setText("测试连接")
        if ok:
            self.probe_state.setStyleSheet("color: #4caf50;")
        else:
            self.probe_state.setStyleSheet("color: #e91e63;")
        self.probe_state.setText(message)