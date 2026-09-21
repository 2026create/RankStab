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

## 快速开始

```bash
# 依赖可选。装不上时自动使用同源离线快照表，行为一致。
pip install opencc-python-reimplemented

# 一条命令验收全部主张（自检 + 流水线 + 结果可重复）
python verify.py
```

预期输出 `全部通过：3 / 3`。**建议先跑这一条**——它比读文档快。

想逐步执行：

```bash
# 1. 自检：必须输出「通过 85 / 85」
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
│   └── 中文OCR标注工作台.html  数据集构建工具（单文件、零依赖、纯本地）
├── tests/
│   └── test_all.py         85 项自检
├── data/
│   ├── gt/                 真值（index.json + <sample_id>.md）
│   └── raw/                原始截图 / 文档（不入库）
├── models_out/             各模型输出（<model>/<sample_id>.md）
└── out/                    产物
```

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
| `protocol_curve.csv` | 模型 × 档位曲线。某模型在 P3 突然下降，说明它的主要偏差是全角字符 |
| `rank_matrix.csv` | 18 个完整榜单。用它可以指出「第 3 名和第 4 名在两个档位下互换」 |
| `scores.csv` | 每组的双口径 CER、S/D/I 分解、bootstrap 区间 |
| `meta.json` | 运行元信息。**复现时先看这个文件**——它记录了所有影响结果的开关 |

**一句话读法**：`rank_shift = 0` 的模型，名次可以放心引用；
`rank_shift >= 2` 的模型，任何单一榜单的排名都不足以支撑结论。

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
