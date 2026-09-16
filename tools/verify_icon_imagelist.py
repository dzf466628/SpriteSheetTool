"""图标回归 —— 走**资源管理器真实使用的路径**（系统图标列表）。

为什么必须专门有这么一个工具：上一轮改 `SSTool.ico` 时，我用
`IShellItemImageFactory`（取缩略图用的接口）做 A/B，结论是「新旧渲染逐像素相同」，
于是把方形画布改成了「原比例帧」。用户随后截图反馈图标被压扁 —— 因为

  * `IShellItemImageFactory`  **保持**长宽比；
  * 而 Explorer 图标视图走 `SHGetFileInfo` / 系统图标列表，列表项是**方形位图**，
    **非方形帧会被拉伸填满方形槽位**（185×256 拉成方形 = 横向拉宽 1.38 倍）。

所以这个工具直接问 `SHGetFileInfo(SHGFI_ICON | SHGFI_LARGEICON)`，量图里那圈品牌粉的
包围盒宽高比，与源图的 0.723 对照。方形帧应得到 ~0.75，非方形帧会得到 1.000。

为了绕开图标缓存，每次运行都**新注册一个临时扩展名**（用完即删），
免得测到的还是上一次的位图。

用法: .venv\\Scripts\\python.exe tools\\verify_icon_imagelist.py
"""
from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []

SHGFI_ICON = 0x00000100
SHGFI_LARGEICON = 0x00000000
SHGFI_SMALLICON = 0x00000001
DIB_RGB_COLORS = 0
BI_RGB = 0
DI_NORMAL = 0x0003


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HANDLE),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
        ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
        ("bmPlanes", ctypes.c_ushort), ("bmBitsPixel", ctypes.c_ushort),
        ("bmBits", ctypes.c_void_p),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# 必须显式声明 argtypes/restype：不声明时 ctypes 用默认的 c_int 返回类型，
# 会把 DWORD_PTR 的返回值截断，而且字符串参数也不会按 LPCWSTR 规范传递。
_SHGETFILESINFOW = shell32.SHGetFileInfoW
_SHGETFILESINFOW.restype = ctypes.c_size_t
_SHGETFILESINFOW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW),
    wintypes.UINT, wintypes.UINT,
]

user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
user32.GetIconInfo.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [wintypes.HICON]
gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
user32.DrawIconEx.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
    ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HANDLE, wintypes.UINT,
]
user32.DrawIconEx.restype = wintypes.BOOL


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK' if ok else 'FAIL'}]   {label} {detail}")
    if not ok:
        FAILURES.append(label)


def hicon_to_image(hicon: int) -> Image.Image | None:
    """HICON -> PIL RGBA。

    用 DrawIconEx 把图标画进一张**自上而下**的 32bpp DIB 里再读位。
    不要用 GetIconInfo + GetDIBits：图标里的 HBITMAP 是 DDB，
    GetDIBits 在没有 DC 的情况下两种 biHeight（正/负）都返回 0（实测）。
    """
    info = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        return None

    hdc = gdi32.CreateCompatibleDC(None)
    if not hdc:
        return None

    bmp = BITMAP()
    width = height = 0
    if info.hbmColor and gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bmp), ctypes.byref(bmp)):
        width, height = int(bmp.bmWidth), int(bmp.bmHeight)
    if not width or not height:
        width = height = 32          # 兜底：图标视图最大也就 32

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height        # 负数 = 自上而下
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = BI_RGB
    bmi = BITMAPINFO()
    bmi.bmiHeader = header

    bits = ctypes.c_void_p()
    dib = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                 ctypes.byref(bits), None, 0)
    if not dib or not bits:
        gdi32.DeleteDC(hdc)
        return None

    old = gdi32.SelectObject(hdc, dib)
    try:
        ctypes.memset(bits, 0, width * height * 4)
        user32.DrawIconEx(hdc, 0, 0, hicon, width, height, 0, None, DI_NORMAL)
        buf = ctypes.string_at(bits, width * height * 4)
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(dib)
        gdi32.DeleteDC(hdc)
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)

    return Image.frombytes("RGBA", (width, height), buf, "raw", "BGRA")


def shell_icon(path: Path, *, large: bool) -> Image.Image | None:
    info = SHFILEINFOW()
    flags = SHGFI_ICON | (SHGFI_LARGEICON if large else SHGFI_SMALLICON)
    rc = _SHGETFILESINFOW(str(path), 0, ctypes.byref(info), ctypes.sizeof(info), flags)
    if not rc or not info.hIcon:
        return None
    try:
        return hicon_to_image(info.hIcon)
    finally:
        user32.DestroyIcon(info.hIcon)


def pink_bbox(img: Image.Image):
    px = img.load()
    hits = [(x, y) for y in range(img.height) for x in range(img.width)
            if px[x, y][3] > 80
            and abs(px[x, y][0] - 251) < 60 and abs(px[x, y][1] - 85) < 70
            and abs(px[x, y][2] - 130) < 70]
    if not hits:
        return None
    xs = [p[0] for p in hits]
    ys = [p[1] for p in hits]
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def probe(icon_path: Path, label: str, *, assert_aspect: bool = True) -> float | None:
    """注册临时扩展名 -> 该图标，用 shell 路径取 32px/16px 图标量粉色框比例。

    返回 32px 档的实测宽高比（拿不到就 None）。assert_aspect=False 时只打印不断言 ——
    软件本体图标里没有那圈「图的边框」，粉色只是零星分布，量不出比例。
    """
    import winreg

    stamp = uuid.uuid4().hex[:8]
    ext = f".sstprobe{stamp}"
    progid = f"SSToolProbe{stamp}"
    probe_file = ROOT / "tools" / f"_probe{ext}"
    probe_file.write_bytes(b"x")

    classes = winreg.HKEY_CURRENT_USER
    keys = []
    ratio_32 = None
    try:
        with winreg.CreateKey(classes, rf"Software\Classes\{ext}") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, progid)
        keys.append(rf"Software\Classes\{ext}")
        with winreg.CreateKey(classes, rf"Software\Classes\{progid}\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(icon_path))
        keys.append(rf"Software\Classes\{progid}")

        print(f"  --- {label} （临时扩展名 {ext}）---")
        for large, name, expect_h in ((True, "32px", 32), (False, "16px", 16)):
            img = shell_icon(probe_file, large=large)
            if img is None:
                check(f"{name} 取到图标", False, "SHGetFileInfo 没返回图标")
                continue
            box = pink_bbox(img)
            if box is None:
                check(f"{name} 有品牌粉", False, "图里找不到品牌粉")
                continue
            w, h = box
            ratio = w / h
            if name == "32px":
                ratio_32 = ratio
            if not assert_aspect:
                print(f"  [---]  {name} 粉色范围 {w}x{h}（宽高比 {ratio:.3f}，仅打印）")
                continue
            # 源图 185:256 = 0.723；方形帧实测 0.69~0.75；被拉伸则是 1.000
            ok = abs(ratio - 0.723) <= 0.08
            check(f"{name} 未变形（粉框 {w}x{h}，宽高比 {ratio:.3f}，应为 ~0.723）", ok,
                  "" if ok else "<-- 被拉伸了！检查是否用了非方形帧")
            if ok:
                check(f"{name} 图按高度铺满整档（{h} == {expect_h}）", h == expect_h, f"实际 {h}")
    finally:
        for key in keys:
            try:
                winreg.DeleteKey(classes, key + r"\DefaultIcon")
            except OSError:
                pass
        for key in keys:
            try:
                winreg.DeleteKey(classes, key)
            except OSError:
                pass
        probe_file.unlink(missing_ok=True)
    return ratio_32


def self_check(ratio_32: float | None) -> None:
    """证明这个测试**真的能发现**被拉伸的图标。

    现场生成一个「原比例帧」的 ico（就是 v1.1.22 那个错误做法），
    它必须被量成 ~1.0；量不出来说明测试本身没有判别力。
    """
    import importlib.util
    import tempfile

    print("  --- 自检：故意做一个非方形帧的图标，测试必须发现它被拉伸 ---")
    spec = importlib.util.spec_from_file_location(
        "make_icons", ROOT / "tools" / "make_icons.py")
    make_icons = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_icons)

    with tempfile.TemporaryDirectory(prefix="sstool_icon_selfcheck_") as tmp:
        frames = make_icons.natural_frames(Image.open(ROOT / "SSTool_源图.png"))
        bad = Path(tmp) / "bad.ico"
        biggest = max(frames, key=lambda f: f.width * f.height)
        biggest.save(bad, format="ICO", sizes=[f.size for f in frames], append_images=frames)
        bad_ratio = probe(bad, "自检用非方形帧图标", assert_aspect=False)
        if bad_ratio is None:
            check("自检能取到图标", False)
            return
        check(f"自检：非方形帧被量成拉伸（{bad_ratio:.3f} ≈ 1.0）", bad_ratio > 0.9,
              f"实际 {bad_ratio:.3f} —— 测试没有判别力！")
        if ratio_32 is not None:
            check(f"正式图标与它明显不同（{ratio_32:.3f}）", bad_ratio - ratio_32 > 0.15,
                  f"差 {bad_ratio - ratio_32:.3f}")


def main() -> int:
    # 必须先初始化 COM（STA）。实测没有 CoInitialize 时 SHGetFileInfoW 一律返回 0
    # （表现为「取不到图标」）—— 四个组合里只有 CoInit=yes 的两个能拿到图标。
    ctypes.windll.ole32.CoInitialize(None)

    ratio = None
    for name, label, assert_aspect in (
        ("SSTool.ico", "工程文件图标", True),
        ("SpriteSheetTool.ico", "软件本体图标", False),
    ):
        path = ROOT / name
        if not path.exists():
            check(f"{name} 存在", False)
            continue
        got = probe(path, f"{name}（{label}）", assert_aspect=assert_aspect)
        if assert_aspect:
            ratio = got
        print()

    self_check(ratio)

    print()
    print(f"ICON IMAGELIST FAILED {len(FAILURES)}" if FAILURES else "ICON IMAGELIST OK")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
