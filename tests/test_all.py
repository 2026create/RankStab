# -*- coding: utf-8 -*-
"""端到端自检。直接运行： python tests/test_all.py

覆盖四类断言：
    A 归一化协议逐档行为
    B 指标正确性（S/D/I 分解、CER 分母、bootstrap 可复现）
    C 切分/匹配三种模式
    D 18 组重算端到端 + 核心命题在受控数据上成立

这些断言**确实抓到过真问题**（首次运行 56/60），保留在案：
  1. 手写繁简表把「体體」写成简体在前，导致 P5 把简体转成了繁体 —— 已改为 OpenCC + 同源快照
  2. 「宽容度越高错误率越低」在标准口径下不成立，因为折叠空白会缩短分母 ——
     这不是实现 bug，而是方法论问题，已通过固定分母口径（cer_fixed）解决，见 metrics.py
  3. 测试用例本身写错了两处（P5 含 casefold、切分敏感性用了不敏感的模型）
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src import make_demo, make_report, metrics, protocol, runner, segmatch  # noqa: E402

_PASS = 0
_FAIL = []


def eq(name, got, want):
    global _PASS
    if got == want:
        _PASS += 1
    else:
        _FAIL.append("%s\n      got  = %r\n      want = %r" % (name, got, want))


def ok(name, cond, detail=""):
    global _PASS
    if cond:
        _PASS += 1
    else:
        _FAIL.append("%s%s" % (name, ("  -> " + str(detail)) if detail else ""))


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


# ---------------------------------------------------------------- A 协议
def test_protocol():
    n = protocol.normalize

    eq("A1 P0 只去首尾空白", n("  甲  乙  ", "P0"), "甲  乙")
    eq("A2 P1 折叠连续空白", n("  甲  乙  ", "P1"), "甲 乙")
    eq("A3 P1 折叠换行", n("甲\n\n\n乙", "P1"), "甲 乙")
    eq("A4 P2 大小写不敏感", n("DeepSeek-OCR", "P2"), "deepseek-ocr")
    eq("A5 P3 全角字母转半角", n("ＡＢ", "P3"), "ab")
    eq("A6 P3 全角数字转半角", n("１２３", "P3"), "123")
    eq("A7 P3 全角空格", n("甲\u3000乙", "P3"), "甲 乙")
    eq("A8 P4 中文逗号归一", n("甲，乙", "P4"), "甲,乙")
    eq("A9 P4 中文句号归一", n("甲。乙", "P4"), "甲.乙")
    eq("A10 P4 引号归一", n("\u201c甲\u201d", "P4"), '"甲"')
    eq("A11 P4 省略号归一（1:1，不得展开）", n("甲\u2026乙", "P4"), "甲.乙")
    eq("A12 P5 繁体转简体", n("體檢報告與問題", "P5"), "体检报告与问题")
    eq("A13 P5 删除 CJK 间空格", n("甲 乙 丙", "P5"), "甲乙丙")
    eq("A14 P5 只删 CJK 间空格，中英之间保留", n("甲 1 乙", "P5"), "甲 1 乙")
    eq("A15 P0 不动全角", n("Ａ１", "P0"), "Ａ１")
    eq("A16 未知档位应报错", _raises(lambda: n("x", "P9")), True)
    eq("A17 P4 包含 P3 全角转换", n("Ａ，Ｂ", "P4"), "a,b")

    # 繁简后端必须被记录（报告里要照实声明用了哪个）
    # A18 已被 A18b 取代：TRAD_BACKEND 现在必须恒定，不再允许随环境取值。
    ok("A19 繁简表规模合理", len(protocol.TRAD_TO_SIMP) > 3000,
       len(protocol.TRAD_TO_SIMP))

    # --- 可复现性防线：繁简后端不得随环境变化 -------------------------------
    # 事故背景（2026-09-21）：曾经的实现是「装了 opencc 就用它」，
    # 导致同一份代码在两台机器上产出不同字节，且不报错，只是静静改变产物。
    # 对一个主张可复现的项目这是致命缺陷，故在此用断言永久锁死。
    eq("A18b 繁简后端恒定不随环境变化", protocol.TRAD_BACKEND, "snapshot_table")
    eq("A18c 快照被标记为已冻结", protocol.TRAD_TO_SIMP_COVERAGE.get("frozen"), True)

    # 无论本机是否装有 opencc，P5 的转换结果必须完全一致。
    # 这条断言在「装有 opencc」和「未装 opencc」两种环境下都必须通过，
    # 只要它通过，就证明环境差异无法渗入评分路径。
    # 注意：「路」「算」在繁简两侧同形，无映射项应原样保留 —— 这正是逐个字符
    # 映射的正确行为，不要"顺手"把它们也映射掉。
    eq("A18d P5 转换结果与环境无关(一)",
       n("\u8a08\u7b97\u6a5f\u8207\u7db2\u8def", "P5"), "\u8ba1\u7b97\u673a\u4e0e\u7f51\u8def")
    eq("A18e P5 转换结果与环境无关(二)",
       n("\u9ede\u7dda\u8207\u5716\u8868", "P5"), "\u70b9\u7ebf\u4e0e\u56fe\u8868")

    # 若本机恰好装有 opencc，则顺带交叉校验快照与它一致（不一致说明快照有问题）。
    # 未安装时该断言自动跳过，不构成对环境的依赖。
    _cc, _ccver = protocol._load_opencc_for_crosscheck()
    if _cc is not None:
        _probe = "\u8a08\u7b97\u6a5f\u8207\u7db2\u8def\u7684\u7bc0\u9ede"
        _snap = "".join(protocol.TRAD_TO_SIMP.get(c, c) for c in _probe)
        eq(f"A18f 快照与 OpenCC({_ccver}) 交叉校验一致", _cc.convert(_probe), _snap)
    else:
        ok("A18f 本机无 opencc，跳过交叉校验（不影响结果一致性）", True, "skipped")

    # 长度型规则必须被单独标出（单调性陷阱的来源）
    ok("A20 长度型规则可识别", "collapse_ws" in protocol.length_affecting_rules("P1"),
       protocol.length_affecting_rules("P1"))
    # P3 仍继承 P1 的 collapse_ws —— 这正是「高档位也可能因分母缩短而抬高 CER」的根源，
    # 因此长度型规则必须在每一档都被显式列出，而不是只在首次引入的那一档。
    eq("A21 P3 仍继承长度型规则", protocol.length_affecting_rules("P3"), ["collapse_ws"])
    eq("A22 P0 无长度型规则", protocol.length_affecting_rules("P0"), [])
    ok("A23 P5 两个长度型规则都在",
       set(protocol.length_affecting_rules("P5")) == {"collapse_ws", "drop_cjk_space"},
       protocol.length_affecting_rules("P5"))

    # 档位可比性的前提：映射型规则不改长度，长度型规则被显式登记。
    # 一旦有人往 PUNCT_MAP 里塞入会改变长度的映射，这两条会立刻报警。
    bad = {k: v for k, v in protocol.PUNCT_MAP.items() if len(k) != len(v)}
    ok("A26 标点表全部 1:1 映射", protocol.punct_map_is_length_preserving(), bad)
    eq("A27 标点归一如实登记为映射型", protocol.RULE_KIND["punct_unify"], "mapping")

    total_kinds = {r for r in protocol.RULE_KIND.values()}
    eq("A28 规则类型只有三种", total_kinds, {"mapping", "length", "boundary"})

    # 单调性：映射型规则逐档累加后，编辑距离必须单调不增
    ref = "实验结果表１显示，模型的准确率为92.5％。"
    hyp = "實驗結果表1顯示, 模型的準確率為 92.5%."
    dists = [metrics.edit_ops(protocol.normalize(ref, l), protocol.normalize(hyp, l))["dist"]
             for l in protocol.LEVELS]
    ok("A24 六档编辑距离单调不增", all(b <= a for a, b in zip(dists, dists[1:])),
       "dists=%s" % dists)
    ok("A25 P5 显著优于 P0", dists[-1] < dists[0], "P0=%d P5=%d" % (dists[0], dists[-1]))


# ---------------------------------------------------------------- B 指标
def test_metrics():
    e = metrics.edit_ops
    eq("B1 完全相同", e("abc", "abc")["dist"], 0)
    eq("B2 单字符替换 S=1", (e("abc", "axc")["S"], e("abc", "axc")["dist"]), (1, 1))
    eq("B3 缺字 D=1", (e("abc", "ac")["D"], e("abc", "ac")["dist"]), (1, 1))
    eq("B4 多字 I=1", (e("abc", "abXc")["I"], e("abc", "abXc")["dist"]), (1, 1))
    eq("B5 中文漏字", e("中文测试", "中文测")["D"], 1)
    eq("B6 空 vs 空", e("", "")["dist"], 0)

    c = metrics.char_error_rate
    eq("B7 CER 分母为参考长度", round(c("abcd", "ab")["cer"], 6), 0.5)
    eq("B8 CER 完全正确为 0", c("中文", "中文")["cer"], 0.0)
    ok("B9 多字计入 I", c("ab", "abcd")["ins_rate"] > 0, True)

    # 固定分母：分母由外部给定，与归一化后长度无关
    r = c("abcd", "ab", fixed_denom=8)
    eq("B10 固定分母生效", round(r["cer_fixed"], 6), round(2 / 8, 6))
    eq("B11 标准分母不受影响", round(r["cer"], 6), 0.5)

    r1 = [{"cer": 0.1, "cer_fixed": 0.1, "ref_len": 100, "fixed_denom": 100,
           "sub_rate": 0.1, "del_rate": 0.0, "ins_rate": 0.0},
          {"cer": 0.2, "cer_fixed": 0.2, "ref_len": 100, "fixed_denom": 100,
           "sub_rate": 0.2, "del_rate": 0.0, "ins_rate": 0.0}]
    eq("B12 语料级为微平均", round(metrics.corpus_cer(r1)["cer_fixed"], 6), 0.15)

    r2 = [{"cer": 0.0, "cer_fixed": 0.0, "ref_len": 990, "fixed_denom": 990,
           "sub_rate": 0.0, "del_rate": 0.0, "ins_rate": 0.0},
          {"cer": 1.0, "cer_fixed": 1.0, "ref_len": 10, "fixed_denom": 10,
           "sub_rate": 1.0, "del_rate": 0.0, "ins_rate": 0.0}]
    micro = metrics.corpus_cer(r2)["cer_fixed"]
    macro = sum(x["cer"] for x in r2) / 2
    ok("B13 微平均与算术平均不同", abs(micro - macro) > 0.1,
       "micro=%.4f macro=%.4f" % (micro, macro))
    eq("B14 微平均结果正确", round(micro, 6), 0.01)

    rows = [{"cer": i / 100.0, "cer_fixed": i / 100.0, "ref_len": 100,
             "fixed_denom": 100, "sub_rate": i / 100.0, "del_rate": 0.0, "ins_rate": 0.0}
            for i in range(20)]
    a1 = metrics.bootstrap_ci(rows, n_boot=300)
    a2 = metrics.bootstrap_ci(rows, n_boot=300)
    eq("B15 bootstrap 同种子两次一致", a1, a2)
    ok("B16 区间包含点估计", a1[0] <= metrics.corpus_cer(rows)["cer_fixed"] <= a1[1],
       "ci=%s" % (a1,))


# ---------------------------------------------------------------- C 切分
def test_segmatch():
    s = segmatch.split_blocks
    eq("C1 whole 单块", len(s("甲\n\n乙\n\n丙", "whole")), 1)
    eq("C2 para 按空行切", s("甲\n\n乙\n\n丙", "para"), ["甲", "乙", "丙"])
    eq("C3 空文本返回空", s("   ", "para"), [])

    txt = "今天天气很好\n\n我们去公园玩。\n\n公园里人很多\n\n大家都很开心。"
    eq("C4 para 不合并", len(s(txt, "para")), 4)
    eq("C5 para_merge 合并被截断段", len(s(txt, "para_merge")), 2)

    head = "第一段结论文本\n\n## 二、分析\n\n第二段内容"
    ok("C6 结构标记不被误合并", len(s(head, "para_merge")) == 3, s(head, "para_merge"))

    pairs, rl, hl = segmatch.align(["a", "b", "c"], ["x"])
    eq("C7 顺序一对一", len(pairs), 1)
    eq("C8 多余 ref 块判为漏", len(rl), 2)
    eq("C9 多余 hyp 块判为多", len(hl), 0)

    ok("C10 三种切分方式齐全", segmatch.SEG_MODES == ["whole", "para", "para_merge"],
       segmatch.SEG_MODES)


# ---------------------------------------------------------------- D 端到端
def test_end_to_end():
    tmp = tempfile.mkdtemp(prefix="rankstab_")
    try:
        gt = os.path.join(tmp, "gt")
        mo = os.path.join(tmp, "models")
        out = os.path.join(tmp, "out")

        info = make_demo.generate(gt, mo, n_samples=10)
        eq("D1 生成 5 个模型", len(info["models"]), 5)

        res = runner.run(gt, mo, out, n_boot=200)
        meta = res["meta"]

        eq("D2 样本数", meta["n_samples"], 10)
        eq("D3 组合数 3x6", meta["n_groups"], 18)
        eq("D4 scores 行数", len(res["scores"]), 5 * 18)
        eq("D5 名次矩阵行数", len(res["ranks"]), 5 * 18)
        eq("D6 稳定度行数", len(res["stability"]), 5)
        eq("D7 主口径为固定分母", meta["primary_metric"], "cer_fixed")

        ok("D8 无单调性违规（固定分母口径）", not meta["monotonicity_violations"],
           meta["monotonicity_violations"][:2])
        ok("D9 切分实现标注重建状态", "reconstructed" in meta["segmode_impl"])
        # D10 用于断言 meta 中的后端取值合法；现收紧为「必须恒为快照」。
        # 这样一旦有人把环境探测逻辑改回来，D10b 会立刻失败。
        ok("D10 繁简后端写入 meta", meta["trad_backend"] in ("opencc", "snapshot_table"),
           meta["trad_backend"])
        eq("D10b meta 中的繁简后端恒为快照", meta["trad_backend"], "snapshot_table")

        for fn in ("scores.csv", "rank_matrix.csv", "stability.csv",
                   "protocol_curve.csv", "meta.json"):
            ok("D11 产出 %s" % fn, os.path.exists(os.path.join(out, fn)))

        def cer_of(model, seg, lvl, key="cer_fixed"):
            for r in res["scores"]:
                if r["model"] == model and r["seg_mode"] == seg and r["level"] == lvl:
                    return r[key]
            raise KeyError((model, seg, lvl))

        # ---- 核心命题一：协议敏感性必须可测出 ----
        fw_p0 = cer_of("model_fullwidth", "para", "P0")
        fw_p3 = cer_of("model_fullwidth", "para", "P3")
        ok("D12 全角风格：P0 远差于 P3", fw_p0 > fw_p3 * 3,
           "P0=%.4f P3=%.4f" % (fw_p0, fw_p3))

        tr_p0 = cer_of("model_traditional", "para", "P0")
        tr_p5 = cer_of("model_traditional", "para", "P5")
        ok("D13 繁体风格：P0 差于 P5", tr_p0 > tr_p5,
           "P0=%.4f P5=%.4f" % (tr_p0, tr_p5))

        # ---- 核心命题二：名次漂移必须真实发生 ----
        shifts = {r["model"]: r["rank_shift"] for r in res["stability"]}
        ok("D14 存在名次位移", max(shifts.values()) >= 1, shifts)

        clean = next(r for r in res["stability"] if r["model"] == "model_clean")
        ok("D15 model_clean 名次不稳（会被反超）", clean["rank_shift"] >= 1,
           "rank %d..%d" % (clean["rank_min"], clean["rank_max"]))

        # ---- 核心命题三：切分方式也是有效自变量 ----
        fl_w = cer_of("model_flatten", "whole", "P0")
        fl_p = cer_of("model_flatten", "para", "P0")
        ok("D16 段落结构崩坏时切分方式显著影响分数", fl_p > fl_w * 3,
           "whole=%.4f para=%.4f" % (fl_w, fl_p))

        # ---- 核心命题四：双口径必须真的不同，否则方法论讨论是空话 ----
        diff = [(r["level"], r["cer"], r["cer_fixed"]) for r in res["scores"]
                if r["model"] == "model_clean" and r["seg_mode"] == "para"]
        ok("D17 标准口径与固定分母口径确实分叉",
           any(abs(a - b) > 1e-9 for _, a, b in diff),
           diff[:3])

        # ---- 输出可被第三方解析（不是只写不读）----
        with open(os.path.join(out, "meta.json"), encoding="utf-8") as f:
            reloaded = json.load(f)
        eq("D18 meta.json 可回读", reloaded["n_groups"], 18)

        curve = res["curve"]
        eq("D19 宽容度曲线行数", len(curve), 5)
        ok("D20 曲线含 P0-P5 六列", all(k in curve[0] for k in protocol.LEVELS),
           sorted(curve[0].keys()))

        # ---- 可视化报告：结构配平 + 无未替换占位符 ----
        rp = os.path.join(out, "report.html")
        make_report.build(res, rp)
        ok("D21 报告已生成", os.path.exists(rp) and os.path.getsize(rp) > 2000)
        with open(rp, encoding="utf-8") as f:
            h = f.read()
        ok("D22 报告标签配平",
           h.count("<html") == 1 and h.count("</html>") == 1
           and h.count("<body") == 1 and h.count("</body>") == 1
           and h.count("<table") == h.count("</table>")
           and h.count("<svg") == h.count("</svg>"),
           "html=%d/%d table=%d/%d svg=%d/%d"
           % (h.count("<html"), h.count("</html>"),
              h.count("<table"), h.count("</table>"),
              h.count("<svg"), h.count("</svg>")))
        leftovers = [t for t in ("%s", "%d", "%%") if t in h]
        ok("D23 报告无未替换占位符", not leftovers, leftovers)

        # ---- E 组：跨平台字节一致性 ----
        # 行尾必须统一为 LF。两个独立的 Windows 陷阱会破坏这一点：
        #   1. open(..., "w") 默认把 \n 翻译成 os.linesep（Windows 下是 \r\n）
        #   2. csv 模块的 lineterminator 默认就是 \r\n，与平台无关
        # 后果不是「文件坏了」，而是同一份代码在 Windows 与 Linux 上
        # 产出的字节不同 —— 于是「可复现」只在同一平台内成立。
        # 这类缺陷在文本内容上完全看不出来，只有比对字节才会暴露。
        def has_crlf(path: str) -> bool:
            with open(path, "rb") as fh:
                return b"\r\n" in fh.read()

        csv_names = ["scores.csv", "rank_matrix.csv", "stability.csv",
                     "protocol_curve.csv"]
        bad_csv = [n for n in csv_names if has_crlf(os.path.join(out, n))]
        ok("E1 CSV 行尾为 LF（csv 模块默认 CRLF，须显式覆盖）", not bad_csv, bad_csv)
        ok("E2 meta.json 行尾为 LF", not has_crlf(os.path.join(out, "meta.json")))
        ok("E3 report.html 行尾为 LF", not has_crlf(rp))

        gt_bad = [n for n in sorted(os.listdir(gt))
                  if os.path.isfile(os.path.join(gt, n))
                  and has_crlf(os.path.join(gt, n))]
        ok("E4 真值文件行尾为 LF", not gt_bad, gt_bad[:5])

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    for fn in (test_protocol, test_metrics, test_segmatch, test_end_to_end):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            _FAIL.append("%s 抛出异常: %r" % (fn.__name__, exc))

    total = _PASS + len(_FAIL)
    print("=" * 62)
    print("通过 %d / %d" % (_PASS, total))
    if _FAIL:
        print("-" * 62)
        for f in _FAIL:
            print("FAIL  " + f)
        print("=" * 62)
        sys.exit(1)
    print("全部通过")
    print("=" * 62)
