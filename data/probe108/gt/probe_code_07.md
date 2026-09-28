示例代码 · 07
示例代码与查询

编号：2026-0007

本说明适用于当前版本，后续若有调整会另行通知。

若在导入过程中遇到字段缺失，请先核对表头顺序是否与模板一致。

for i, row in enumerate(rows):
        if row["score"] > 0.8:
            keep.append(i)def parse(text):
    if not text:
        return []
    return [x for x in text.split() if x]
