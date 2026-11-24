# RankStab 文档解析评分协议敏感性评测

2026-11-22 测试提升版采用 OmniDocBench 筛选的 200 页、六套配置和 18 组协议。参考组显式固定为 whole/P0，bootstrap 2000 次，按 167 个已知文档或页面单元重采样。参考组五个相邻模型对中，2 对出现相对参考组逆序，共 8 个模型对与协议组组合。规则标签为 STABLE 0 对、FRAGILE 2 对、TIE 3 对。

四套本地输出为 PP-OCR v6、CnOCR、EasyOCR、Windows OCR，两套为 Docling、Tensorlake 的公开保存输出。CnOCR 使用 PP-OCR v6 检测器与 DenseNet-GRU 识别器。公开输出的精确模型版本未披露。200 页包含原回归集 38 页和新增 162 页，88 页的原始文档归属仍无法完全确认。完整 50 页历史回归集另行保存。

参考组（whole/P0）的固定分母 CER 依次为 0.4032、0.4649、0.5669、0.7686、0.8976、1.7415，对应 PP-OCR v6、CnOCR、EasyOCR、Docling、Windows OCR、Tensorlake。

## 运行

评分核心使用 Python 标准库，支持 Python 3.10 及以上。以下命令从 02_code 目录执行。

```powershell
python run.py --gt data/validation200/gt --models data/validation200/models_out --select-models ppocr-v6,cnocr,easyocr,windows_ocr,docling_public,tensorlake_public --ref-seg whole --boot 2000 --seed 20260921 --cluster-field bootstrap_cluster --max-edit-cells 16000000 --out ./out/validation200
python -m pip install -r requirements-test.txt
python tests/run_suite.py
python verify.py
```

输入真值目录包含 index.json 和每个样本的 Markdown，模型目录按模型名和样本编号组织。缺失预测按空文本计分，并登记 meta.json。默认拒绝覆盖非空结果目录，--force 在新结果全部生成成功后执行替换。

## 输出

- scores.csv 保存语料级 CER、S/D/I 和重采样区间。
- sample_scores.csv 保存每个样本在每个模型和协议下的误差统计。
- rank_matrix.csv 保存各协议的模型顺序。
- verdicts.csv 保存参考组相邻对的 gap、span、margin、标签、判定原因和最小配对分差。
- pair_protocol_details.csv 保存逐协议 B−A 分差和相对参考组的方向。
- stability.csv、protocol_curve.csv、meta.json 和离线 report.html 保存摘要、参考切分曲线、运行参数及复算命令。

STABLE 的充分条件限于已测且包含参考组的有限协议集合，等号允许追平。FRAGILE 的原因可能为实际逆序或充分条件未满足。TIE 按参考组区间重叠给出。最小配对分差独立显示是否实际逆序。每种切分内固定 P0 参考长度，上游匈牙利指派与本项目单调配对的差异已另行受控测试。

## 验证和材料

145 条回归断言、79 项自动化测试全部通过。四个评分核心文件（metrics / segmatch / runner / protocol）加权行覆盖率 96.55%，分支覆盖率 94.37%（268/284）。10 项指定故障注入均被断言发现。外部 50 页执行 226 项官方文本事实与顺序检查，结果按选中检查微平均。

判定层另有独立验证：60 对已知标签合成样本（STABLE / FRAGILE / TIE 各 20 对）的混淆矩阵 3×3 全中对角线，准确率 1.0；变形测试覆盖交换对称（A/B 互换后标签与翻转组数一致）、幂等性（同输入同参数字节一致）与档位顺序无关性；阈值扫描覆盖 0–2 并对 0.001–0.05 加密。证据在 `evidence/20261123_判定层验证/`。

主结果位于 `out/validation200/`（10 个产物文件，六份核心 CSV 与归档主实验内容一致；行尾按仓库 `.gitattributes` 统一为 LF），测试与判定层验证证据位于 `evidence/`，输入文本位于 `data/validation200/`（201 页真值 + 六套输出，仅文本，原图不入库，来源与许可见 03_data 的来源核对记录）。PPT、讲稿与评委说明在项目目录 09_国赛，操作记录与回退快照在 06_日志。当前边界包括筛选子集、未知文档归属、公开输出版本、行优先排序、多栏完整解析、真实用户试用与第三方独立复核。
