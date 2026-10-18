"""Sense2Act 常驻采集 worker(契约:Backend/docs/api-design.md §9.1)。

领取到期信息源 → 适配器抓列表 → 礼貌补抓详情 → 映射成文档 → 批量推送 → 回报运行结果。
适配器、唯一出网点与后端客户端复用 agent 的 sense2act 包,本包只做采集编排。
"""
