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
                     % (x, H - 14, html.escape(lv)))

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
    missing = meta.get("missing_predictions", {})
    missing_note = ""
    if missing:
        details = ["%s：%s" % (html.escape(model),
                   ", ".join(html.escape(sid) for sid in sample_ids))
                   for model, sample_ids in sorted(missing.items())]
        missing_note = ('<div class="card warn"><b>缺失的预测文件</b>'
                        '<div class="note">以下样本按空预测计分，请核对文件准备：%s</div></div>'
                        % "；".join(details))

    by_key = {(r["seg_mode"], r["level"], r["model"]): r for r in ranks}
    groups = [(s, l) for s in seg_modes for l in levels]

    # ---- 结论自动生成 ----
    n_shift = sum(1 for r in res["stability"] if r["rank_shift"] > 0)
    worst = res["stability"][0]
    mono_ok = not meta["monotonicity_violations"]

    # 在实际参与的切分组中描述区间，重叠与否不等同于显著性检验。
    ref_group = meta.get("ref_group", {})
    ci_seg = ref_group.get("seg_mode", seg_modes[0])
    ci_level = ref_group.get("level", levels[0])
    ci_rows = [r for r in res["scores"]
               if r["level"] == ci_level and r["seg_mode"] == ci_seg]
    tied = []
    for i in range(len(ci_rows)):
        for j in range(i + 1, len(ci_rows)):
            a, b = ci_rows[i], ci_rows[j]
            # 区间不可估计时写空值，不能当成分数 0 参与比较。
            if a["ci_hi"] == "" or b["ci_hi"] == "":
                continue
            if not (a["ci_hi"] < b["ci_lo"] or b["ci_hi"] < a["ci_lo"]):
                tied.append((a["model"], b["model"]))
    unavailable = any(r["ci_lo"] == "" or r["ci_hi"] == "" for r in ci_rows)
    if tied:
        ci_summary = "参考组 %s/%s 下模型 %s 的置信区间重叠；重叠本身不等同于显著性检验。" % (
            ci_seg, ci_level, "、".join("与".join(t) for t in tied[:3]))
        if unavailable:
            ci_summary += "部分模型区间不可估计。"
    elif unavailable:
        ci_summary = "参考组 %s/%s 中存在不可估计的置信区间，无法判断所有模型区间是否重叠。" % (ci_seg, ci_level)
    elif len(ci_rows) < 2:
        ci_summary = "参考组 %s/%s 少于两个模型，无区间重叠比较。" % (ci_seg, ci_level)
    else:
        ci_summary = "参考组 %s/%s 下模型置信区间互不重叠；此描述不构成显著性检验。" % (ci_seg, ci_level)

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

    # ---- 名次可信度判定表 ----
    _V_STYLE = {"STABLE": ("#0F6E56", "#FFFFFF"), "FRAGILE": ("#EF9F27", "#3B2200"),
                "TIE": ("#CECBF6", "#26215C")}
    verdict_rows = []
    reasons = {'reference_ci_overlap': '参考组区间重叠',
               'observed_reversal': '已测协议出现逆序',
               'insufficient_margin': '稳定充分条件未满足',
               'sufficient_margin': '满足已测范围内的稳定充分条件'}
    for v in res.get("verdicts", []):
        bg, fg = _V_STYLE.get(v["verdict"], ("#F1EFE8", "#2C2C2A"))
        margin_txt = "∞" if v["margin"] == "inf" else "%.3f" % v["margin"]
        if v["ci_overlap"] == "":
            ov_txt = "不可估计"
        else:
            ov_txt = "重叠" if v["ci_overlap"] else "不重叠"
        if v["paired_diff_lo"] == "":
            pd_txt = "—"
        else:
            pd_txt = "[%+.4f, %+.4f]" % (v["paired_diff_lo"], v["paired_diff_hi"])
        verdict_rows.append(
            "<tr><td>%d</td><td>%s → %s</td><td>%.4f</td><td>%.4f</td>"
            "<td>%s</td><td>%s</td><td>%d</td><td>%s</td>"
            '<td><span class="pill" style="background:%s;color:%s">%s</span></td><td>%s</td></tr>'
            % (v["rank"], html.escape(v["model_a"]), html.escape(v["model_b"]),
               v["cer_a"], v["cer_b"], margin_txt, pd_txt, v["flipped_groups"],
               ov_txt, bg, fg, v["verdict"],
               reasons.get(v.get('verdict_reason'), '历史结果，详见分差与区间')))

    # 逐协议分差由 runner 计算；旧结果不含该字段时仍可正常生成报告。
    pair_details = res.get("pair_protocol_details", [])
    detail_rows = []
    for row in pair_details:
        detail_rows.append(
            "<tr><td>%s → %s</td><td>%s/%s</td><td>%+.8f</td><td>%s</td><td>%s</td></tr>"
            % (html.escape(row["model_a"]), html.escape(row["model_b"]),
               html.escape(row["seg_mode"]), html.escape(row["level"]),
               row["difference_b_minus_a"], html.escape(row["relative_to_reference"]),
               html.escape(row["transition_from_previous_level"])))
    summary_rows = []
    for v in res.get("verdicts", []):
        rows = [r for r in pair_details if r["model_a"] == v["model_a"]
                and r["model_b"] == v["model_b"]]
        if not rows:
            continue
        worst_pair = min(rows, key=lambda r: r["difference_b_minus_a"])
        transitions = sum(r["transition_from_previous_level"] == "换位" for r in rows)
        ties = sum(r["difference_b_minus_a"] == 0 for r in rows)
        summary_rows.append(
            "<tr><td>%s → %s</td><td>%+.8f</td><td>%+.8f（%s/%s）</td>"
            "<td>%d</td><td>%d</td><td>%d</td></tr>"
            % (html.escape(v["model_a"]), html.escape(v["model_b"]),
               v["cer_b"] - v["cer_a"], worst_pair["difference_b_minus_a"],
               html.escape(worst_pair["seg_mode"]), html.escape(worst_pair["level"]),
               v["flipped_groups"], transitions, ties))
    pair_section = ""
    if pair_details:
        pair_section = ("<h2>逐协议模型对比</h2>"
            "<div class=\"note\">分差为 B 的固定分母 CER 减 A 的固定分母 CER，"
            "A、B 按参考组排序，正值表示 A 分数更低。参考组平分时不定义逆序。"
            "最小分差取已测协议中的有符号最小值；多个最小值时列出首个。</div>"
            "<div style=\"overflow-x:auto\"><table><thead><tr><th>模型对 A → B</th>"
            "<th>参考分差</th><th>最小分差及协议</th><th>相对参考逆序组</th>"
            "<th>相邻档位换位</th><th>平分组</th></tr></thead><tbody>%s</tbody></table></div>"
            "<div class=\"note\">相邻档位换位仅统计同一切分方式中连续两档的严格正负变号；"
            "平分或跨切分不算换位。结果仅适用于已测样本和协议。</div>"
            "<details><summary>查看全部协议分差</summary><table><thead><tr>"
            "<th>模型对 A → B</th><th>协议</th><th>B − A</th><th>相对参考组</th>"
            "<th>与前一档比较</th></tr></thead><tbody>%s</tbody></table></details>"
            % ("".join(summary_rows), "".join(detail_rows)))

    # ---- 名次矩阵 ----
    head = "".join('<th colspan="%d">%s</th>' % (len(levels), html.escape(s)) for s in seg_modes)
    sub = "".join("<th>%s</th>" % html.escape(lv) for _ in seg_modes for lv in levels)
    body = []
    for model in models:
        cells = "".join(_rank_cell(by_key[(s, l, model)]["rank"], len(models)) for s, l in groups)
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
.pill{display:inline-block;padding:1px 10px;border-radius:10px;font-size:12px;letter-spacing:.5px}
</style></head><body><div class="wrap">

<h1>归一化协议敏感性评测报告</h1>
<div class="sub">同一批模型输出，在 %d 组协议下重算。切分方式及档位以本次运行参数为准。</div>

<div class="card"><div class="meta">
<span>样本 <b>%d</b> 条</span><span>模型 <b>%d</b> 个</span><span>组合 <b>%d</b> 组</span>
<span>主口径 <b>%s</b></span><span>繁简后端 <b>%s</b></span><span>数据来源 <b>%s</b></span>
</div>
<div class="note">单调性自检：%s</div></div>
%s

<h2>核心结论</h2>
<div class="card">
<ul class="key">
<li>%d 个模型中有 <b>%d 个</b>的名次在 %d 组规则下发生位移。</li>
<li>位移最大的模型是 <b>%s</b>，名次在 <b>%d</b> 到 <b>%d</b> 之间跳动，位移 <b>%d 位</b>。</li>
<li>%s</li>
</ul></div>

<h2>名次稳定度</h2>
<table><thead><tr><th>模型</th><th>最低名次</th><th>最高名次</th><th>名次位移</th><th>名次标准差</th><th>参与组数</th></tr></thead>
<tbody>%s</tbody></table>
<div class="note">位移为 0 的模型在本次样本和参与协议范围内未发生名次变化；位移 ≥ 2 的模型对协议选择较敏感，单一榜单不足以概括其稳定性。</div>

<h2>名次可信度判定</h2>
<table><thead><tr><th>名次对</th><th>相邻模型</th><th>参考分 A</th><th>参考分 B</th>
<th>margin</th><th>配对差值区间</th><th>翻转组数</th><th>区间重叠</th><th>判定</th><th>判定依据</th></tr></thead>
<tbody>%s</tbody></table>
<div class="note">margin = 名次差距 ÷ 两个模型在<b>本次参与组内</b>的分数极差之和。margin ≥ 1 仅在已采样且包含参考组的协议组内保证不严格翻转（等号可追平），不能外推到未采样协议；margin &lt; 1 只说明「不翻转」的充分条件不成立，<b>不等于已经翻转</b>，是否翻转以「翻转组数」为准。配对差值区间为诊断量，不参与判定；判定仍按参考组各自区间是否重叠（重叠即 TIE，是规则标签而非正式显著性检验）。</div>

%s

<h2>宽容度曲线</h2>
<div class="card">%s
<div class="note">纵轴为对数刻度。P0 最严，P5 最宽。曲线在某档出现明显变化，说明该模型对该协议规则较敏感；这只是协议敏感性线索，不能单独归因于某类主要偏差。</div></div>

<h2>名次矩阵（%d 组完整榜单）</h2>
<div style="overflow-x:auto"><table>
<thead><tr><th rowspan="2">模型</th>%s</tr><tr>%s</tr></thead>
<tbody>%s</tbody></table></div>
<div class="note">颜色越深名次越好。横向看同一行，颜色跳变即名次不稳定。</div>

<h2>复现</h2>
<div class="card"><code>python run.py --gt &lt;GT目录&gt; --models &lt;模型输出目录&gt;%s --out &lt;输出目录&gt; --boot %d --ref-seg %s --ref-level %s --seg-modes %s --seed %d --max-edit-cells %d%s</code>
<div class="note">bootstrap 次数为 %d，随机种子为 %d；参考组与数据来源均记录在 meta.json。</div></div>

<div class="card warn"><b>边界声明</b>
<div class="note" style="margin-top:6px">%s
切分方式按上游公开描述重建（reconstructed），尚未与上游源码逐条对齐，不能声称等价。
本报告不声称构建了评测基准；本实验仅在本次数据与参与配置上量化评分规则的敏感性，不据此声称上游或其他研究从未进行类似量化。</div></div>

</div></body></html>""" % (
        meta["n_groups"],
        meta["n_samples"], meta["n_models"], meta["n_groups"],
        html.escape(meta["primary_metric"]), html.escape(meta["trad_backend"]),
        html.escape(", ".join(meta["data_sources"])),
        ("通过（固定分母口径，%d 组内无违规）" % meta["n_groups"] if mono_ok
         else ("发现 %d 处违规：固定分母只消除分母变化；繁简 1:1 映射与按上下文删除 "
               "CJK 间空格仍会改变对齐，故不保证单调。此处如实记录，不属于实现缺陷。"
               % len(meta["monotonicity_violations"]))),
        missing_note,
        len(models), n_shift, meta["n_groups"],
        html.escape(worst["model"]), worst["rank_min"], worst["rank_max"], worst["rank_shift"],
        html.escape(ci_summary),

        "".join(stab_rows),
        "".join(verdict_rows),
        pair_section,
        _curve_svg(res["curve"], levels, models),
        meta["n_groups"], head, sub, "".join(body),
        (" --select-models " + html.escape(",".join(meta["model_selection"]))
         if meta.get("model_selection") else ""),
        meta["bootstrap_n"], html.escape(ref_group.get("seg_mode", seg_modes[0])),
        html.escape(ref_group.get("level", levels[0])),
        html.escape(",".join(seg_modes)), meta['bootstrap_seed'],
        meta.get('long_text_fallback', {}).get('trigger_threshold_product', 4_000_000),
        (' --cluster-field ' + html.escape("'" + meta['bootstrap_cluster_field'].replace("'", "''") + "'")
         if meta.get('bootstrap_cluster_field') else ''),
        meta["bootstrap_n"], meta["bootstrap_seed"],
        ("样本来源标记为 %s；本报告不据此推断脱敏状态或再分发许可。" %
         html.escape(", ".join(meta["data_sources"]))),
    )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    # newline="\n"：报告是交付物，需与其它产物一样跨平台逐字节一致
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    return out_path
