# -*- coding: utf-8 -*-
"""RankStab（稳定度）: 文档解析评测中「排名稳定度」的量化工具。

核心命题
--------
已有榜单给的是「一套评分规则下的排名」；本工具量化的是「这个排名有多稳」。

模块
----
protocol   六档文本归一化协议 P0..P5
metrics    编辑距离 S/D/I 分解、CER、bootstrap 置信区间
segmatch   三种切分/匹配方式 whole / para / para_merge
runner     3 x 6 = 18 组重算 + 名次矩阵 + 稳定度表
make_demo  合成探针集与模拟模型输出（打通流水线用）
"""

__version__ = "0.1.0"
