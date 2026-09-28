示例代码 · 12
示例代码与查询

编号：2026-0012

界面语言可在设置页切换，切换后需要重新登录一次方才生效。

如对结果有疑问，可携带记录编号联系值班人员核对。

SELECT userId, COUNT(*) AS n
FROM events
WHERE dt = '2026-09-23'
GROUP BY userId
HAVING n > 100;for i, row in enumerate(rows):
        if row["score"] > 0.8:
            keep.append(i)
