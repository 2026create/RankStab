示例代码·01
示例代码与查询
编号：2026-0001
导出文件采用通用格式，用任意表格软件都能直接打开。
统计口径按月汇总，跨月的数据不会计入当期报表。
def parse(text):
if not text:
return []
return [x for x in text.split() if x]SELECT userId, CoUNT(*) AS n
FROM events
WHERE dt = '2026-09-23'
GROUP BY userId
HAVING n > 100;
