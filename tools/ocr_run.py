# -*- coding: utf-8 -*-
r"""在指定图片集上运行**声明的**行级 OCR 引擎，输出可作为被测模型的 Markdown。

    python tools/ocr_run.py --engine ppocr-v3 --name rapidocr_v3 \
        --images <图片目录> --models-out <模型输出根目录>

**与 tools/ocr_draft.py 的分工**：
  - `ocr_draft.py`  → 跑 OCR 给**人工校对真值**打底稿（草稿，会被改）
  - `ocr_run.py`    → 跑 OCR 作为**被测模型输出**（原样计分，不得修改）

两者必须分开：真值初稿会被人工改动，而被测模型输出**一个字都不能动**。
混用会让"起草偏倚"混进结论。

**关于 --engine 的设计**（重要，涉及项目自订纪律）：

引擎是**显式声明的实验变量**，不是"运行时探测环境"。
D-009 已经把「运行时探测环境」定为可复现性漏洞 ——
所以这里**不写** `try: import A except: import B` 这类回退逻辑。
选哪个引擎由调用者给出，并记录进 manifest。

可用引擎：

    ppocr-v3   rapidocr-onnxruntime  → PP-OCRv3   文档解析
    ppocr-v4   rapidocr (3.x)        → PP-OCRv4   文档解析
    ppocr-v5   rapidocr (3.x)        → PP-OCRv5   文档解析
    ppocr-v6   rapidocr (3.x)        → PP-OCRv6   文档解析
    cnocr      cnocr                 → CnOCR      文档解析（独立技术路线）
    easyocr    easyocr               → EasyOCR    文档解析（独立技术路线）
    ddddocr    ddddocr               → 验证码识别  **领域外对照**

前四个是 PP-OCR 系列的不同代际。主实验保留 v6，其他代际作历史对照。
当前 CnOCR 默认采用 PP-OCR v6 检测器与 DenseNet-GRU 识别器。
EasyOCR 采用 CRAFT 检测器与中英混合识别器。精确配置和权重随清单记录。
`ddddocr` **不是文档解析模型**，其角色见 `_Dddd` 的说明：它是**判据的对照**，
用来检验"差距很大时判据是否仍能给出 STABLE"。

**依赖与环境（环境本身也是实验变量，必须与 manifest 一起登记）**：

    # 全局解释器：ppocr-v4 / v5 / v6
    pip install rapidocr onnxruntime
    # 全局解释器：easyocr（自带 torch CPU 版）
    pip install easyocr
    # 全局解释器：ddddocr 对照（只在重跑对照时需要）
    pip install ddddocr

    # cnocr 必须独立 venv：cnocr 2.3.3 / cnstd 1.2.8 要求 rapidocr>=3.9.1，
    # 但它们调用的 rapidocr.utils.model_resolver.resolve_model_key 在 3.10 已移除，
    # 与上面 v4/v5/v6 所需的 rapidocr 3.10 冲突，故钉在 3.9.2：
    python -m venv %LOCALAPPDATA%\rankgap\venv-cnocr
    %LOCALAPPDATA%\rankgap\venv-cnocr\Scripts\python -m pip install cnocr onnxruntime "rapidocr==3.9.2"
    # 再用该解释器调用本脚本：
    #   ...\venv-cnocr\Scripts\python tools/ocr_run.py --engine cnocr ...

本机实测（2026-11-22）：rapidocr 3.10.0 / cnocr 2.3.3 / easyocr 1.7.2（torch 2.15.0+cpu）。
`ppocr-v3`（rapidocr-onnxruntime）未随现有输出重跑，其产物见 models_out 中已入库的历史目录。
"""

from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src.ocr_lines import boxes_to_markdown  # noqa: E402

SUPPORTED = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def _force_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


class _V3:
    """rapidocr-onnxruntime（PP-OCRv3）。"""

    name = "ppocr-v3"
    package = "rapidocr-onnxruntime"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self._eng = RapidOCR()

    def run(self, path: str) -> str:
        result, _elapse = self._eng(path)
        if not result:
            return ""
        boxes = [r[0] for r in result]
        texts = [r[1] for r in result]
        return boxes_to_markdown(boxes, texts)


class _V6:
    """rapidocr 3.x（PP-OCRv6）。"""

    name = "ppocr-v6"
    package = "rapidocr"

    def __init__(self):
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        from rapidocr import RapidOCR
        self._eng = RapidOCR()

    def run(self, path: str) -> str:
        out = self._eng(path)
        boxes = getattr(out, "boxes", None)
        txts = getattr(out, "txts", None)
        if boxes is None or txts is None:
            return ""
        return boxes_to_markdown(boxes, txts)


class _RapidVersioned:
    """rapidocr 3.x 指定代际（PP-OCRv4 / v5，mobile）。"""

    package = "rapidocr"
    version = ""

    def __init__(self):
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import ModelType, OCRVersion
        v = getattr(OCRVersion, self.version)
        self._eng = RapidOCR(params={
            "Det.ocr_version": v,
            "Det.model_type": ModelType.MOBILE,
            "Rec.ocr_version": v,
            "Rec.model_type": ModelType.MOBILE,
            "Global.log_level": "error",
        })

    def run(self, path: str) -> str:
        out = self._eng(path)
        boxes = getattr(out, "boxes", None)
        txts = getattr(out, "txts", None)
        if boxes is None or txts is None:
            return ""
        return boxes_to_markdown(boxes, txts)


class _V4(_RapidVersioned):
    name = "ppocr-v4"
    version = "PPOCRV4"


class _V5(_RapidVersioned):
    name = "ppocr-v5"
    version = "PPOCRV5"


class _Dddd:
    """ddddocr —— **领域外对照，不是文档解析模型**。

    它的识别模型是为验证码/单行短文本训练的。把它放进本项目**不是**为了
    比较文档解析水平，而是为了检验判据在极端位置是否健全：

        一个远落后于其他的模型，判据**应当**给出稳定的"垫底"，
        而不是同样报 `FRAGILE`。

    若连它都判成 `FRAGILE`，说明判据在"差距很大"时也失去了分辨力，
    那才是判据本身的问题。**它是判据的对照，不是排名表的选手。**

    之所以选它：模型（`common.onnx` 等三个文件）**随 wheel 分发**，
    不需要联网拉取，适合当"极端垫底"的判据对照。
    """

    name = "ddddocr"
    package = "ddddocr"
    domain = "out-of-domain-control"

    def __init__(self):
        import ddddocr
        self._det = ddddocr.DdddOcr(det=True, show_ad=False)
        self._cls = ddddocr.DdddOcr(show_ad=False)

    def run(self, path: str) -> str:
        import io

        from PIL import Image

        with open(path, "rb") as f:
            raw = f.read()
        rects = self._det.detection(raw)
        if not rects:
            return ""
        img = Image.open(io.BytesIO(raw)).convert("RGB")

        polys, texts = [], []
        for r in rects:
            x1, y1, x2, y2 = (int(v) for v in r[:4])
            if x2 <= x1 or y2 <= y1:
                continue
            buf = io.BytesIO()
            img.crop((x1, y1, x2, y2)).save(buf, format="PNG")
            texts.append(self._cls.classification(buf.getvalue()) or "")
            # ddddocr 给的是轴对齐矩形，还原成 boxes_to_markdown 要的 4 点多边形
            polys.append([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
        return boxes_to_markdown(polys, texts)


class _Cnocr:
    """CnOCR 默认采用 PP-OCR v6 检测器和 DenseNet-GRU 识别器。"""

    name = "cnocr"
    package = "cnocr"

    def __init__(self):
        from cnocr import CnOcr
        self._eng = CnOcr()

    def run(self, path: str) -> str:
        outs = self._eng.ocr(path) or []
        polys, texts = [], []
        for o in outs:
            t = (o.get("text") or "").strip()
            pos = o.get("position")
            if not t or pos is None:
                continue
            polys.append([[float(x), float(y)] for x, y in pos])
            texts.append(t)
        return boxes_to_markdown(polys, texts)


class _EasyOCR:
    """EasyOCR —— CRAFT 检测 + CRNN 识别，PyTorch 实现，独立于 PP-OCR 家族。

    识别模型选 `ch_sim + en`（官方中英混合权重），用于检验结论是否只在
    「百度系 + CnOCR」两族之间成立。
    """

    name = "easyocr"
    package = "easyocr"

    def __init__(self):
        import easyocr
        self._eng = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)

    def run(self, path: str) -> str:
        outs = self._eng.readtext(path, detail=1, paragraph=False,
                                  batch_size=getattr(self, 'batch_size', 1),
                                  canvas_size=getattr(self, 'canvas_size', 2560))
        polys, texts = [], []
        for box, text, _conf in outs:
            t = (text or "").strip()
            if not t:
                continue
            polys.append([[float(x), float(y)] for x, y in box])
            texts.append(t)
        return boxes_to_markdown(polys, texts)


ENGINES = {"ppocr-v3": _V3, "ppocr-v4": _V4, "ppocr-v5": _V5,
           "ppocr-v6": _V6, "cnocr": _Cnocr, "easyocr": _EasyOCR,
           "ddddocr": _Dddd}


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="运行声明的 OCR 引擎，产出被测模型输出")
    ap.add_argument("--engine", required=True, choices=sorted(ENGINES))
    ap.add_argument("--name", required=True,
                    help="模型名，输出写入 <models-out>/<name>/")
    ap.add_argument("--images", required=True)
    ap.add_argument("--models-out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument('--resume', action='store_true', help='保留已有样本输出，仅推理缺失文件。')
    ap.add_argument('--threads', type=int, default=None, help='显式设置 PyTorch CPU 线程数。')
    ap.add_argument('--batch-size', type=int, default=1, help='EasyOCR 识别批大小。')
    ap.add_argument('--canvas-size', type=int, default=2560, help='EasyOCR 检测画布最大边。')
    args = ap.parse_args()

    if args.threads is not None:
        if args.threads < 1:
            ap.error('--threads must be positive')
        if args.engine == 'easyocr':
            import torch
            torch.set_num_threads(args.threads)
            torch.set_num_interop_threads(1)

    try:
        engine = ENGINES[args.engine]()
    except ImportError as e:
        print("缺少依赖：pip install %s（%s）" % (ENGINES[args.engine].package, e),
              file=sys.stderr)
        return 2
    if args.batch_size < 1:
        ap.error('--batch-size must be positive')
    if args.engine == 'easyocr':
        engine.batch_size = args.batch_size
        if args.canvas_size < 32:
            ap.error('--canvas-size must be at least 32')
        engine.canvas_size = args.canvas_size

    names = sorted(n for n in os.listdir(args.images)
                   if os.path.splitext(n)[1].lower() in SUPPORTED)
    if args.limit:
        names = names[:args.limit]
    if not names:
        print("目录下没有图片：%s" % args.images, file=sys.stderr)
        return 1

    dst = os.path.join(args.models_out, args.name)
    os.makedirs(dst, exist_ok=True)

    t0 = time.time()
    for i, n in enumerate(names, 1):
        sid = os.path.splitext(n)[0]
        if args.resume and os.path.isfile(os.path.join(dst, sid + '.md')):
            continue
        s = time.time()
        md = engine.run(os.path.join(args.images, n))
        with open(os.path.join(dst, sid + ".md"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write(md + "\n")
        print("[%2d/%2d] %-24s %5d 字  %5.1fs"
              % (i, len(names), sid, len(md), time.time() - s))

    manifest = {
        "model_name": args.name,
        "engine": args.engine,
        "engine_package": ENGINES[args.engine].package,
        # 领域外对照必须显式登记，避免它被误读成"第三个文档解析模型"
        "domain": getattr(ENGINES[args.engine], "domain", "document-parsing"),
        "n_samples": len(names),
        "images_dir": os.path.abspath(args.images),
        "elapsed_sec": round(time.time() - t0, 1),
        "python_version": sys.version,
        "resume": args.resume,
        "cpu_threads": args.threads,
        "recognition_batch_size": args.batch_size,
        "detection_canvas_size": args.canvas_size if args.engine == 'easyocr' else None,
        "note": ("被测模型输出，原样计分，不得人工修改。"
                 "引擎为显式声明，不做运行时回退（见 D-009）。"),
    }
    packages = {}
    for package in ('rapidocr', 'onnxruntime', 'cnocr', 'cnstd', 'easyocr', 'torch', 'torchvision'):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            pass
    manifest['package_versions'] = packages
    weight_roots = []
    if args.engine.startswith('ppocr-v'):
        import rapidocr
        weight_roots.append(os.path.dirname(rapidocr.__file__))
    elif args.engine == 'cnocr':
        from cnocr.utils import data_dir
        from cnstd.utils import data_dir as detector_data_dir
        weight_roots.extend([str(data_dir()), str(detector_data_dir())])
    elif args.engine == 'easyocr':
        weight_roots.append(os.path.expanduser('~/.EasyOCR/model'))
    weights = []
    for weight_root in weight_roots:
        for folder, _, files in os.walk(weight_root):
            for filename in files:
                if filename.endswith(('.onnx', '.pth', '.pt')):
                    path = os.path.join(folder, filename)
                    with open(path, 'rb') as handle:
                        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
                    weights.append({'file': path, 'sha256': digest, 'bytes': os.path.getsize(path)})
    manifest['available_weight_files'] = weights
    with open(os.path.join(dst, "_manifest.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("-" * 58)
    print("完成 %d 条，用时 %.0fs -> %s" % (len(names), time.time() - t0, dst))
    print("manifest: %s" % json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
