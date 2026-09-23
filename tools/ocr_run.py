# -*- coding: utf-8 -*-
"""在指定图片集上运行**声明的**行级 OCR 引擎，输出可作为被测模型的 Markdown。

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

可用引擎（两者都是 PP-OCR 系列，但**代际不同**，是真正不同的识别模型）：

    ppocr-v3   rapidocr-onnxruntime  → PP-OCRv3
    ppocr-v6   rapidocr (3.x)        → PP-OCRv6

依赖：pip install rapidocr-onnxruntime rapidocr
"""

from __future__ import annotations

import argparse
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


ENGINES = {"ppocr-v3": _V3, "ppocr-v6": _V6}


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="运行声明的 OCR 引擎，产出被测模型输出")
    ap.add_argument("--engine", required=True, choices=sorted(ENGINES))
    ap.add_argument("--name", required=True,
                    help="模型名，输出写入 <models-out>/<name>/")
    ap.add_argument("--images", required=True)
    ap.add_argument("--models-out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    try:
        engine = ENGINES[args.engine]()
    except ImportError as e:
        print("缺少依赖：pip install %s（%s）" % (ENGINES[args.engine].package, e),
              file=sys.stderr)
        return 2

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
        "n_samples": len(names),
        "images_dir": os.path.abspath(args.images),
        "elapsed_sec": round(time.time() - t0, 1),
        "note": ("被测模型输出，原样计分，不得人工修改。"
                 "引擎为显式声明，不做运行时回退（见 D-009）。"),
    }
    with open(os.path.join(dst, "_manifest.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("-" * 58)
    print("完成 %d 条，用时 %.0fs -> %s" % (len(names), time.time() - t0, dst))
    print("manifest: %s" % json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
