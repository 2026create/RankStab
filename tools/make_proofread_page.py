# -*- coding: utf-8 -*-
"""生成真值校对台（单文件 HTML，零依赖，双击即开）。

    python tools/make_proofread_page.py --images <图片目录> --gt <真值目录> \
        --out <输出的 html 路径>

设计要点：

1. **单文件、原生 JS、零构建零安装** —— 与本项目既定的展示层技术路线一致。
   图片用**相对路径**引用，所以输出必须放在图片目录的同级或其父级。

2. **预填 OCR 初稿，标注者只做「改」而不是「写」。** 从零手打的成本与出错率
   都远高于在机器结果上改错字。

3. **导出单个 JSON 而不是逐文件保存。** 标注者点一次「导出」得到一个文件，
   交回后由脚本统一写回 `gt/<sample_id>.md`。
   逐文件下载再手工放回目录，是格式错乱和漏交的主要来源。

4. **不做自动保存到磁盘** —— 浏览器无权直接写本地文件（除非起本地服务）。
   为降低丢工作风险，页面按样本维度记录草稿，并在关闭前提示未导出的改动。

⚠️ 真值的两条硬纪律已写入页面顶部，防止标注时"顺手规范化"：
   - 照抄所见：全角数字写全角、emoji 写 emoji，不转成半角
   - 原文错字照抄：不得修正原文本身的笔误
"""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import sys

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>真值校对台 · RankStab</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.6 "Microsoft YaHei",system-ui,sans-serif;
       background:#f2f2ef;color:#1a1a1a}
  header{position:sticky;top:0;z-index:10;background:#fff;
         border-bottom:1px solid #d8d8d8;padding:10px 16px}
  .row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  h1{font-size:16px;margin:0 12px 0 0}
  .pill{background:#eef3f9;border:1px solid #cddcec;border-radius:20px;
        padding:2px 10px;font-size:12px;color:#2f5d8a}
  .warn{background:#fff8e6;border:1px solid #f0dCa8;border-radius:4px;
        padding:8px 12px;margin-top:8px;font-size:12.5px;color:#6b5320}
  .warn b{color:#a1640a}
  button{font:inherit;padding:5px 12px;border:1px solid #bbb;background:#fff;
         border-radius:4px;cursor:pointer}
  button:hover{background:#f6f6f6}
  button.pri{background:#2f5d8a;color:#fff;border-color:#2f5d8a}
  button.pri:hover{background:#27506f}
  main{display:flex;gap:0;height:calc(100vh - 118px)}
  #side{width:190px;flex:none;overflow:auto;background:#fafaf8;
        border-right:1px solid #d8d8d8;padding:6px}
  #side div{padding:5px 8px;border-radius:4px;cursor:pointer;font-size:12.5px;
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #side div:hover{background:#eceff3}
  #side div.on{background:#2f5d8a;color:#fff}
  #side div.done::after{content:" \\2713";color:#2e7d32;font-weight:700}
  #side div.on.done::after{color:#c8e6c9}
  #imgpane{flex:1;overflow:auto;background:#e9e9e5;padding:14px;
           display:flex;justify-content:center;align-items:flex-start}
  #imgpane img{max-width:100%;height:auto;box-shadow:0 1px 4px rgba(0,0,0,.2);
               background:#fff}
  #editpane{width:44%;flex:none;display:flex;flex-direction:column;
            border-left:1px solid #d8d8d8;background:#fff}
  #editpane .bar{padding:6px 10px;border-bottom:1px solid #e6e6e6;
                 display:flex;align-items:center;gap:10px;font-size:12.5px}
  textarea{flex:1;border:0;outline:0;resize:none;padding:12px 14px;
           font:13.5px/1.9 Consolas,"Microsoft YaHei",monospace;
           white-space:pre-wrap;tab-size:2}
  .dirty{color:#c62828;font-weight:600}
  .clean{color:#2e7d32}
</style>
</head>
<body>
<header>
  <div class="row">
    <h1>真值校对台</h1>
    <span class="pill" id="prog">进度 0 / 0</span>
    <span class="pill" id="cur">—</span>
    <button id="prev">上一条</button>
    <button id="next">下一条</button>
    <button id="done" class="pri">标记完成并下一条</button>
    <button id="export">导出全部</button>
    <span id="state" class="clean">未修改</span>
  </div>
  <div class="warn">
    <b>两条硬纪律</b>：① <b>照抄所见</b> —— 全角数字就写全角、emoji 就写 emoji，
    不要"顺手"转成半角或删掉；② <b>原文错字照抄</b> ——
    <code>satrt</code>、<code>iostrean</code> 这类原文本身的笔误<b>不得修正</b>，
    改正了等于给标准答案注水。
    右侧文本已用机器结果预填，你只需<b>改错</b>，不需要从零写。
  </div>
</header>
<main>
  <div id="side"></div>
  <div id="imgpane"><img id="img" alt=""></div>
  <div id="editpane">
    <div class="bar">
      <span>真值文本</span>
      <span id="cnt">0 字</span>
      <span style="color:#888">改动会自动留在本页，导出前请勿关闭</span>
    </div>
    <textarea id="ta" spellcheck="false"></textarea>
  </div>
</main>
<script>
const SAMPLES = __SAMPLES__;
let i = 0;
const done = new Set();
const ta = document.getElementById('ta');

function cur(){ return SAMPLES[i]; }
function render(){
  const s = cur();
  document.getElementById('img').src = s.img;
  ta.value = s.text;
  document.getElementById('cur').textContent = s.id;
  document.getElementById('cnt').textContent = ta.value.length + ' 字';
  document.getElementById('prog').textContent =
      '进度 ' + done.size + ' / ' + SAMPLES.length;
  document.querySelectorAll('#side div').forEach((d, k) => {
    d.classList.toggle('on', k === i);
    d.classList.toggle('done', done.has(k));
  });
  updateState();
}
function updateState(){
  const el = document.getElementById('state');
  const changed = ta.value !== cur().text;
  el.textContent = changed ? '已修改' : '未修改';
  el.className = changed ? 'dirty' : 'clean';
}
function commit(){ cur().text = ta.value; }

const side = document.getElementById('side');
SAMPLES.forEach((s, k) => {
  const d = document.createElement('div');
  d.textContent = s.id;
  d.onclick = () => { commit(); i = k; render(); };
  side.appendChild(d);
});

ta.addEventListener('input', updateState);

document.getElementById('prev').onclick = () => {
  commit(); i = (i - 1 + SAMPLES.length) % SAMPLES.length; render();
};
document.getElementById('next').onclick = () => {
  commit(); i = (i + 1) % SAMPLES.length; render();
};
document.getElementById('done').onclick = () => {
  commit(); done.add(i);
  i = (i + 1) % SAMPLES.length; render();
};
document.getElementById('export').onclick = () => {
  commit();
  const out = {};
  SAMPLES.forEach(s => { out[s.id] = s.text; });
  const blob = new Blob([JSON.stringify(out, null, 2)],
                        {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'gt_export.json';
  a.click();
  document.getElementById('state').textContent =
      '已导出，请把下载到的 gt_export.json 交回';
};

window.addEventListener('beforeunload', e => {
  commit();
  const dirty = SAMPLES.some(s => s.text !== s.orig);
  if (dirty){ e.preventDefault(); e.returnValue = ''; }
});

render();
</script>
</body>
</html>
"""


def _force_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def ingest(export_json: str, gt_dir: str) -> int:
    """把校对台导出的 JSON 写回 gt/<sample_id>.md。

    只写回 JSON 里出现过的样本；不删任何已有文件 —— 万一导出不完整，
    宁可留下旧稿也不要丢数据。写回前打印逐条字数变化，便于人工复核。
    """
    with open(export_json, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data:
        print("导出文件格式不对或为空：%s" % export_json, file=sys.stderr)
        return 1

    os.makedirs(gt_dir, exist_ok=True)
    print("%-22s %8s %8s %8s" % ("sample_id", "原字数", "新字数", "变化"))
    print("-" * 52)
    n_changed = 0
    for sid in sorted(data):
        text = (data[sid] or "").strip()
        dst = os.path.join(gt_dir, sid + ".md")
        old = ""
        if os.path.exists(dst):
            with open(dst, encoding="utf-8") as f:
                old = f.read().strip()
        delta = len(text) - len(old)
        if text != old:
            n_changed += 1
        with open(dst, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
        print("%-22s %8d %8d %+8d%s"
              % (sid, len(old), len(text), delta,
                 "  <== 未改动，请确认是否漏校" if delta == 0 else ""))
    print("-" * 52)
    print("写回 %d 条，其中 %d 条有实际改动。" % (len(data), n_changed))
    if n_changed == 0:
        print("[!] 没有任何改动 —— 若确实校对过，说明导出的是未修改的版本。")
    return 0


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description="真值校对台：生成页 / 写回导出")
    ap.add_argument("--images", help="图片目录（raw/）")
    ap.add_argument("--gt", required=True, help="真值目录")
    ap.add_argument("--out", help="输出的 html 路径（生成模式）")
    ap.add_argument("--ingest", help="校对台导出的 gt_export.json（写回模式）")
    args = ap.parse_args()

    if args.ingest:
        return ingest(args.ingest, args.gt)

    if not (args.images and args.out):
        ap.error("生成模式需要同时给出 --images 与 --out")

    out_dir = os.path.dirname(os.path.abspath(args.out))
    samples = []
    for name in sorted(n for n in os.listdir(args.gt) if n.endswith(".md")):
        sid = os.path.splitext(name)[0]
        png = sid + ".png"
        if not os.path.exists(os.path.join(args.images, png)):
            print("[跳过] 无对应图片：%s" % sid)
            continue
        with open(os.path.join(args.gt, name), encoding="utf-8") as f:
            text = f.read().strip()
        # 图片用相对路径，保证 html 可以放在任意位置、双击即开
        rel = os.path.relpath(os.path.join(args.images, png), out_dir)
        samples.append({"id": sid, "img": rel.replace(os.sep, "/"),
                        "text": text, "orig": text})

    if not samples:
        print("没有可用样本", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(PAGE.replace("__SAMPLES__", json.dumps(samples,
                                                       ensure_ascii=False)))
    total = sum(len(s["text"]) for s in samples)
    print("已生成 %s" % args.out)
    print("样本 %d 条，合计 %d 字（预填自 OCR 初稿，等待人工校对）"
          % (len(samples), total))
    print("提示：导出得到的 gt_export.json 交回后，用 --ingest 写回 gt/*.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
