示例代码·09
示例代码与查询
编号：2026-0009
如对结果有疑问，可携带记录编号联系值班人员核对
本说明适用于当前版本，后续若有调整会另行通知。
SELECT userId， COUNT(*） AS n
FROM events
WHERE dt = '2026-09-23'
GROUP BY userId
HAVING n > 100;def parse(text):
if not text:
return［]
return [x for x in text.split() if x]
