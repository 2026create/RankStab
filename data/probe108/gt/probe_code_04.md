示例代码 · 04
示例代码与查询

编号：2026-0004

统计口径按月汇总，跨月的数据不会计入当期报表。

若在导入过程中遇到字段缺失，请先核对表头顺序是否与模板一致。

def parse(text):
    if not text:
        return []
    return [x for x in text.split() if x]SELECT userId, COUNT(*) AS n
FROM events
WHERE dt = '2026-09-23'
GROUP BY userId
HAVING n > 100;

该功能仅在网络连通时可用，离线状态下入口会被置灰。
