# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for the write-capable MCP tools."""
from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spritesheet.mcp_server import (  # noqa: E402
    add_composite_frame, add_frame, add_source_image, create_action,
    create_empty_project, create_project_from_json, duplicate_frame,
    export_project_asset, extract_animation_frame_png, key_project_background,
    extract_frame_png,
    move_source_image, read_animation_document, read_sstool_project,
    remove_source_image, reorder_frames, update_animation_document,
    update_frame,
)
from spritesheet.project_file import png_bytes, write_project  # noqa: E402


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"PASS {name}")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="sstool_mcp_ext_"))
    try:
        project = root / "new.sstool"
        created = create_empty_project(str(project), 32, 32)
        check("create empty", created["size"] == [32, 32])
        check("create action", create_action(str(project), "idle")["frame_count"] == 0)

        red = root / "red.png"; blue = root / "blue.png"
        Image.new("RGBA", (6, 4), (255, 0, 0, 255)).save(red)
        Image.new("RGBA", (4, 6), (0, 0, 255, 255)).save(blue)
        check("add frame", add_frame(str(project), "idle", str(red))["frame_count"] == 1)
        check("duplicate frame", duplicate_frame(str(project), "idle", 0)["frame_count"] == 2)
        check("update frame", "rotation" in update_frame(
            str(project), "idle", 1, rotation=90, mirror_h=True,
            offset=[2, -1], duration_ms=250)["changed"])
        update_frame(str(project), "idle", 0,
                     quad=[0.0, 0.0, 0.9, 0.1, 1.0, 1.0, 0.1, 0.9])
        check("quad extract", extract_frame_png(str(project), "idle", 0)["width"] > 0)
        check("reorder frames", reorder_frames(str(project), "idle", [1, 0])["order"] == [1, 0])
        check("add source", add_source_image(str(project), str(blue))["source_count"] == 1)
        check("move source", move_source_image(str(project), 0, 0)["index"] == 0)
        check("remove source", remove_source_image(str(project), 0)["source_count"] == 0)
        composite = add_composite_frame(
            str(project), "idle",
            [{"image_path": str(red), "x": 0, "y": 0},
             {"image_path": str(blue), "x": 6, "y": 0}],
        )
        check("composite frame", composite["part_count"] == 2)
        keyed = key_project_background(
            str(project), mode_id="traditional", scope="frames", bg_color=[0, 0, 0],
        )
        check("key project", keyed["changed_sections"])
        check("cartoon key project", key_project_background(
            str(project), mode_id="cartoon", scope="frames", bg_color=[0, 0, 0],
        )["changed_sections"])

        exported = export_project_asset(str(project), "native_json", str(root))
        check("native json export", any(p.endswith(".json") for p in exported["files"]))
        check("png export", export_project_asset(str(project), "png", str(root))["files"])
        check("sstool export", export_project_asset(str(project), "sstool", str(root))["files"])
        check("gif export", export_project_asset(str(project), "gif", str(root), "idle")["files"])
        check("frame export", export_project_asset(str(project), "frames_png", str(root), "idle")["files"])

        atlas = root / "atlas.png"
        Image.new("RGBA", (8, 8), (12, 34, 56, 255)).save(atlas)
        source_json = root / "atlas.json"
        source_json.write_text(json.dumps({
            "frames": {"walk_0": {"frame": {"x": 0, "y": 0, "w": 8, "h": 8}}},
            "meta": {"image": "atlas.png"},
        }), encoding="utf-8")
        converted = root / "converted.sstool"
        check("json conversion", create_project_from_json(str(source_json), str(converted))["actions"])
        check("converted readable", read_sstool_project(str(converted))["actions"][0]["frames"] == 1)

        anim = root / "anim.sstool"
        frame_name = "anim/frame_0000.png"
        manifest = {"format": "spritesheet-tool-project", "version": 2,
                    "anim_doc": {"name": "demo", "index": 0, "durations": [100],
                                  "frames": [frame_name], "ops": []}}
        write_project(anim, manifest, {frame_name: png_bytes(Image.new("RGBA", (5, 4), (255, 0, 0, 255)))})
        check("read anim doc", read_animation_document(str(anim))["frame_count"] == 1)
        update_animation_document(str(anim), {"kind": "transform", "t": "flip_h"})
        frame = extract_animation_frame_png(str(anim), 0)
        check("extract anim doc", base64.b64decode(frame["png_base64"]))
        check("export anim gif", export_project_asset(str(anim), "gif", str(root))["anim_doc"] is True)
        check("export anim frames", export_project_asset(str(anim), "frames_png", str(root))["files"])
        print("VERIFY MCP EXTENDED OK")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
