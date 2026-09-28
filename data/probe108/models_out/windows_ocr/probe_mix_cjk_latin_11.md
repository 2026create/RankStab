接 囗 说 明 · 1 1
接 口 与 客 户 端 说 明
编 号 ， 2026 一 0011
使 用 Python 调 用 API, 注 意 timeout 默 认 是 30 秒 。
缓 存 命 中 率 HIT RATE 与 QPS 呈 正 相 关 ， 详 见 Dashboard0
接 口 返 回 JSON 格 式 ， 字 段 userld 、 userName 均 为 必 填 项 。
在 SQL 中 执 行 EXPLAIN 可 以 看 到 执 行 计 划 。
该 功 能 仅 在 网 络 连 通 时 可 用 ， 离 线 状 态 下 入 口 会 被 置 灰 。
系 统 会 根 据 近 期 使 用 情 况 自 动 调 整 推 荐 顺 序 ， 排 序 结 果 不 影 响 实 际 数 据 。
