# -*- coding: utf-8 -*-
"""把 out/ 的结果渲染成单文件静态 HTML（零依赖、双击即看、可离线分发）。

设计纪律（与方案第 07 节的交付形态一致）：
  * 只读不算——所有计算都在 runner 里完成，这里只负责呈现
  * 不引任何前端框架、不引 CDN、不写 JS 逻辑
  * 生成的文件可以被安全地当成附件发出，链接挂了也不影响阅读

用法：
    from src import make_report
    make_report.build(res, "out/report.html")

注意：模板用旧式 % 格式化，CSS 里出现的百分号必须写成 %%，
否则会报 "unsupported format character"（本项目已踩过一次）。
"""

from __future__ import annotations

import html
import math
import os
from typing import Dict, List

# 名次 -> 底色 / 字色（名次越好底色越深）
_RANK_STYLE = {
    1: ("#0F6E56", "#FFFFFF"),
    2: ("#5DCAA5", "#04342C"),
    3: ("#CECBF6", "#26215C"),
    4: ("#F5C4B3", "#4A1B0C"),
    5: ("#F7C1C1", "#501313"),
    6: ("#B4B2A9", "#2C2C2A"),
}


def _rank_cell(rank: int, total: int) -> str:
    bg, fg = _RANK_STYLE.get(rank, ("#F1EFE8", "#2C2C2A"))
    return '<td class="rk" style="background:%s;color:%s">%d</td>' % (bg, fg, rank)


def _curve_svg(curve: List[Dict], levels: List[str], models: List[str]) -> str:
    """宽容度曲线。纵轴用 log10 刻度，否则 0.0001 与 1.0 无法同图。"""
    W, H = 620, 260
    L, R, T, B = 52, 16, 16, 34
    plot_w, plot_h = W - L - R, H - T - B

    vals = [v for row in curve for v in (row[l] for l in levels) if v > 0]
    lo_exp = math.floor(math.log10(min(vals))) if vals else -4
    hi_exp = math.ceil(math.log10(max(vals))) if vals else 0
    lo_exp = min(lo_exp, hi_exp - 1)

    def y_of(v: float) -> float:
        v = max(v, 1e-6)
        e = math.log10(v)
        e = min(max(e, lo_exp), hi_exp)
        return T + plot_h * (hi_exp - e) / (hi_exp - lo_exp)

    def x_of(i: int) -> float:
        return L + plot_w * i / (len(levels) - 1)

    colors = ["#185FA5", "#0F6E56", "#993C1D", "#534AB7", "#854F0B", "#A32D2D"]
    parts: List[str] = []

    # 网格线 + y 轴刻度
    for e in range(lo_exp, hi_exp + 1):
        y = T + plot_h * (hi_exp - e) / (hi_exp - lo_exp)
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#D3D1C7" stroke-width="0.5"/>'
                     % (L, y, W - R, y))
        parts.append('<text x="%d" y="%.1f" font-size="10" fill="#5F5E5A" text-anchor="end" '
                     'dominant-baseline="central">%s</text>'
                     % (L - 6, y, ("1e%d" % e) if e != 0 else "1"))

    # x 轴刻度
    for i, lv in enumerate(levels):
        x = x_of(i)
        parts.append('<text x="%.1f" y="%d" font-size="11" fill="#2C2C2A" text-anchor="middle">%s</text>'
                     % (x, H - 14, lv))

    # 折线
    for mi, model in enumerate(models):
        row = next(r for r in curve if r["model"] == model)
        pts = " ".join("%.1f,%.1f" % (x_of(i), y_of(row[lv])) for i, lv in enumerate(levels))
        c = colors[mi % len(colors)]
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.5"/>' % (pts, c))
        for i, lv in enumerate(levels):
            parts.append('<circle cx="%.1f" cy="%.1f" r="2.5" fill="%s"/>'
                         % (x_of(i), y_of(row[lv]), c))
        parts.append('<text x="%.1f" y="%.1f" font-size="10" fill="%s">%s</text>'
                     % (x_of(len(levels) - 1) + 4, y_of(row[levels[-1]]), c, html.escape(model)))

    return ('<svg viewBox="0 0 %d %d" width="100%%" role="img" '
            'xmlns="http://www.w3.org/2000/svg">%s</svg>' % (W, H, "".join(parts)))


def build(res: Dict, out_path: str) -> str:
    meta = res["meta"]
    levels = meta["levels"]
    seg_modes = meta["seg_modes"]
    models = [r["model"] for r in res["stability"]]
    ranks = res["ranks"]

    by_key = {(r["seg_mode"], r["level"]): r for r in ranks}
    groups = [(s, l) for s in seg_modes for l in levels]

    # ---- 结论自动生成 ----
    n_shift = sum(1 for r in res["stability"] if r["rank_shift"] > 0)
    worst = res["stability"][0]
    mono_ok = not meta["monotonicity_violations"]

    # P5 档下置信区间重叠的模型对（分不出胜负）
    p5 = [r for r in res["scores"] if r["level"] == "P5" and r["seg_mode"] == "para"]
    tied = []
    for i in range(len(p5)):
        for j in range(i + 1, len(p5)):
            a, b = p5[i], p5[j]
            if not (a["ci_hi"] < b["ci_lo"] or b["ci_hi"] < a["ci_lo"]):
                tied.append((a["model"], b["model"]))

    # ---- 稳定度表 ----
    stab_rows = []
    for r in res["stability"]:
        bar = min(100, r["rank_shift"] * 25)
        stab_rows.append(
            "<tr><td>%s</td><td>%d</td><td>%d</td>"
            '<td><span class="bar" style="width:%dpx"></span> %d</td>'
            "<td>%s</td><td>%s</td></tr>"
            % (html.escape(r["model"]), r["rank_min"], r["rank_max"], bar, r["rank_shift"],
               r["rank_std"], "%d" % r["n_groups"])
        )

    # ---- 名次矩阵 ----
    head = "".join('<th colspan="%d">%s</th>' % (len(levels), html.escape(s)) for s in seg_modes)
    sub = "".join("<th>%s</th>" % lv for _ in seg_modes for lv in levels)
    body = []
    for model in models:
        cells = "".join(_rank_cell(by_key[(s, l)]["rank"], len(models)) for s, l in groups)
        body.append("<tr><th class=\"rowh\">%s</th>%s</tr>" % (html.escape(model), cells))

    doc = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>协议敏感性评测报告</title>
<style>
:root{--bg:#FAFAF8;--card:#FFFFFF;--tx:#2C2C2A;--tx2:#5F5E5A;--bd:#D3D1C7}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px;background:var(--bg);color:var(--tx);
font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:22px;font-weight:500;margin:0 0 6px}
h2{font-size:16px;font-weight:500;margin:34px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--bd)}
.sub{color:var(--tx2);font-size:13px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px 18px;margin:12px 0}
.meta{display:flex;flex-wrap:wrap;gap:8px 22px;font-size:13px;color:var(--tx2)}
.meta b{color:var(--tx);font-weight:500}
table{width:100%%;border-collapse:collapse;font-size:13px;background:var(--card)}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid #EDEBE5}
th{font-weight:500;color:var(--tx2);font-size:12px}
td.rk{text-align:center;font-size:12px;border-radius:4px}
th.rowh{font-weight:500;color:var(--tx);white-space:nowrap}
.bar{display:inline-block;height:9px;background:#185FA5;border-radius:5px;vertical-align:middle}
.key{font-size:13px;color:var(--tx2);margin:8px 0 0;padding-left:18px}
.key li{margin:3px 0}
code{background:#F1EFE8;padding:1px 6px;border-radius:4px;font-size:12px}
.note{font-size:12px;color:var(--tx2);margin-top:10px}
.warn{background:#FAEEDA;border-color:#EF9F27}
</style></head><body><div class="wrap">

<h1>归一化协议敏感性评测报告</h1>
<div class="sub">同一批模型输出，在 3 种切分方式 × 6 档归一化协议下重算 %d 遍。不改模型，只改批卷标准。</div>

<div class="card"><div class="meta">
<span>样本 <b>%d</b> 条</span><span>模型 <b>%d</b> 个</span><span>组合 <b>%d</b> 组</span>
<span>主口径 <b>%s</b></span><span>繁简后端 <b>%s</b></span><span>数据来源 <b>%s</b></span>
</div>
<div class="note">单调性自检：%s</div></div>

<h2>核心结论</h2>
<div class="card">
<ul class="key">
<li>%d 个模型中有 <b>%d 个</b>的名次在 18 组规则下发生位移。</li>
<li>位移最大的模型是 <b>%s</b>，名次在 <b>%d</b> 到 <b>%d</b> 之间跳动，位移 <b>%d 位</b>。</li>
<li>%s</li>
</ul></div>

<h2>名次稳定度</h2>
<table><thead><tr><th>模型</th><th>最低名次</th><th>最高名次</th><th>名次位移</th><th>名次标准差</th><th>参与组数</th></tr></thead>
<tbody>%s</tbody></table>
<div class="note">位移为 0 的模型，名次可以放心引用；位移 ≥ 2 的模型，任何单一榜单的排名都不足以支撑结论。</div>

<h2>宽容度曲线</h2>
<div class="card">%s
<div class="note">纵轴为对数刻度。P0 最严，P5 最宽。曲线在某档突然下降，说明该模型的主要偏差正好是那一档消除的东西。</div></div>

<h2>名次矩阵（%d 组完整榜单）</h2>
<div style="overflow-x:auto"><table>
<thead><tr><th rowspan="2">模型</th>%s</tr><tr>%s</tr></thead>
<tbody>%s</tbody></table></div>
<div class="note">颜色越深名次越好。横向看同一行，颜色跳变即名次不稳定。</div>

<h2>复现</h2>
<div class="card"><code>pip install opencc-python-reimplemented</code><br>
<code>python verify.py</code> &nbsp;→ 必须输出「全部通过：3 / 3」<br>
<code>python run.py%s</code>
<div class="note">所有随机源固定种子（bootstrap %d）。meta.json 记录了全部影响结果的开关。</div></div>

<div class="card warn"><b>边界声明</b>
<div class="note" style="margin-top:6px">%s
切分方式实现为按上游公开描述的等价重建，尚未与上游源码逐条对齐。本报告不声称构建了评测基准——
基准是上游既有的，本实验量化的是它从未量化的一层。</div></div>

</div></body></html>""" % (
        meta["n_groups"],
        meta["n_samples"], meta["n_models"], meta["n_groups"],
        html.escape(meta["primary_metric"]), html.escape(meta["trad_backend"]),
        html.escape(", ".join(meta["data_sources"])),
        ("通过（固定分母口径，18 组内无违规）" if mono_ok
         else "发现 %d 处违规，需检查协议实现" % len(meta["monotonicity_violations"])),
        len(models), n_shift,
        html.escape(worst["model"]), worst["rank_min"], worst["rank_max"], worst["rank_shift"],
        ("P5 档下 " + "、".join("%s 与 %s" % t for t in tied[:3]) + " 的置信区间重叠——"
         "当前样本量下它们其实分不出胜负。" if tied else
         "各模型在 P5 档下的置信区间互不重叠，名次差异可视为真实差异。"),
        "".join(stab_rows),
        _curve_svg(res["curve"], levels, models),
        meta["n_groups"], head, sub, "".join(body),
        " --demo" if "generated" in meta["data_sources"] else "",
        meta["bootstrap_seed"],
        ("本报告的样本来源为 synthetic（source=generated），只能证明方法可行，"
         "不能证明任何真实工具的水平。" if "generated" in meta["data_sources"]
         else "真实样本已完成脱敏并保留可再分发依据。"),
    )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    # newline="\n"：报告是交付物，需与其它产物一样跨平台逐字节一致
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    return out_path
