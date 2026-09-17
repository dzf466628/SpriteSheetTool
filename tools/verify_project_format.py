"""工程文件格式回归：新布局（PNG 前置）、旧布局兼容、损坏文件处理。

新布局让 .sstool 本身是一张合法 PNG，资源管理器（配好图片缩略图处理器后）
就能直接显示内容缩略图；容器数据接在 PNG 的 IEND 之后。

Usage: .venv\\Scripts\\python.exe tools\\verify_project_format.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spritesheet import project_file as PF

FAIL = []


def check(name, ok, detail=""):
    print(f"  [{'OK' if ok else 'FAIL'}]   {name} {detail}")
    if not ok:
        FAIL.append(name)


def make_png(w=64, h=48, color="#E91E63"):
    pm = QPixmap(w, h)
    pm.fill(QColor(color))
    buf = PF.QByteArray()
    b = PF.QBuffer(buf)
    b.open(PF.QIODevice.OpenModeFlag.WriteOnly)
    pm.save(b, "PNG")
    b.close()
    return bytes(buf)


def build_v1_bytes(manifest, sections):
    """手工拼一个旧布局（容器在最前）的工程文件。"""
    mb = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    out = bytearray()
    out += PF._HEADER.pack(PF.MAGIC, PF.VERSION, len(mb), len(sections))
    out += mb
    for name, payload in sections.items():
        nb = name.encode("utf-8")
        out += PF._SECTION.pack(len(nb), len(payload))
        out += nb
        out += payload
    return bytes(out)


def main() -> int:
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="sstool_fmt_"))
    thumb = make_png()
    frame = make_png(32, 32, "#1565C0")
    manifest = {"format": "spritesheet-tool-project", "version": 1, "thumbnail": "thumbnail.png"}
    sections = {"thumbnail.png": thumb, "actions/0000/frame_0000.png": frame}

    print("=== 1. 新布局：文件以合法 PNG 开头 ===")
    p2 = tmp / "v2.sstool"
    PF.write_project(p2, manifest, sections)
    data = p2.read_bytes()
    check("文件以 PNG 魔数开头", data.startswith(PF.PNG_SIGNATURE))
    end = PF.png_image_end(data)
    check("能定位 PNG 结束位置(IEND)", end is not None, f"offset={end}")
    check("PNG 结束处紧跟容器魔数", data[end:end + len(PF.MAGIC)] == PF.MAGIC)
    check("前置图与写的缩略图字节一致", data[:end] == thumb)

    print("\n=== 2. 新布局读回：manifest 与 sections 一致 ===")
    m2, s2 = PF.read_project(p2)
    check("manifest 一致", m2 == manifest)
    check("section 数量一致", set(s2) == set(sections), f"{sorted(s2)}")
    check("缩略图字节一致（由前置图补回）", s2.get("thumbnail.png") == thumb)
    check("帧图字节一致", s2.get("actions/0000/frame_0000.png") == frame)

    print("\n=== 3. 旧布局（容器在最前）仍可读取 ===")
    p1 = tmp / "v1.sstool"
    p1.write_bytes(build_v1_bytes(manifest, sections))
    m1, s1 = PF.read_project(p1)
    check("旧布局 manifest 一致", m1 == manifest)
    check("旧布局 sections 一致", set(s1) == set(sections))
    check("旧布局缩略图来自 section 本身", s1.get("thumbnail.png") == thumb)

    print("\n=== 4. 无缩略图时退回旧布局（不产生坏的前置图）===")
    p3 = tmp / "no_thumb.sstool"
    PF.write_project(p3, manifest, {"actions/0000/frame_0000.png": frame})
    d3 = p3.read_bytes()
    check("无缩略图 -> 文件不以 PNG 开头", not d3.startswith(PF.PNG_SIGNATURE))
    check("无缩略图 -> 容器在最前", d3[:len(PF.MAGIC)] == PF.MAGIC)
    m3, s3 = PF.read_project(p3)
    check("无缩略图仍可读回", m3 == manifest and "actions/0000/frame_0000.png" in s3)

    print("\n=== 5. 缩略图不是合法 PNG 时安全退回 ===")
    p4 = tmp / "badthumb.sstool"
    PF.write_project(p4, manifest, {"thumbnail.png": b"not a png at all"})
    d4 = p4.read_bytes()
    check("坏缩略图 -> 不放前置，容器在最前", d4[:len(PF.MAGIC)] == PF.MAGIC)
    m4, s4 = PF.read_project(p4)
    check("坏缩略图 -> 仍作为 section 保留", s4.get("thumbnail.png") == b"not a png at all")

    print("\n=== 6. 损坏文件必须报错而不是崩溃 ===")
    cases = [
        ("空文件", b""),
        ("只有 PNG 没有容器", thumb),
        ("PNG 声明残缺", thumb[:len(thumb) // 2]),
        ("容器魔数被改", PF.MAGIC[:-1] + b"X" + b"\x01\x00\x00\x00" + b"\x00" * 20),
    ]
    for name, blob in cases:
        f = tmp / f"bad_{abs(hash(name))}.sstool"
        f.write_bytes(blob)
        try:
            PF.read_project(f)
            check(f"{name} -> 应报错", False, "却成功读出了")
        except ValueError as e:
            check(f"{name} -> ValueError", True, str(e)[:28])
        except Exception as e:  # noqa: BLE001
            check(f"{name} -> 应报 ValueError", False, f"{type(e).__name__}: {e}")

    print("\n=== 7. 大图前置缩略图仍能被系统解码器读取（结构完整性）===")
    big = make_png(512, 384)
    p5 = tmp / "big.sstool"
    PF.write_project(p5, manifest, {"thumbnail.png": big, "x": b"y" * 100000})
    d5 = p5.read_bytes()
    e5 = PF.png_image_end(d5)
    check("大缩略图定位正确", e5 == len(big))
    _, s5 = PF.read_project(p5)
    check("大文件 sections 完整", s5.get("x") == b"y" * 100000)

    print()
    if FAIL:
        print("格式回归失败：", *FAIL, sep="\n  - ")
        return 1
    print("工程文件格式回归全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
