"""验证抠图算法（spritesheet/keying.py）。

纯 Pillow、不需要 Qt，所以不用 offscreen 就能跑：
    .venv\\Scripts\\python.exe tools\\verify_keying.py

覆盖两类断言：
1. **行为**：猜色、容差、只清边缘连通、软边、收边、去色溢、只减不增、统计字段；
2. **正确性对照**：把"行程 + 并查集"求边缘连通的结果，跟朴素逐像素 flood fill 的结果
   逐像素比对 —— 前者快（行程级循环），后者慢但显然正确，用后者当尺子量前者。
"""
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageChops, ImageFilter                # noqa: E402

from spritesheet import keying                          # noqa: E402

FAILED = []


def check(label, cond, detail=""):
    if cond:
        print(f"  [OK]   {label} {detail}")
    else:
        print(f"  [FAIL] {label} {detail}")
        FAILED.append(label)


def make_sprite(bg=(255, 0, 255), fg=(40, 80, 200), size=(40, 30), box=(15, 10, 25, 20)):
    img = Image.new("RGBA", size, bg + (255,))
    img.paste(fg + (255,), box)
    return img


def alpha_at(img, x, y):
    return img.getpixel((x, y))[3]


print("=== 1. 猜背景色 ===")
img = make_sprite()
check("纯色底：猜出来的就是底色", keying.estimate_bg_color(img) == (255, 0, 255),
      keying.estimate_bg_color(img))

# 底色 + 8% 随机杂点（AI 图常见的噪点）：不能因为几个脏点就猜偏
noisy = Image.new("RGBA", (60, 40), (250, 251, 249, 255))
rnd = random.Random(20260923)
pix = noisy.load()
for _ in range(int(60 * 40 * 0.08)):
    pix[rnd.randrange(60), rnd.randrange(40)] = (rnd.randrange(256), rnd.randrange(256),
                                                 rnd.randrange(256), 255)
guess = keying.estimate_bg_color(noisy)
check("带 8% 杂点：仍猜出主色（不是被脏点带跑）",
      guess is not None and max(abs(g - t) for g, t in zip(guess, (250, 251, 249))) <= 4, guess)

blank = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
check("整张全透明：返回 None（提示用户已经是透明底）", keying.estimate_bg_color(blank) is None)

print("=== 2. 基本抠除 ===")
src = make_sprite()
src_copy = src.copy()
out, stats = keying.key_image(src, (255, 0, 255), tolerance=28, soft=0, edge_only=True,
                              shrink=0, despill=0)
check("背景被清成透明", alpha_at(out, 0, 0) == 0 and alpha_at(out, 39, 29) == 0,
      (alpha_at(out, 0, 0), alpha_at(out, 39, 29)))
check("角色不透明", alpha_at(out, 20, 15) == 255, alpha_at(out, 20, 15))
check("边界精确：方块外 1px 透明、方块内 1px 不透明",
      alpha_at(out, 14, 15) == 0 and alpha_at(out, 15, 15) == 255 and alpha_at(out, 25, 15) == 0,
      (alpha_at(out, 14, 15), alpha_at(out, 15, 15), alpha_at(out, 25, 15)))
check("角色颜色没被改", out.getpixel((20, 15))[:3] == (40, 80, 200), out.getpixel((20, 15)))
check("**没有就地修改传入的图**（快照共享引用，改了会毁掉撤销）",
      ImageChops.difference(src, src_copy).getbbox() is None)
check("统计：内容占比从 100% 降到方块占比", 8.0 < stats["content_after_pct"] < 12.0
      and stats["content_before_pct"] > 99.0,
      (round(stats["content_before_pct"], 1), round(stats["content_after_pct"], 1)))
check("统计：背景占比 ~92%", 87.0 < stats["changed_pct"] < 93.0, round(stats["changed_pct"], 1))

print("=== 3. 容差（底色带噪点） ===")
grainy = Image.new("RGBA", (40, 30), (255, 0, 255, 255))
gpix = grainy.load()
for y in range(30):
    for x in range(40):
        if not (15 <= x < 25 and 10 <= y < 20):
            gpix[x, y] = (255 - rnd.randrange(13), rnd.randrange(13), 255 - rnd.randrange(13), 255)
grainy.paste((40, 80, 200, 255), (15, 10, 25, 20))
loose, _ = keying.key_image(grainy, (255, 0, 255), tolerance=28, soft=0, despill=0)
tight, _ = keying.key_image(grainy, (255, 0, 255), tolerance=4, soft=0, despill=0)
check("容差 28：±12 的噪点底清干净了", alpha_at(loose, 0, 0) == 0 and alpha_at(loose, 39, 0) == 0)
check("容差 4：同样这张图清不干净（说明容差真的在起作用）", alpha_at(tight, 0, 0) != 0,
      alpha_at(tight, 0, 0))

print("=== 4. 只清边缘连通（保护角色内部的同色像素） ===")
hole = Image.new("RGBA", (40, 30), (255, 0, 255, 255))
hole.paste((40, 80, 200, 255), (10, 5, 30, 25))          # 角色
hole.paste((255, 0, 255, 255), (18, 12, 22, 16))         # 角色内部一块"和背景同色"的区域
inner_on, _ = keying.key_image(hole, (255, 0, 255), tolerance=28, soft=0, edge_only=True, despill=0)
inner_off, _ = keying.key_image(hole, (255, 0, 255), tolerance=28, soft=0, edge_only=False, despill=0)
check("开着：内部同色像素被保住（白衣服/眼白不会被吃掉）", alpha_at(inner_on, 20, 14) == 255,
      alpha_at(inner_on, 20, 14))
check("关掉：同一块被清掉（说明开关有效）", alpha_at(inner_off, 20, 14) == 0,
      alpha_at(inner_off, 20, 14))
check("开着时外部背景照样清掉", alpha_at(inner_on, 0, 0) == 0)

print("=== 5. 软边（抗锯齿边缘） ===")
aa = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
aa.paste((255, 0, 0, 255), (5, 5, 15, 15))
# 把边缘一圈换成"红白各半"的混合像素，模拟抗锯齿
apix = aa.load()
for i in range(5, 15):
    apix[i, 5] = (255, 128, 128, 255)
    apix[i, 14] = (255, 128, 128, 255)
soft_out, _ = keying.key_image(aa, (255, 255, 255), tolerance=28, soft=150, edge_only=True, despill=0)
hard_out, _ = keying.key_image(aa, (255, 255, 255), tolerance=28, soft=0, edge_only=True, despill=0)
soft_edge = alpha_at(soft_out, 10, 5)
hard_edge = alpha_at(hard_out, 10, 5)
check("软边：混合像素拿到中间 alpha（不再是锯齿）", 0 < soft_edge < 255, soft_edge)
check("硬边：同一像素只有 0/255 两种值", hard_edge in (0, 255), hard_edge)

print("=== 6. 收边 / 扩张 ===")
flat, _ = keying.key_image(src, (255, 0, 255), tolerance=28, soft=0, despill=0)
shrunk, _ = keying.key_image(src, (255, 0, 255), tolerance=28, soft=0, shrink=1, despill=0)
grown, _ = keying.key_image(src, (255, 0, 255), tolerance=28, soft=0, shrink=-1, despill=0)


def content_pixels(image):
    return sum(image.getchannel("A").histogram()[128:])


check("收边 1px：内容像素变少（吃掉最外圈残留）",
      content_pixels(shrunk) < content_pixels(flat), (content_pixels(shrunk), content_pixels(flat)))
check("扩张 1px：内容像素变多（边缘被啃掉时补回来）",
      content_pixels(grown) > content_pixels(flat), (content_pixels(grown), content_pixels(flat)))

print("=== 7. 去色溢 ===")
# 白底上一个"半透明红"边缘像素：混进去的白色要能减掉
edge = Image.new("RGBA", (12, 12), (255, 255, 255, 255))
edge.paste((255, 0, 0, 255), (3, 3, 9, 9))
epix = edge.load()
for i in range(3, 9):
    epix[i, 3] = (255, 128, 128, 255)
no_despill, _ = keying.key_image(edge, (255, 255, 255), tolerance=28, soft=150, despill=0)
yes_despill, _ = keying.key_image(edge, (255, 255, 255), tolerance=28, soft=150, despill=100)
before = no_despill.getpixel((5, 3))
after = yes_despill.getpixel((5, 3))
check("去色溢：混进去的白被减掉（G/B 明显下降、红保留）",
      after[1] < before[1] - 30 and after[0] >= 250, (before, after))

# 再与管线解耦地单独测一次 _apply_despill 本身：手算 F=(128*255-(255-168)*255)/168=62
manual = keying._apply_despill(Image.new("RGBA", (1, 1), (255, 128, 128, 255)),
                               Image.new("L", (1, 1), 168), (255, 255, 255), 100)
check("去色溢（单独测，手算核对）：(255,128,128)+alpha168+白底 -> G≈62",
      abs(manual.getpixel((0, 0))[1] - 62) <= 3, manual.getpixel((0, 0)))

print("=== 8. 只减不增 ===")
already = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
already.paste((10, 10, 10, 0), (4, 4, 8, 8))             # 已经有一块透明
keep, _ = keying.key_image(already, (255, 255, 255), tolerance=28, soft=40, despill=100)
check("原本透明的像素不会被写回不透明", alpha_at(keep, 5, 5) == 0, alpha_at(keep, 5, 5))
check("同一个像素的颜色也没被乱改", keep.getpixel((5, 5))[:3] == (10, 10, 10), keep.getpixel((5, 5)))

print("=== 9. alpha 查找表边界 ===")
lut = keying.alpha_lut(28, 40)
check("色距 ≤ 容差 -> 0", lut[0] == 0 and lut[28] == 0)
check("刚超出容差 -> 很小但不为 0", 0 < lut[29] < 10, lut[29])
check("容差 + 软边 -> 255", lut[68] == 255, lut[68])
check("软边 0 时是硬边（只有 0/255）", set(keying.alpha_lut(28, 0)) == {0, 255})

print("=== 10. 连通块计数 ===")
two = Image.new("RGBA", (60, 30), (255, 255, 255, 255))
two.paste((0, 0, 0, 255), (5, 0, 20, 30))                # 上下贯通，把背景切成左右两块
_, stats_two = keying.key_image(two, (255, 255, 255), tolerance=28, soft=0, despill=0)
check("背景被切成两块时计数为 2", stats_two["components"] == 2, stats_two["components"])

print("=== 11. 正确性对照：行程+并查集 vs 朴素逐像素 flood fill ===")


def naive_edge_mask(pil_img, bg, tol):
    """显然正确的慢版本：从最外圈能走到的候选像素做 4 连通 BFS。"""
    w, h = pil_img.size
    rgb = pil_img.convert("RGB").load()
    cand = [[max(abs(rgb[x, y][k] - bg[k]) for k in range(3)) <= tol
             for x in range(w)] for y in range(h)]
    seen = [[False] * w for _ in range(h)]
    stack = []
    for x in range(w):
        for y in (0, h - 1):
            if cand[y][x]:
                stack.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if cand[y][x]:
                stack.append((x, y))
    while stack:
        x, y = stack.pop()
        if seen[y][x] or not cand[y][x]:
            continue
        seen[y][x] = True
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and cand[ny][nx] and not seen[ny][nx]:
                stack.append((nx, ny))
    return seen


# 造一张有噪点、有多个色块的小图，尽量把行程逻辑逼到边角情况
rnd2 = random.Random(7)
W, H = 61, 43
fuzz = Image.new("RGBA", (W, H), (255, 255, 255, 255))
fpix = fuzz.load()
for y in range(H):
    for x in range(W):
        tone = 255 - rnd2.randrange(20) if rnd2.random() < 0.3 else 255
        fpix[x, y] = (tone, tone, tone, 255)
for _ in range(7):                                        # 随机色块，制造内部同色区域
    x0, y0 = rnd2.randrange(W - 8), rnd2.randrange(H - 8)
    fpix[x0, y0] = (rnd2.randrange(256), rnd2.randrange(256), rnd2.randrange(256), 255)
    for dx in range(rnd2.randrange(3, 8)):
        for dy in range(rnd2.randrange(3, 8)):
            if x0 + dx < W and y0 + dy < H:
                fpix[x0 + dx, y0 + dy] = (30, 30, 30, 255)

fast_mask, _ = keying.edge_connected_mask(keying.diff_map(fuzz, (255, 255, 255)), 28)
fast_px = fast_mask.load()
slow = naive_edge_mask(fuzz, (255, 255, 255), 28)
mismatch = [(x, y) for y in range(H) for x in range(W)
            if (fast_px[x, y] == 255) != slow[y][x]]
check("行程+并查集的结果与朴素 flood fill 逐像素一致（61×43 随机图）",
      not mismatch, f"不一致 {len(mismatch)} 处 {mismatch[:5]}")

print("=== 12. 性能（拖滑块要能实时预览） ===")
for side in (1024, 2048):
    big = Image.new("RGBA", (side, side), (255, 0, 255, 255))
    big.paste((40, 80, 200, 255), (side // 4, side // 4, side * 3 // 4, side * 3 // 4))
    t0 = time.perf_counter()
    _, big_stats = keying.key_image(big, (255, 0, 255))
    ms = (time.perf_counter() - t0) * 1000
    print(f"  {side}×{side} 全参数一跑 {ms:.0f}ms（连通块 {big_stats['components']}）")
    check(f"{side}² 一次抠图 < 1000ms（实时预览前提）", ms < 1000, f"{ms:.0f}ms")

# 最费的情况：边缘有抗锯齿过渡（半透明那一圈很大，去色溢躲不掉）
blurred = Image.new("RGBA", (2048, 2048), (255, 255, 255, 255))
blurred.paste((40, 80, 200, 255), (400, 400, 1600, 1600))
blurred = blurred.convert("RGB").filter(ImageFilter.GaussianBlur(6)).convert("RGBA")
t0 = time.perf_counter()
_, blur_stats = keying.key_image(blurred, (255, 255, 255))
ms = (time.perf_counter() - t0) * 1000
print(f"  2048×2048 抗锯齿边（最费的情况）{ms:.0f}ms（连通块 {blur_stats['components']}）")
check("抗锯齿大图一次抠图 < 1000ms（拖滑块才不会卡）", ms < 1000, f"{ms:.0f}ms")

print()
if FAILED:
    print(f"KEYING FAILED {len(FAILED)}")
    print("失败项：")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("KEYING OK")