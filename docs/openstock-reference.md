# OpenStock 交互参考

核对版本：[Open-Dev-Society/OpenStock · 855ca6a](https://github.com/Open-Dev-Society/OpenStock/tree/855ca6af33ae0b86f8835ecda6c31707f4ec3891)。

## 本次实现

参考上游多证券标签和指标偏好记忆的产品思路，使用本看板既有组件、数据流与存储独立实现：

- **最近查看**：A股 / ETF 分析、港股研究、美股研究分别保留最多6个最近证券，名称优先显示，一键切换。仅在本浏览器保存代码和名称；记录不是自选、持仓或交易信号。旧版单个“上次查看”仍可读取。清除记录保留当前图表，不影响其他市场。
- **按需图表**：最近记录不会创建隐藏图表或预取行情。切换后仍使用原有查询键和缓存释放规则；返回旧证券时，需要的数据按原接口重新查询。
- **日K详情偏好**：个股详情的“自研Canvas / 专业指标”手动选择、专业指标集合会在本浏览器记忆并用于其他证券。配置缺失或损坏时回到Canvas与成交量默认设置；回测区间等必须用专业图时仍按原规则展示，不覆盖手选模式。研究日期、锚点和缩放继续按原上下文管理。

上述改动直接位于现有研究页面、`useLastStock` 和日K组件，属于局部 L3 改动；已有插槽不包含这些控件。没有新增数据源、API、定时任务、依赖或交易行为。服务器账户与行情文件不受影响。

## 数据和服务取舍

上游的 Finnhub 访问需要自己的 API Key；其[市场支持说明](https://github.com/Open-Dev-Society/OpenStock/blob/855ca6af33ae0b86f8835ecda6c31707f4ec3891/MARKET_SUPPORT.md)将港股排除在免费报价支持范围外。免费 TradingView 嵌入图表、付费或需密钥的情绪服务不是本次免费数据补充。原有港美股数据、Canvas/ECharts、分钟缓存与账户体系继续按本看板契约运行。

上游的共享缓存、并发合并、超时和失败保留旧值等思路，本看板已有对应机制，复用现有实现。本批不增加第二套缓存或通过多密钥轮换扩展配额。

## 参考范围

- [多证券导航](https://github.com/Open-Dev-Society/OpenStock/blob/855ca6af33ae0b86f8835ecda6c31707f4ec3891/components/shell/TabBar.tsx)
- [图表指标偏好](https://github.com/Open-Dev-Society/OpenStock/blob/855ca6af33ae0b86f8835ecda6c31707f4ec3891/components/stocks/StockChart.tsx)
- [服务端行情缓存](https://github.com/Open-Dev-Society/OpenStock/blob/855ca6af33ae0b86f8835ecda6c31707f4ec3891/lib/actions/finnhub.actions.ts)

OpenStock 采用 [AGPL-3.0](https://github.com/Open-Dev-Society/OpenStock/blob/855ca6af33ae0b86f8835ecda6c31707f4ec3891/LICENSE)。本次未复制其源码、样式、图标或资源，项目许可证保持原值；如后续直接引入上游代码，需另行处理其许可要求。
