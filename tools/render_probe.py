# -*- coding: utf-8 -*-
"""把探针集 HTML 渲染成图片与真值 —— 合成样本的「真值天然无损」就靠这一步。

    python tools/render_probe.py --html <a.html> [--html <b.html> ...] \
        --out-images <图片目录> --out-gt <真值目录>

设计要点（每一条都是踩过坑才加的）：

1. **按 `.page` 元素逐个渲染，不截整页长图再切。**
   整页长图会连续跨样本，切点只能靠像素高度猜，切歪了就把两个样本混在一起。
   逐个渲染则样本边界由 HTML 结构天然决定，切分逻辑为零。

2. **真值取自同一份 HTML 的 `inner_text`，不做 OCR。**
   这是 `source=generated` 的全部意义：真值与图片同源，不存在识别误差。
   必须显式声明它来自生成过程，不得与 `real` 样本混统。

3. **固定 viewport 宽度与 2 倍设备像素比。**
   宽度取 900 CSS px（公众号正文标准宽度），2× 后为 1800px，
   与真实样本的宽度量级对齐，避免"合成样本更清晰所以分数更好"这类不公平比较。

4. **截图前等字体就绪、冻结动画。**
   不等 `document.fonts.ready` 会把中文渲染成空白；
   不冻结动画会截到过渡中间态。

依赖：playwright（`pip install playwright && playwright install chromium`）
"""

from __future__ import annotations

import argparse
import os
import sys

VIEWPORT_W = 900
SCALE = 2.0


def _force_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def render_one_html(page, html_path: str, stem: str,
                    out_images: str, out_gt: str) -> list:
    """渲染单个 HTML 里的每个 `.page`，返回 [(sample_id, w, h)]。"""
    from pathlib import Path

    src = Path(html_path).resolve()
    if not src.exists():
        raise FileNotFoundError("HTML 不存在: %s" % src)

    page.goto(src.as_uri(), wait_until="networkidle")
    try:
        page.evaluate("() => document.fonts.ready")
    except Exception:
        pass
    page.add_style_tag(content="*,*::before,*::after{"
                               "animation:none!important;"
                               "transition:none!important;}")
    page.wait_for_timeout(300)

    blocks = page.query_selector_all(".page")
    if not blocks:
        raise RuntimeError("未找到任何 .page 元素：%s" % src)

    os.makedirs(out_images, exist_ok=True)
    os.makedirs(out_gt, exist_ok=True)

    made = []
    for i, blk in enumerate(blocks, 1):
        sid = "%s_%02d" % (stem, i)

        png = os.path.join(out_images, sid + ".png")
        box = blk.bounding_box()
        blk.screenshot(path=png)
        w = int(box["width"] * SCALE) if box else 0
        h = int(box["height"] * SCALE) if box else 0

        # 真值：同一份 HTML 的可见文本。newline="\n" 保证跨平台字节一致。
        text = (blk.inner_text() or "").strip()
        gt = os.path.join(out_gt, sid + ".md")
        with open(gt, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")

        made.append((sid, w, h, len(text)))
    return made


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="探针集 HTML → 图片 + 真值")
    ap.add_argument("--html", action="append", required=True,
                    help="探针集 HTML，可多次指定")
    ap.add_argument("--out-images", required=True)
    ap.add_argument("--out-gt", required=True)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少依赖：pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 2

    total_ok = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb"])
        ctx = browser.new_page(
            viewport={"width": VIEWPORT_W, "height": 1200},
            device_scale_factor=SCALE,
        )
        for hp in args.html:
            stem = os.path.splitext(os.path.basename(hp))[0]
            try:
                made = render_one_html(ctx, hp, stem, args.out_images, args.out_gt)
            except Exception as e:  # noqa: BLE001
                print("[失败] %s -> %s: %s" % (stem, type(e).__name__, e))
                continue
            print("[%s] 渲染 %d 个样本" % (stem, len(made)))
            for sid, w, h, n in made:
                flag = ""
                # 与真实样本对齐的宽度下限：过窄会让模型缩放后糊字，
                # 使"协议敏感性"被"分辨率不足"淹没（真实样本上已踩过这个坑）。
                if w and w < 600:
                    flag = "  <== 宽度不足 600px"
                print("    %-22s %5dx%-6d 真值 %4d 字%s" % (sid, w, h, n, flag))
                total_ok += 1
        browser.close()

    print("-" * 58)
    print("完成 %d 个样本；图片 -> %s；真值 -> %s"
          % (total_ok, args.out_images, args.out_gt))
    print("[!] 真值来自 HTML 生成过程（source=generated），")
    print("    只能证明方法可行，不得用于声称任何真实工具的水平。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
