# RankStab · 稳定度

> **一句话**：已有榜单给的是「一套评分规则下的排名」；本仓库给的是「这个排名有多稳」。
>
> **上游是谁**：OmniDocBench（Apache-2.0，OpenDataLab / 上海人工智能实验室，CVPR 2025）。
> **我们改什么**：它的评测器里有三种匹配方式和一套写死的文本比对规则，但从未量化过这些规则本身会让分数和名次改变多少。
> **凭什么算实质改进**：我们把这套规则变成可配置的 6 档协议，把差异量化成一张稳定度表，并把这个能力提交给上游。

---

## 这是什么

一份**可执行的实验方法**，回答一个问题：

> 文档解析评测榜上的名次，换一套评分细则还算不算数？

答案形式不是新排名，而是**排名的稳定度**——哪些名次是稳的，哪些一换规则就翻，哪些模型其实分不出胜负。

它不是一个新的评测基准。数据集是上游既有的，模型是别人跑出来的。
本仓库改的是**「怎么批卷」**这一层。

```
一批模型输出（不变）
        ↓
3 种切分方式  ×  6 档归一化协议
        ↓
同一批输出重算 18 遍  ← 不重跑任何模型，计算成本趋近于零
        ↓
名次矩阵 / 稳定度表 / 宽容度曲线 / bootstrap 置信区间
```

---

## 在线演示（无需任何环境）

**https://2026create.github.io/RankStab/**

国内镜像（魔搭创空间）：https://www.modelscope.cn/studios/en0123/rankstab-demo

单页应用：粘贴两段文字，现场看六档协议怎么改变分数；其余页签展示全部实测结果与判定。
同一页面也在仓库根目录 `index.html`，**下载后双击即可离线使用**。

---

## 快速开始

```bash
# 无必需依赖。繁简转换走仓库内固化的离线快照表（src/trad_table.py），
# 不依赖 opencc，也不受其版本变化影响 —— 见 SPEC.md 第 2.2 与 7 节。

# 一条命令验收全部主张（自检 + 流水线 + 结果可重复）
python verify.py
```

预期输出 `全部通过：3 / 3`。**建议先跑这一条**——它比读文档快。

想逐步执行：

```bash
# 1. 自检：必须输出「通过 98 / 98」
python tests/test_all.py

# 2. 用合成数据跑通全流程（不需要任何真实数据）
python run.py --demo
```

`run.py --demo` 会打印一张名次稳定度表：

```
模型                      最低名  最高名   位移   名次σ
model_flatten                 2      5      3    1.18
model_traditional             2      5      3    0.97
model_clean                   1      3      2    0.74
model_fullwidth               1      3      2    0.74
model_spacy                   3      5      2    0.60
```

五个模型的名次全都不稳。**这就是本项目要交付的那句话。**

---

## 目录结构

```
rankstab/                   ← 本目录即仓库根
├── run.py                  一键入口
├── verify.py               可复现性验收脚本
├── SPEC.md                 规格说明 v1.0（六档协议的正式定义）
├── src/
│   ├── protocol.py         六档归一化 P0..P5 + 规则类型登记
│   ├── metrics.py          编辑距离 S/D/I 分解、双口径 CER、bootstrap
│   ├── segmatch.py         三种切分/匹配方式
│   ├── runner.py           18 组重算 + 名次矩阵 + 稳定度
│   ├── make_demo.py        合成探针集与五种模拟模型输出
│   └── trad_table.py       繁简离线快照表（由 OpenCC 生成，勿手改）
├── tools/
│   ├── gen_trad_table.py   重新生成繁简快照表
│   ├── WinOCR.ps1          Windows 内置 OCR 引擎接入脚本（真实数据层）
│   └── 中文OCR标注工作台.html  数据集构建工具（单文件、零依赖、纯本地）
├── tests/
│   └── test_all.py         98 项自检
├── data/
│   ├── gt/                 真值·合成演示层（index.json + gen_*.md，10 条）
│   ├── probe108/           扩充探针层（自研生成，CC-BY-4.0，随仓库分发）
│   │   ├── gt/             真值 108 条（index.json + probe_*.md）
│   │   ├── images/         渲染原图 108 张
│   │   ├── models_out/     4 个真实 OCR 引擎输出（rapidocr_v3/v6、windows_ocr、ddddocr）
│   │   └── coverage_audit.csv  敏感特征覆盖审计
│   ├── real22/             真实截图层（真值文本与引擎输出随仓库公开）
│   │   ├── gt/             人工逐字校对真值 22 条（约 1.13 万字）
│   │   ├── models_out/     4 引擎输出（v3 为起草偏倚对照角色）
│   │   ├── annotation_protocol.md  标注规范
│   │   └── sample_list.csv / coverage_audit_gt_final.csv
│   └── raw/                原始截图（不入库，见下方「数据分发边界」）
├── models_out/             合成演示层模拟输出（<model>/<sample_id>.md）
├── out/                    演示层产物（run.py 默认）
├── out/probe108/           扩充探针层产物
└── out/real22/             真实截图层产物
```

### 数据分发边界

- 合成探针层（`probe108`）：本项目自研生成，CC-BY-4.0，**含渲染原图全部随仓库分发**。
- 真实截图层（`real22`）：截图含第三方界面内容（Apple 官方帮助页、即时通讯对话等），
  **原图不随本仓库分发**（见 `NOTICE` 第 4 节）；**真值文本与引擎输出全部随仓库公开**，
  原图随比赛数据包另行提交，供评委对照核验。
- 三层数据**必须分层评测，不得混跑**：各层样本难度与模型覆盖不同，
  混算会触发本项目自己论证过的「口径混算陷阱」（见 `out/real22` 报告页反例）。

### 复现两个数据层

```bash
# 扩充探针层（108 样本 × 4 引擎，行级层）
python run.py --gt data/probe108/gt --models data/probe108/models_out --out out/probe108 --seg-modes whole

# 真实截图层（22 样本 × 4 引擎，行级层）
python run.py --gt data/real22/gt --models data/real22/models_out --out out/real22 --seg-modes whole
```

参考组（whole / P0）的 cer_fixed 应为：
probe108 — rapidocr_v6 `0.108470`、rapidocr_v3 `0.136389`、windows_ocr `0.795588`、ddddocr `0.950874`；
real22 — rapidocr_v6 `0.140943`、rapidocr_v3（起草偏倚对照）`0.185605`、windows_ocr `0.651980`、ddddocr `0.960320`。
种子固定，逐字节可复现。

---

## 接入你自己的数据

### 1. 放真值

```
data/gt/index.json
data/gt/wx_0001.md
data/gt/wx_0002.md
```

`index.json`：

```json
[
  {
    "sample_id": "wx_0001",
    "doc_type": "chat_screenshot",
    "source": "real",
    "sensitive_attrs": ["fullwidth_digit"],
    "license": "team-authored, de-identified, CC-BY-4.0"
  }
]
```

`source` 三选一，**不得混统**：

| 值 | 含义 |
|---|---|
| `generated` | 用 HTML 构造界面再截图，真值天然 100% 准确，零标注成本 |
| `degraded` | 由干净文本反向退化（转繁体、全角化、加噪、压缩），真值同样无损 |
| `real` | 真实采集。**必须**完成脱敏（昵称/头像/手机号/二维码）并保留可再分发依据 |

### 2. 放模型输出

```
models_out/MinerU/wx_0001.md
models_out/docling/wx_0001.md
```

### 3. 跑

```bash
python run.py
```

---

## 输出解读

| 文件 | 关注什么 |
|---|---|
| `stability.csv` | **核心产出。** `rank_shift` 越大，说明这个模型的名次越依赖评分规则 |
| `verdicts.csv` | **名次可信度判定。** 每个相邻名次对给出 `STABLE` / `FRAGILE` / `TIE` |
| `protocol_curve.csv` | 模型 × 档位曲线。某模型在 P3 突然下降，说明它的主要偏差是全角字符 |
| `rank_matrix.csv` | 18 个完整榜单。用它可以指出「第 3 名和第 4 名在两个档位下互换」 |
| `scores.csv` | 每组的双口径 CER、S/D/I 分解、bootstrap 区间 |
| `meta.json` | 运行元信息。**复现时先看这个文件**——它记录了所有影响结果的开关 |

**一句话读法**：`rank_shift = 0` 的模型，名次可以放心引用；
`rank_shift >= 2` 的模型，任何单一榜单的排名都不足以支撑结论。

**更严格的读法看 `verdicts.csv`**——名次位移只是现象，判定才是结论：

| 判定 | 含义 | 怎么用 |
|---|---|---|
| `STABLE` | `margin >= 1`，协议抖动的极限也追不上这个差距 | 名次可以引用 |
| `FRAGILE` | 已观测到翻转，或 `margin < 1` 翻得动 | 换规则就变，别拿它下结论 |
| `TIE` | bootstrap 区间重叠 | 连谁赢都没定，这个名次本身没意义 |

> `margin = gap / (span_a + span_b)`。阈值取 **1** 不是超参数：
> 等于 1 就是「领先幅度正好等于协议能把两者掀起的极限」。判据见 `SPEC.md` 第 4.5 节。

---

## 已知边界

1. 合成数据（`source=generated`）只能证明**方法可行**，不能证明任何真实工具的水平。
2. 三种切分方式是按上游公开描述的**等价重建**，尚未与上游源码逐条对齐，
   结论涉及切分方式时须标注 `reconstructed`（已自动写入 `meta.json`）。
3. 标点归一表为 25 条 1:1 映射，覆盖范围有限，不得宣称其为完整标点标准。
4. 不声称「构建了一个评测基准」——基准是上游的，本项目做的是在其之上量化它没量化的一层。

---

## 许可与致谢

- 上游：OmniDocBench，Apache-2.0
- 繁简转换：OpenCC t2s（以离线快照形式固化，见 `src/trad_table.py`）
- 本仓库自研部分：六档协议、双分母口径、稳定度统计、评测流水线

详细方法定义见 [SPEC.md](SPEC.md)。

