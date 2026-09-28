示 例 代 码 · 1 2
示 例 代 码 与 查 询
编 号 ， 2026 一 0012
界 面 语 言 可 在 设 置 页 切 换 ， 切 换 后 需 要 重 新 登 录 一 次 方 才 生 效 。
如 对 结 果 有 疑 问 ， 可 携 带 记 录 编 号 联 系 值 班 人 员 核 对 。
S E L E CT userld, COUNT(*) AS n
FROM events
WH E R E dt
' 2926 一 99 一 23 '
GROUP BY u s e r I d
HAVING n 〉 199 ； for i ， ro 1 n enumerate(rows) ：
if row["score"] > 9 ． 8 ：
keep.append(i)
