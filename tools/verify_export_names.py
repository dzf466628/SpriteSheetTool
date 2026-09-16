"""导出同名处理回归。

两件事要证明：
  1) `ExportDialog._planned_names()` 预告的路径与 `_export_to_directory()` 实际写入的
     文件**完全一致**（两处命名逻辑一旦脱节，同名提示就会漏报或误报）；
  2) 覆盖 / 加序号 / 取消 三种选择的行为正确（加序号时不覆盖已有文件）。

Usage: .venv\\Scripts\\python.exe tools\\verify_export_names.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet.ui.export_dialog import ExportDialog
from spritesheet.ui.exporters import FMT_NATIVE, FMT_TP_HASH
from spritesheet.ui.models import Action

FAIL: list[str] = []
CHOICE = {"index": 1}          # 0=覆盖 1=加序号 2=取消


def check(name, ok, detail=""):
    print(f"  [{'OK' if ok else 'FAIL'}]   {name} {detail}")
    if not ok:
        FAIL.append(name)


def patch_policy():
    """替换弹窗选择（测试不必 mock Qt 模态框）。"""
    def fake(self, existing):
        return ("overwrite", "rename", "cancel")[CHOICE["index"]]
    ExportDialog._ask_name_policy = fake


def make_sheet(elements=3, size=40):
    pm = QPixmap(elements * size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    rects = []
    for i in range(elements):
        p.fillRect(QRect(i * size + 2, 2, size - 4, size - 4), QColor("#C62828"))
        rects.append((i * size + 2, 2, size - 4, size - 4))
    p.end()
    return pm, rects


def build_dialog(separate: bool, apply_overrides: bool = True):
    from spritesheet.ui.main_window import MainWindow
    win = MainWindow()
    pm, rects = make_sheet()
    viewer = win.image_viewer
    viewer._detect_generation += 1
    viewer._pixmap = pm
    viewer._source_images = [pm]
    viewer._frames = rects
    viewer._frame_logical = {}
    win.actions.clear()
    win.action_list.clear()
    for i, nm in enumerate(("跑步", "跳跃")):
        act = Action(nm, "#E3351C")
        win.actions.append(act)
        win._append_action_card(act)
        act.selected_indices = [i]
        viewer.crop_frames_for_action(act)
    dlg = ExportDialog(win.actions, viewer._pixmap, project_provider=win._project_manifest)
    if apply_overrides:
        dlg.cb_gif.setChecked(True)
        dlg.cb_project.setChecked(True)
        dlg.cb_separate.setChecked(separate)
        dlg._json_fmt_checks[FMT_NATIVE].setChecked(True)
        dlg._json_fmt_checks[FMT_TP_HASH].setChecked(True)
    return win, dlg


def run_case(label: str, separate: bool) -> None:
    print(f"\n=== {label} ===")
    tmp = Path(tempfile.mkdtemp(prefix="sstool_export_"))
    win, dlg = build_dialog(separate)

    # 第一次导出（目录为空，不应该有冲突）
    planned0 = dlg._planned_names(tmp)
    check("空目录无冲突", dlg.resolve_name_conflicts(str(tmp)) == "")
    written0 = dlg._export_to_directory(str(tmp))
    made0 = set(written0) | {p for p in planned0 if p.is_dir()}
    missing = [p for p in planned0 if not p.exists()]
    check("预告路径与实际写入一致", not missing, f"缺失 {[p.name for p in missing]}")
    print(f"    首次产出 {len(written0)} 个文件：{[p.name for p in written0][:6]}")

    # 记录已有内容的指纹，用于确认「加序号」不会覆盖
    before = {p: p.stat().st_mtime_ns for p in tmp.rglob("*") if p.is_file()}

    # 选择「加序号另存」
    CHOICE["index"] = 1
    suffix = dlg.resolve_name_conflicts(str(tmp))
    check("有同名时返回序号", suffix == "_2", f"得到 {suffix!r}")
    planned2 = dlg._planned_names(tmp, suffix)
    written2 = dlg._export_to_directory(str(tmp), suffix=suffix)
    missing2 = [p for p in planned2 if not p.exists()]
    check("序号路径的预告与实际一致", not missing2, f"缺失 {[p.name for p in missing2]}")
    after = {p: p.stat().st_mtime_ns for p in tmp.rglob("*") if p.is_file()}
    overwritten = [p.name for p, t in before.items() if after.get(p) != t]
    check("加序号未覆盖原文件", not overwritten, f"被改动的 {overwritten}")

    # 选择「覆盖」
    CHOICE["index"] = 0
    check("选择覆盖返回空后缀", dlg.resolve_name_conflicts(str(tmp)) == "")

    # 选择「取消」
    CHOICE["index"] = 2
    check("选择取消返回 None", dlg.resolve_name_conflicts(str(tmp)) is None)

    win.close()


def check_defaults() -> None:
    """导出选项的默认值与顺序：默认导出 SSTOOL 工程，且排在 JSON 之前。"""
    print("\n=== 导出选项默认值与顺序 ===")
    win, dlg = build_dialog(separate=False, apply_overrides=False)
    check("默认勾选「导出 SSTOOL 工程」", dlg.cb_project.isChecked())
    check("默认勾选「导出 JSON」", dlg.cb_json.isChecked())
    check("默认不勾选 GIF / 分别导出",
          not dlg.cb_gif.isChecked() and not dlg.cb_separate.isChecked())

    # 读出「附加选项」里的复选框顺序（布局顺序 = 用户看到的顺序）
    order = []
    layout = dlg.cb_project.parentWidget().layout()
    for i in range(layout.count()):
        widget = layout.itemAt(i).widget()
        if widget in (dlg.cb_project, dlg.cb_json, dlg.cb_gif, dlg.cb_separate):
            order.append(widget.text())
    print(f"    附加选项顺序: {order}")
    check("SSTOOL 工程排在 JSON 之前",
          order.index(dlg.cb_project.text()) < order.index(dlg.cb_json.text()),
          f"实际 {order}")

    # 默认导出就应该产出 project.sstool
    tmp = Path(tempfile.mkdtemp(prefix="sstool_export_default_"))
    planned = [p.name for p in dlg._planned_names(tmp)]
    check("默认就会写入 project.sstool", "project.sstool" in planned, f"实际 {planned}")
    win.close()


def main() -> int:
    app = QApplication(sys.argv)
    patch_policy()
    check_defaults()
    run_case("整体导出（不分别导出）", separate=False)
    run_case("分别导出（每个动作一个文件夹）", separate=True)
    print()
    if FAIL:
        print("导出同名处理回归失败：", *FAIL, sep="\n  - ")
        return 1
    print("导出同名处理回归全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
