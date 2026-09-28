接口说明 · 11
接口与客户端说明

编号：2026-0011

使用 Python 调用 API，注意 timeout 默认是 30 秒。

缓存命中率 HIT RATE 与 QPS 呈正相关，详见 Dashboard。

接口返回 JSON 格式，字段 userId、userName 均为必填项。

在 SQL 中执行 EXPLAIN 可以看到执行计划。

该功能仅在网络连通时可用，离线状态下入口会被置灰。

系统会根据近期使用情况自动调整推荐顺序，排序结果不影响实际数据。
