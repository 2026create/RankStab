示例代码·11
示例代码与查询
编号：2026-0011
本节说明配置项的取值范围与默认行为，未列出的参数请沿用上一个稳定版本。
导出文件采用通用格式，用任意表格软件都能直接打开
SELECT userId， COUNT(*） AS n
FROM events
WHERE dt = '2026-09-23'
GROUP BY userId
HAVING n > 1oo;for i, row in enumerate(rows):
if row["score"] > 0.8:
keep.append(i)
导出文件采用通用格式，用任意表格软件都能直接打开。
