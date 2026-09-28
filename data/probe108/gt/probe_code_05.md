示例代码 · 05
示例代码与查询

编号：2026-0005

统计口径按月汇总，跨月的数据不会计入当期报表。

任务执行完毕后会自动生成一份摘要，可在历史记录中随时回看。

SELECT userId, COUNT(*) AS n
FROM events
WHERE dt = '2026-09-23'
GROUP BY userId
HAVING n > 100;for i, row in enumerate(rows):
        if row["score"] > 0.8:
            keep.append(i)

如对结果有疑问，可携带记录编号联系值班人员核对。
