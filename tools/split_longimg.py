# -*- coding: utf-8 -*-
"""长图按章节切分渲染 —— 同时产出图片与**逐段对应的真值**。

    python tools/split_longimg.py --html <a.html> [--html <b.html> ...] \
        --out-images <图片目录> --out-gt <真值目录>

为什么按章节切、而不是截一张长图再按像素切：

1. **图文对齐由构造保证。** 长图切分只能按像素高度猜边界，切歪了就把两段内容混在一起；
   而且真值还得反推"哪段文字落在哪几个像素里"。按章节切则每段的图与文来自同一次渲染，
   对齐关系是构造出来的，不存在误差。
2. **章节不会被拆散。** 一个 `<h2>` 及其后内容作为一个整体，符合"段落语义完整"。

切分方案（章节组合，1-based 闭区间）：

    数学（8 章）: [1-3] [4-6] [7-8]      -> 3 段
    英语（5 章）: [1]   [2-3] [4]   [5]  -> 4 段
    茶  （6 章）: [1-2] [3-4] [5-6]      -> 3 段

高度上限 3300 CSS px：超过这个高度，VLM 会等比缩放到长边 2048，
宽度被压到约 350px，文字糊掉 —— 于是所有档位都错得离谱，协议敏感性反而测不出来。
这条约束在真实样本上已经用超长图踩过一次。

依赖：playwright（`pip install playwright && playwright install chromium`）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

WIDTH = 900
SCALE = 2.0
MAX_H = 3300

# 章节组合。key 用文件名前缀匹配，避免路径差异导致失效。
PLAN = {
    "01_数学": {"src_id": "0011", "segments": [[1, 3], [4, 6], [7, 8]]},
    "02_英语": {"src_id": "0012", "segments": [[1, 1], [2, 3], [4, 4], [5, 5]]},
    "03_茶": {"src_id": "0013", "segments": [[1, 2], [3, 4], [5, 6]]},
}


def _force_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _collect(page):
    """抓取可复用的结构片段：章节 HTML、前言、封面、页脚、样式。"""
    return page.evaluate("""() => {
        const body = document.querySelector('.body');
        const kids = Array.from(body.children);
        const h2idx = [];
        kids.forEach((el, i) => { if (el.matches('h2')) h2idx.push(i); });
        const chaps = h2idx.map((start, k) => {
            const end = (k+1 < h2idx.length) ? h2idx[k+1] : kids.length;
            return kids.slice(start, end).map(x => x.outerHTML).join('');
        });
        const preface = kids.slice(0, h2idx[0]).map(e => e.outerHTML).join('');
        const footer = document.querySelector('footer');
        const cover = document.querySelector('.cover');
        const style = Array.from(document.querySelectorAll('style'))
                           .map(s => s.textContent).join('\\n');
        return { chaps, preface,
                 footer: footer ? footer.outerHTML : '',
                 cover: cover ? cover.outerHTML : '',
                 style };
    }""")


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="长图按章节切分 + 逐段真值")
    ap.add_argument("--html", action="append", required=True)
    ap.add_argument("--out-images", required=True)
    ap.add_argument("--out-gt", required=True)
    ap.add_argument("--prefix", default="gen", help="样本 id 前缀，默认 gen")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少依赖：pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 2

    os.makedirs(args.out_images, exist_ok=True)
    os.makedirs(args.out_gt, exist_ok=True)

    report, n_total = {}, 0
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": WIDTH, "height": 1200},
                                device_scale_factor=SCALE)

        for hp in args.html:
            src = Path(hp).resolve()
            stem = src.stem
            plan = next((v for k, v in PLAN.items() if k in stem), None)
            if plan is None:
                print("[跳过] 未在 PLAN 中登记：%s" % stem)
                continue

            page.goto(src.as_uri(), wait_until="networkidle")
            page.evaluate("() => document.fonts.ready")
            page.wait_for_timeout(250)
            data = _collect(page)

            segs = []
            ranges = plan["segments"]
            for i, (s, e) in enumerate(ranges):
                seg_letter = chr(ord("a") + i)
                sid = "%s_%s%s" % (args.prefix, plan["src_id"], seg_letter)
                is_first, is_last = (i == 0), (i == len(ranges) - 1)

                frag = "".join(data["chaps"][s - 1:e])
                if is_first:
                    frag = data["preface"] + frag
                doc = ('<!DOCTYPE html><html lang="zh-CN"><head>'
                       '<meta charset="UTF-8"><style>%s</style></head>'
                       '<body><div class="wrap">%s<div class="body">%s</div>%s'
                       '</div></body></html>'
                       % (data["style"], data["cover"] if is_first else "",
                          frag, data["footer"] if is_last else ""))

                page.set_content(doc, wait_until="load")
                page.evaluate("() => document.fonts.ready")
                page.add_style_tag(
                    content="*,*::before,*::after{animation:none!important;"
                            "transition:none!important;}")
                page.wait_for_timeout(180)
                page.evaluate("""() => {
                    document.body.style.margin = '0';
                    const w = document.querySelector('.wrap');
                    const r = w.getBoundingClientRect();
                    const h = Math.ceil(r.height + r.top + 24);
                    document.body.style.height = h + 'px';
                    document.body.style.overflow = 'hidden';
                }""")
                page.wait_for_timeout(80)

                png = os.path.join(args.out_images, sid + ".png")
                page.screenshot(path=png, full_page=True)

                # ★ 真值：同一次渲染的可见文本，与图片严格对应
                gt_text = (page.inner_text(".wrap") or "").strip()
                with open(os.path.join(args.out_gt, sid + ".md"),
                          "w", encoding="utf-8", newline="\n") as f:
                    f.write(gt_text + "\n")

                box = page.evaluate("""() => {
                    const w = document.querySelector('.wrap');
                    const r = w.getBoundingClientRect();
                    return {w: Math.round(r.width), h: Math.round(r.height)};
                }""")
                ok_h = box["h"] <= MAX_H
                segs.append({"sid": sid, "chapters": "%d-%d" % (s, e),
                             "css": "%dx%d" % (box["w"], box["h"]),
                             "png": "%dx%d" % (int(box["w"] * SCALE),
                                               int(box["h"] * SCALE)),
                             "gt_chars": len(gt_text), "ok": ok_h})
                n_total += 1

            report[stem] = segs
            print("[%s]" % stem)
            for r in segs:
                print("    %-16s 章 %-5s CSS %-10s PNG %-12s 真值 %4d 字 %s"
                      % (r["sid"], r["chapters"], r["css"], r["png"],
                         r["gt_chars"], "" if r["ok"] else "  <== 超 3300px"))

        browser.close()

    print("-" * 62)
    print("共 %d 段；图片 -> %s" % (n_total, args.out_images))
    print("           真值 -> %s" % args.out_gt)
    print("[!] 真值取自同一份渲染，source=generated，只能证明方法可行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
