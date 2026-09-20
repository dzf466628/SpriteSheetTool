# -*- coding: utf-8 -*-
import sys
from pathlib import Path
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
here = Path(__file__).resolve().parent
html = (here / "index.html").read_text(encoding="utf-8")
inj = """
<style id="check-override">
html,body{height:auto!important;overflow:visible!important;}
.viewport{position:static!important;inset:auto!important;display:block!important;height:auto!important;overflow:visible!important;}
.stage{width:100%!important;height:auto!important;max-height:none!important;max-width:none!important;overflow:visible!important;box-shadow:none!important;}
.slide{position:relative!important;inset:auto!important;opacity:1!important;visibility:visible!important;height:56.25vw!important;border-bottom:3px dashed #ff2d75;}
[class*="fade"],.row{opacity:1!important;transform:none!important;}
.token .bar i{width:100%!important;}
.progress,.pageno,.hint,.brandtag{display:none!important;}
/* 强制所有动画运行，角色不暂停，能看到完整走动 */
.slide .sprite,.slide .actor,.slide .sprite img.frames{animation-play-state:running!important;}
</style>
</head>"""
(here / "_check.html").write_text(html.replace("</head>", inj, 1), encoding="utf-8")
print("ok")
