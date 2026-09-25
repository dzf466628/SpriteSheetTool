# context-memory — SpriteSheetTool / 抽帧窗口

**Objective**: 抽帧窗口（抽帧按钮 + 实时结果 + 可撤回）做完整、无回归。验收 = 抽帧后**画面元素不位移**、
换档/改相似度都从"该动作所有帧"重算、撤销干净、三本回归全绿。

**Status**
- 已交付且验证：4 颗互斥高亮抽帧按钮（抽帧3/5、1/2、1/4、强力去重，右对齐）、点即抽+实时显示、
  撤销快照、取消/Esc 整轮撤回、相似度框可输入（回车立刻算／不动 2 秒算）、窗口 390×600 可缩放左下抓柄、
  模态、棋盘格预览只画当前帧、循环三档/帧率 1~100、缩略图共用 build_frame_thumb、
  标题「预览·动作名（N 帧·M 块）」、合成帧时相似帧按钮灰掉/抽帧点了只提示。
- 已修 P0：抽帧位移（真因两条：①帧↔元素映射错——老写法"帧号=composite_groups[k]"，
  现已复刻 crop_frames_for_action 的真实规则 + 越界跳过；②相似≠同尺寸 ⇒ max 变化 ⇒ 整体位移，
  已由 compensate_offsets 逐帧补偿）；换档位混乱（还原派生源后**没重建 frames**，已补）；
  取消卡住（取消路径不再刷新即将关闭的窗口）。
- 真进度条：`group_elements(progress=cb)` 两阶段报数 → 抽帧窗口 + 相似帧已接（应用级模态下 processEvents 安全）。
- 回归：compileall 0 / pyflakes(spritesheet) 0 / smoke OK / verify_decimate 42/42 / verify_similar 21/21 /
  verify_auto_align 45/45；诊断 tools\_diag_decimate_shift.py：5 种形态「内容/落点/偏移逐帧一致」+ 切换序列 ✅。

**Decisions**
- 抽帧删的是**选中元素**（元素编号全局，绝不重排），再 crop_frames_for_action 重建帧。
- 基准永远是打开窗口时记下的**原始帧表**（还原派生源后**必须重建 frames**）；换档/改相似度都先还原再算。
- 取消=整轮撤回并关（键 Esc/关闭同义）、只做撤回不刷新窗口；确定=收工。
- 动图文档模式 actions 为空 ⇒ 抽帧按钮禁用并写明原因（不需要给 anim op 链加抽帧 op）。
- 不打包、不升版本（1.1.37）。

**Facts**
- 根：`E:\开发源文件\SpriteSheetTool\SpriteSheetTool`；命令见 `HANDOFF.md`。
- 落点公式：`padding + (max−size)//2 + frame_offsets[i]`（`transform.compute_preview_layout`，positions 元素须下标取）。
- 每帧结构：`selected_indices` 派生 + `composite_groups` / `frame_layouts` 三条路；同序结构一堆（见 HANDOFF）。
- 一串 = 同相似组号 + 帧序相邻；每串留 ≥1 且必含"最标准那帧"；M 块 = 强力去重后帧数。
- 坑：Qt 槽/paintEvent 抛异常直接崩进程；中文串禁 ASCII 双引号；main_window/preview 是 CRLF；
  space_pan 过滤器**绝不吞键**（原图空格拖动靠它）。

**Next**
1. 探针收尾崩 0xC0000005 → **v1.1.40 已做**：断言转正为 `tools/verify_extract.py`（38/38、退出码 0），
   探针 `tools/_check_extract.py` 已退休；里面过期的注释「应回到 2」也改对了（正确是 3）。
2. `NEXT.md`/`README.md` 补相似帧、抽帧、导入进度三节与回归表。
3. 新度量（64×64 特征）的变异验证。
4. 合成帧的相似彩块（目前不标色，老行为）——用户未要求改，待问。

**Landmarks**：`spritesheet/ui/similar.py`（算法+plan/remap）、`extract_dialog.py`（窗口）、
`main_window.py`（`_on_extract_decimate` 等）、`tools/verify_decimate.py`、`tools/verify_extract.py`、`HANDOFF.md`。