# -*- coding: utf-8 -*-
"""生成探针集 HTML 页面 —— 按主题定向注入协议敏感特征。

    python tools/make_probe_pages.py --out <目录> [--per-theme 12] [--seed 20260923]

**为什么要造而不是全靠真实截图**

真实截图的特征覆盖靠碰运气。实测 32 条真实截图里 `fullwidth_digit` 命中
**0/32** —— 因为中文界面几乎不用全角数字。而 P3「宽度宽容」这一档
正是靠全角数字才能触发出位移的：没有这类样本，那一档换规则也不会变分，
**样本就白跑了**。

生成样本的价值不是"更多"，而是**定向**：

| 主题 | 定向补的档位 | 为什么真实截图补不上 |
|---|---|---|
| `p3_fullwidth` | P3 宽度宽容 | 中文界面不用全角数字（日文才是常态） |
| `p5_trad` | P5 字形宽容 | 繁体内容在简体语境里出现频率低 |
| `p1_space` | P1 空白折叠 | 缩进/连续空格多在代码与终端里 |
| `p4_punct` | P4 标点宽容 | 需要一个句子里同时出现全角与半角标点 |
| `mix_cjk_latin` | P1/P2 | 需要密集中英混排与大小写变体 |
| `table` / `code` / `emoji_sym` | 结构类 | 短文本难凑 |
| `baseline` | **对照组** | 全是规范简体 —— 预期在任何档位下分数都不变 |

最后一行很重要：如果没有 `baseline` 组，就无法区分
"换了协议分数不变" 与 "样本本身就没覆盖该特征"。

**真值的来源**：与图片同源生成（`render_probe.py` 取 `.page` 的 `inner_text`），
零人工标注、零识别误差。因此本工具**不需要**也不允许人工修改产物。

**纪律**：本工具只造"文本长什么样"，**不做任何字符归一**。
全角/半角/繁简是六档协议的职责，生成阶段就把字符定死，协议阶段才允许动它。

依赖：无（纯字符串拼接）；渲染见 tools/render_probe.py。
"""

from __future__ import annotations

import argparse
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src.protocol import PUNCT_MAP, TRAD_TO_SIMP  # noqa: E402

# 每个探针页面都带一个唯一编号。作用有二：
#   1. 让各页文本互不相同，避免样本之间高度相关（bootstrap 会低估方差）
#   2. 编号是"针"——OCR 若漏掉或改写它，会直接体现在编辑距离上
#
# ⚠️ 编号**只允许用半角数字**，不得带拉丁字母。
# 首版用了 "DD-" 前缀，结果审计显示 `cjk_latin_mix` 命中 108/108 ——
# 因为每页都被硬塞进了拉丁字符，该特征因此失去区分力，
# 而且污染了 baseline 对照组（对照组本应只有规范简体）。
# 审计能抓住这个缺陷，正是它存在的意义。
ID_FMT = "2026-%04d"

# ---------------------------------------------------------------------------
# 内容池
# ---------------------------------------------------------------------------

SIMPLE = [
    "本节说明配置项的取值范围与默认行为，未列出的参数请沿用上一个稳定版本。",
    "若在导入过程中遇到字段缺失，请先核对表头顺序是否与模板一致。",
    "任务执行完毕后会自动生成一份摘要，可在历史记录中随时回看。",
    "该功能仅在网络连通时可用，离线状态下入口会被置灰。",
    "为避免重复计费，同一账号在二十四小时内只允许提交一次。",
    "系统会根据近期使用情况自动调整推荐顺序，排序结果不影响实际数据。",
    "建议在低峰时段执行批量操作，单次提交的记录数不要超过两千条。",
    "界面语言可在设置页切换，切换后需要重新登录一次方才生效。",
    "导出文件采用通用格式，用任意表格软件都能直接打开。",
    "如对结果有疑问，可携带记录编号联系值班人员核对。",
    "统计口径按月汇总，跨月的数据不会计入当期报表。",
    "本说明适用于当前版本，后续若有调整会另行通知。",
]

# 全角数字 / 全角字母 / 全角标点 —— 定向打 P3
FULLWIDTH = [
    "会议编号：第３回　情報システム整備検討会（２０２６年９月１８日）",
    "费用明细：住民票の写し　３００円／戸籍謄本　４５０円／合計１，５００円",
    "联系窗口：代表电话０３（５２５３）１１１１　内线７７４２",
    "支持浏览器：Ｍｉｃｒｏｓｏｆｔ　Ｅｄｇｅ（Ｖｅｒ．１２４以降）、Ｓａｆａｒｉ",
    "运行环境：Ｗｉｎｄｏｗｓ　１１、ｍａｃＯＳ　１４、ｉＯＳ　１７",
    "维护窗口：令和８年９月２７日　午前２：００～午前５：００",
    "受理期限：９月２４日（木）１７：００までにお申し込み下さい。",
    "手续费减免的咨询电话：☎０３－１２３４－５６７８（工作日のみ）",
    "编号区间：Ｎｏ．０１２０～０３４５　共计２２６件",
    "速率上限：１，２００件／小时，超出部分顺延至次日处理。",
]

# 繁体 —— 定向打 P5。注意这些字与简体不同形，故能触发 t2s 档
TRAD = [
    "本篇文章說明檔案格式的轉換流程，請依照步驟逐步操作。",
    "若顯示異常，請先確認瀏覽器版本並清除暫存資料。",
    "該項設定僅適用於繁體中文介面，其他語系請參考對應說明。",
    "資料匯出後會自動壓縮，解壓縮時請使用支援的軟體。",
    "關於帳號權限的調整，需由管理員於後台審核後方能生效。",
    "系統將於每日凌晨執行備份，期間可能短暫無法連線。",
    "此功能仍在測試階段，若有問題歡迎回報。",
    "請注意：逾時未處理的申請將自動取消，不再另行通知。",
]

# 缩进 / 连续空格 / 中文间空格 —— 定向打 P1
SPACED = [
    "　　　　（首行缩进四格）这一段用于测试行首空白的处理方式。",
    "字段对照：姓名      学号      班级      成绩",
    "　　一级标题\n　　　　二级条目\n　　　　　　三级细则",
    "路径：　D:\\　docs　\\　report　\\　final　.docx",
    "备注：               本行左右各留若干空格               以对齐。",
    "（中文 之间 插入 空格）用于测试中文字间空格的折叠规则。",
]

# 标点混用 —— 定向打 P4
PUNCT = [
    "注意事项：请勿在高温,潮湿环境下使用(包括浴室)。",
    "支持格式:PDF, DOCX, XLSX;单文件不超过 50MB。",
    "请回答“是”或“否”,不要留空。",
    "步骤一、打开设置；步骤二,选择语言;步骤三、确认。",
    "清单（草案）：\n(1) 收集材料\n（2）核对字段\n(3) 提交审核",
]

# 中英混排 + 大小写 —— 定向打 P1/P2
LATIN = [
    "打开 WiFi 设置，确认已连接 WiFi 网络（不是 WIFI）。",
    "App 名称：SmartNote　版本 v3.2.1　兼容 iOS 与 ANDROID。",
    "接口返回 JSON 格式，字段 userId、userName 均为必填项。",
    "使用 Python 调用 API，注意 timeout 默认是 30 秒。",
    "缓存命中率 HIT RATE 与 QPS 呈正相关，详见 Dashboard。",
    "在 SQL 中执行 EXPLAIN 可以看到执行计划。",
]

EMOJI = [
    "✅ 已完成　⏳ 处理中　❌ 已取消　⭐ 重点关注",
    "提示：点击右上角 ⚙ 进入设置，按 ▶ 开始播放。",
    "评分：★★★★☆　（共 5 星，当前 4 星）",
    "→ 继续　← 返回　↑ 上传　↓ 下载",
]

CODE = [
    "def parse(text):\n    if not text:\n        return []\n    return [x for x in text.split() if x]",
    "SELECT userId, COUNT(*) AS n\nFROM events\nWHERE dt = '2026-09-23'\nGROUP BY userId\nHAVING n > 100;",
    "for i, row in enumerate(rows):\n        if row[\"score\"] > 0.8:\n            keep.append(i)",
]

TABLES = [
    ["项目", "数量", "单价", "小计"],
    ["打印费", "１２", "０．５０", "６．００"],
    ["装订费", "3", "2.00", "6.00"],
    ["快递费", "1", "12.00", "12.00"],
]


def _page_body(theme: str, idx: int, rng: random.Random) -> list:
    """按主题返回页面正文块列表。每块是 (kind, payload)。"""
    uid = ID_FMT % idx
    blocks = [("p", "编号：%s" % uid)]

    if theme == "baseline":
        blocks += [("p", t) for t in rng.sample(SIMPLE, 5)]
    elif theme == "p3_fullwidth":
        blocks += [("p", t) for t in rng.sample(SIMPLE, 3)]
        blocks += [("p", t) for t in rng.sample(FULLWIDTH, 3)]
    elif theme == "p5_trad":
        blocks += [("p", t) for t in rng.sample(TRAD, 4)]
        blocks += [("p", t) for t in rng.sample(SIMPLE, 2)]
    elif theme == "p1_space":
        blocks += [("pre", t) for t in rng.sample(SPACED, 3)]
        blocks += [("p", t) for t in rng.sample(SIMPLE, 3)]
    elif theme == "p4_punct":
        blocks += [("p", t) for t in rng.sample(PUNCT, 3)]
        blocks += [("p", t) for t in rng.sample(SIMPLE, 3)]
    elif theme == "mix_cjk_latin":
        blocks += [("p", t) for t in rng.sample(LATIN, 4)]
        blocks += [("p", t) for t in rng.sample(SIMPLE, 2)]
    elif theme == "emoji_sym":
        blocks += [("p", t) for t in rng.sample(EMOJI, 3)]
        blocks += [("p", t) for t in rng.sample(SIMPLE, 3)]
    elif theme == "code":
        blocks += [("p", t) for t in rng.sample(SIMPLE, 2)]
        blocks += [("code", "".join(rng.sample(CODE, 2)).replace("", ""))]
    elif theme == "table":
        blocks += [("p", rng.choice(SIMPLE))]
        blocks += [("table", None)]
    else:
        raise ValueError("未知主题：%s" % theme)

    # 主题之间做轻度混合，避免"某一档只由单一来源触发"这种脆弱结论
    if theme != "baseline" and rng.random() < 0.4:
        blocks.append(("p", rng.choice(SIMPLE)))
    return blocks


CSS = """
*{box-sizing:border-box}
body{margin:0;background:#efefec;padding:28px 0;
     font-family:"Microsoft YaHei","Meiryo",system-ui,sans-serif;color:#1a1a1a}
.page{width:820px;margin:0 auto 28px;background:#fff;padding:40px 48px 44px;
      box-shadow:0 1px 3px rgba(0,0,0,.14)}
.tag{display:inline-block;font-size:12px;letter-spacing:.06em;color:#fff;
     background:#2f5d8a;padding:3px 10px;border-radius:2px;margin-bottom:16px}
h1{font-size:21px;margin:0 0 18px;font-weight:700}
p{font-size:15px;line-height:2.0;margin:0 0 12px;color:#333}
pre{font-family:Consolas,"Courier New",monospace;font-size:13px;line-height:1.8;
    background:#f7f7f5;border:1px solid #e2e2e0;border-radius:3px;
    padding:12px 14px;margin:0 0 14px;white-space:pre;overflow:visible;color:#222}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 4px}
th,td{border:1px solid #d8d8d8;padding:8px 12px;text-align:left;line-height:1.7}
th{background:#f7f7f5;font-weight:600;white-space:nowrap}
"""

# 页面角标用的中文标签。
# ⚠️ **不得使用内部主题 id**（如 `probe_baseline`）——首版就是这么干的，
# 结果主题名里的拉丁字母被渲染进每张图、进而进入真值，
# 使 `cjk_latin_mix` 命中 108/108，该特征彻底失去区分力，
# baseline 对照组也不再"只有规范简体"。
# 内部 id 只应出现在文件名里，不应出现在**样本内容**里。
LABELS = {
    "baseline": "使用说明",
    "p3_fullwidth": "公告与费率",
    "p5_trad": "系統說明",
    "p1_space": "字段对照",
    "p4_punct": "注意事项",          # 简体：p4_punct 只应测标点，不得混入繁体
    "mix_cjk_latin": "接口说明",
    "emoji_sym": "状态一览",
    "code": "示例代码",
    "table": "费用结算单",
}

TITLES = {
    "baseline": "使用说明（对照）",
    "p3_fullwidth": "公告与费率明细",
    "p5_trad": "系統操作說明",
    "p1_space": "字段对照与目录结构",
    "p4_punct": "注意事项与清单",
    "mix_cjk_latin": "接口与客户端说明",
    "emoji_sym": "状态与评分一览",
    "code": "示例代码与查询",
    "table": "费用结算单",
}


def build_html(theme: str, per_theme: int, seed: int) -> str:
    rng = random.Random(seed)
    parts = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
             "<title>探针集 · %s</title><style>%s</style></head><body>"
             % (theme, CSS)]
    for i in range(1, per_theme + 1):
        blocks = _page_body(theme, i, rng)
        body = []
        for kind, payload in blocks:
            if kind == "p":
                body.append("<p>%s</p>" % payload.replace("\n", "<br>"))
            elif kind == "pre":
                body.append("<pre>%s</pre>" % payload)
            elif kind == "code":
                body.append("<pre>%s</pre>" % payload)
            elif kind == "table":
                rows = TABLES
                head = "".join("<th>%s</th>" % c for c in rows[0])
                body.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody>"
                            "</table>" % (head, "".join(
                                "<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r)
                                for r in rows[1:])))
        parts.append('<div class="page"><span class="tag">%s · %02d</span>'
                     "<h1>%s</h1>%s</div>"
                     % (LABELS[theme], i, TITLES[theme], "".join(body)))
    parts.append("</body></html>")
    return "".join(parts)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description="生成探针集 HTML 页面")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--per-theme", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20260923)
    ap.add_argument("--only", default=None, help="只生成指定主题，逗号分隔")
    args = ap.parse_args()

    themes = list(TITLES)
    if args.only:
        want = {s.strip() for s in args.only.split(",") if s.strip()}
        themes = [t for t in themes if t in want]
        missing = want - set(themes)
        if missing:
            print("未知主题：%s" % sorted(missing), file=sys.stderr)
            return 1

    os.makedirs(args.out, exist_ok=True)
    total = 0
    for i, theme in enumerate(themes):
        html = build_html(theme, args.per_theme, args.seed + i * 1000)
        p = os.path.join(args.out, "probe_%s.html" % theme)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        total += args.per_theme
        print("[%s] %d 页 -> %s" % (theme, args.per_theme, os.path.basename(p)))

    print("-" * 60)
    print("共 %d 页，覆盖 %d 个主题（种子 %d，可复现）"
          % (total, len(themes), args.seed))
    print("复用到全角标点 %d 种、繁简对照 %d 对（判据与 protocol.py 同源）"
          % (len(PUNCT_MAP), len(TRAD_TO_SIMP)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
