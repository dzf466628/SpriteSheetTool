"""撤销路由回归：动作栈优先、撤空了落到全局栈。

为什么单独一个脚本：抠除背景 / 添加原图这类工程级操作记在**全局栈**，而 Ctrl+Z 走的是
``_active_stack()``（选中动作时指向动作自己的栈）。以前动作栈撤不动时这里什么都不做，
症状是"按了 Ctrl+Z 完全没反应"——用户已经踩到过。这个脚本把新的路由约定钉死：

  1. 没有动作时：Ctrl+Z 撤全局；
  2. 有动作、动作栈为空时：Ctrl+Z **也要**能撤全局（本次修的）；
  3. 动作栈里有记录时：先撤动作内的，**全局一个都不许动**；
  4. 动作栈撤空后再按：才轮到全局；
  5. 重做同规（动作栈优先，撤空了落到全局重做）。

全程 offscreen 可跑；QApplication 必须挂模块级（放函数里真机收尾会 0xC0000409）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from PyQt6.QtGui import QUndoCommand
from PyQt6.QtWidgets import QApplication

from spritesheet.ui.key_panel import pil_to_pixmap
from spritesheet.ui.main_window import MainWindow

_APP = QApplication.instance() or QApplication(sys.argv)


class _Dummy(QUndoCommand):
    """只记"谁被撤了"的假命令：用来分辨到底撤的是哪个栈。"""

    def __init__(self, log: list, tag: str) -> None:
        super().__init__(tag)
        self._log = log

    def undo(self) -> None:
        self._log.append(f"undo {self.text()}")

    def redo(self) -> None:
        self._log.append(f"redo {self.text()}")


def main() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  PASS  {name} {detail}")
        else:
            failed += 1
            print(f"  FAIL  {name} {detail}")

    def make_image(color) -> Image.Image:
        return Image.new("RGBA", (80, 60), color + (255,))

    win = MainWindow()
    win.resize(1200, 800)
    win.show()
    for _ in range(6):
        _APP.processEvents()

    def wait(ms: int = 250) -> None:
        for _ in range(max(1, ms // 50)):
            _APP.processEvents()
            _APP.thread().msleep(50)

    print("---- 准备：1 张原图 + 1 个动作，再追加 1 张原图（全局记录）----")
    win._on_image_loaded(pil_to_pixmap(make_image((40, 120, 200))))
    wait(200)
    first_baked = win.image_viewer._pixmap
    win._on_add_action_clicked()                 # 全局记录 1：新建动作
    wait(200)
    check("动作建出来了", len(win.actions) == 1, f"动作数={len(win.actions)}")
    action = win.actions[0]
    check("当前动作就是这个新动作", win.image_viewer.current_action is action)
    check("它的撤销栈是空的（刚建出来）", action.undo_stack.count() == 0)
    win._on_image_loaded(pil_to_pixmap(make_image((200, 40, 200))))   # 全局记录 2：加图
    wait(200)
    check("第二张原图加进来了", len(win.image_viewer._source_images) == 2)
    check("全局栈有 2 条记录", win._undo_stack.index() == 2, f"index={win._undo_stack.index()}")

    print("---- 1. 动作栈为空时，Ctrl+Z 要能撤到全局 ----")
    log: list = []
    win._do_undo()
    wait(300)
    check("加进去的第二张原图被撤掉了", len(win.image_viewer._source_images) == 1,
          f"源图数={len(win.image_viewer._source_images)}")
    check("原图回到第一张", win.image_viewer._pixmap is first_baked)
    check("全局栈退到 1", win._undo_stack.index() == 1, f"index={win._undo_stack.index()}")
    check("动作还在（撤的是加图，不是建动作）", len(win.actions) == 1)

    print("---- 2. 动作栈里有记录时：先撤动作内，全局一个都不许动 ----")
    action.undo_stack.push(_Dummy(log, "动作内操作"))
    check("动作栈里有 1 条", action.undo_stack.index() == 1)
    # push() 自己会调一次 redo()（Qt 就是这么设计的），清掉它才能只看这次 Ctrl+Z
    check("push 时 Qt 会先调一次 redo", log == ["redo 动作内操作"], f"log={log}")
    log.clear()
    global_before = win._undo_stack.index()
    win._do_undo()
    wait(200)
    check("撤的是动作内的那条", log == ["undo 动作内操作"], f"log={log}")
    check("全局栈没被动过", win._undo_stack.index() == global_before,
          f"index={win._undo_stack.index()}")

    print("---- 3. 动作栈撤空之后，再按才轮到全局 ----")
    win._do_undo()
    wait(200)
    check("动作栈已空", action.undo_stack.index() == 0)
    check("这一下撤的是全局（新建动作被撤回）", win._undo_stack.index() == global_before - 1,
          f"index={win._undo_stack.index()}")
    check("动作没了", len(win.actions) == 0, f"动作数={len(win.actions)}")

    print("---- 4. 重做同规 ----")
    log.clear()
    win._do_redo()
    wait(200)
    check("Ctrl+Y 重做全局", win._undo_stack.index() == global_before,
          f"index={win._undo_stack.index()}")
    check("动作又回来了", len(win.actions) == 1, f"动作数={len(win.actions)}")
    check("重做的是全局（不是动作栈）", log == [], f"log={log}")

    print("---- 5. 没有动作时：全局照旧能撤 ----")
    win.image_viewer.set_current_action(None)
    before = win._undo_stack.index()
    win._do_undo()
    wait(200)
    check("没有当前动作时撤的仍是全局", win._undo_stack.index() == before - 1,
          f"index={win._undo_stack.index()}")

    print("---- 6. 撤到底不能再撤时不能报错 ----")
    for _ in range(5):
        win._do_undo()
        win._do_redo()
    check("反复撤到底/重做到底不抛异常", True)

    win.close()
    print(f"\nUNDO ROUTE VERIFY {'OK' if not failed else 'FAILED'}  {passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())