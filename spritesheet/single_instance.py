# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""单实例：整个系统只允许跑一个本程序，第二次启动把"要打开的文件"转给已在跑的那个。

需求：双击多个 `.sstool`（或用文件关联打开）时，不要开出一堆进程，而是**在同一个窗口里
多开一个标签页** —— 和 PS 一样。

做法：`QLocalServer` / `QLocalSocket`（Windows 上是命名管道，不需要管理员权限）

  1. 启动时先试着**连**一下固定名字的本地服务；
  2. 连上了 -> 说明已经有一个实例在跑，把参数（要打开的文件路径，可能为空）写过去，
     然后本进程直接退出，**不建窗口**；
  3. 连不上 -> 本进程就是"主实例"，`listen()` 起来等着收消息；收到消息就发信号，
     由主窗口开新标签页。

退出时要把服务名从系统里摘掉（`removeServer`），否则崩溃/强杀之后会留下一个连不上
的"僵尸"服务名，下次启动一直连不上、也 listen 不了 —— 所以 listen 之前**无条件**
先 removeServer 一次，这是这类实现最常见的坑。
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

# 服务名只在同一台机器上唯一，用固定的名字（不带版本号）：
# 装了新版本以后，从旧版本里双击文件也能落到新实例上，不会同时跑两份。
DEFAULT_KEY = "SpriteSheetTool.single-instance"

CONNECT_TIMEOUT_MS = 700
ACK_TIMEOUT_MS = 700
WRITE_TIMEOUT_MS = 700

_TERMINATOR = b"\n"


class SingleInstance(QObject):
    """本地单实例守卫。

    信号：
      ``opened(str)`` —— 另一个实例请求打开某个文件（空串表示"只把窗口拿到前面"）。
    """

    opened = pyqtSignal(str)

    def __init__(self, key: str = DEFAULT_KEY, parent=None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None
        # 每个连接的收数据缓冲，键用 id(socket)：客户端很小，一次就是一条。
        # 但不能假设"写完了就一定收到 readyRead"（见 _on_new_connection）。
        self._buffers: dict[int, bytearray] = {}
        # 上一次 notify_existing 有没有等到 ACK（只做诊断，不参与判定）
        self.last_delivery_acked = False

    # ---- 从实例：把请求转给主实例 ----
    def notify_existing(self, payload: str = "") -> bool:
        """已经有实例在跑就把 payload 转过去并返回 True；否则返回 False。

        协议是**一行文本 + 换行**：主实例收到换行才算收全，然后回一个 ACK。
        为什么要 ACK：实测 `write()` 之后数据其实还在 QLocalSocket 的内部缓冲里，
        要等事件循环转起来才会真正写进管道；立刻 `disconnectFromServer()` 会把数据丢掉
        （第一次就是这么写的，主实例从头到尾没收到过路径）。
        这里用 `waitForReadyRead` 边等 ACK 边让 socket 把数据发出去。

        返回值语义：**连上了就算"已有实例"**，即使 ACK 没等到也返回 True —— 宁可这次
        转发丢了文件，也绝不能因为误判而开出第二个窗口（那是明确要避免的）。
        """
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write((payload + "\n").encode("utf-8"))
        socket.flush()
        acked = socket.waitForReadyRead(ACK_TIMEOUT_MS)
        try:
            socket.readAll()          # 读掉 ACK
        except Exception:             # noqa: BLE001 - 已经断开就无所谓了
            pass
        socket.disconnectFromServer()
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            socket.waitForDisconnected(WRITE_TIMEOUT_MS)
        self.last_delivery_acked = bool(acked)
        return True

    # ---- 主实例：收请求 ----
    def listen(self) -> bool:
        """成为主实例。返回是否成功监听。"""
        # 先清掉可能残留的服务名（上次被强杀/崩溃会留下），否则 listen 会失败
        QLocalServer.removeServer(self._key)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self._key):
            return False
        self._server = server
        return True

    def _on_new_connection(self) -> None:
        """有新连接就挂上读取回调。

        关键：**不能只等 readyRead**。客户端（第二个进程）是"连上 -> 写一条 -> 立刻断开"，
        等主实例处理到 newConnection 时数据往往已经在缓冲区里、readyRead 也已经发过了，
        只挂回调就会永远等不到 —— 实测就是这个原因导致主实例从没收到过路径。
        所以挂完回调要**马上自己读一次**，并且把"断开"当成消息结束的信号。
        """
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[id(socket)] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._drain(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))
            self._drain(socket)

    def _drain(self, socket: QLocalSocket) -> None:
        buf = self._buffers.get(id(socket))
        if buf is None:
            return
        buf.extend(bytes(socket.readAll().data()))
        if _TERMINATOR in buf:
            self._deliver(socket)

    def _deliver(self, socket: QLocalSocket) -> None:
        """收全一行 -> 回 ACK -> 发信号。"""
        key = id(socket)
        buf = self._buffers.pop(key, None)
        if buf is None:
            return
        payload = bytes(buf).split(_TERMINATOR, 1)[0].decode("utf-8", errors="replace")
        try:
            socket.write(b"1\n")
            socket.flush()
        except Exception:  # noqa: BLE001 - 对端可能已经走了
            pass
        socket.disconnectFromServer()
        socket.deleteLater()
        self.opened.emit(payload.strip())

    def _on_disconnected(self, socket: QLocalSocket) -> None:
        """客户端在没发换行的情况下断开：把已经收到的当成完整消息（兜底）。"""
        key = id(socket)
        buf = self._buffers.pop(key, None)
        if buf is None:            # 已经走过 _deliver
            return
        payload = bytes(buf).decode("utf-8", errors="replace")
        socket.deleteLater()
        self.opened.emit(payload.strip())

    def close(self) -> None:
        """退出时摘掉服务名，避免留下连不上的僵尸服务。"""
        if self._server is not None:
            self._server.close()
            self._server = None
        self._buffers.clear()
        QLocalServer.removeServer(self._key)
