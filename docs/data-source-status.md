# 数据源状态与复权因子口径

本表记录代码能力与本机实测，更新于 2026-09-30。出现“可接入”不代表已经启用；访问权限、接口条款和数据完整性由相应提供方决定。

| 来源 | 当前项目状态 | 下一步 |
| --- | --- | --- |
| 问财（同花顺） | 尚无独立的问财查询适配器 | 先确认账号的正式接口权限和返回字段，再以独立插件接入；不抓取登录态。 |
| 扶摇 | 已有日线、实时、财务与除权因子插件；本机测试时缺少可用 API Key | 使用者在本机设置页填 Key 后，逐数据集验证，再决定是否启用同步。Key 不提交到 Git。 |
| 通达信 TdxQuant | 项目已有 CSV 导入能力，尚无官方 `tqserver` 实时/因子适配器 | 官方 `get_divid_factors` 可作为候选；先确认客户端或后台订阅条件、字段口径和平台限制。 |
| GitHub 开源库 AKShare | 基金排名已通过本地 AKShare 服务读取东方财富公开数据；它是采集/清洗库，并非独立交易所行情 | 固定版本、记录上游和获取时间，给断流与字段变化提供提示。 |
| 东方财富 | 基金排名与部分基金数据已间接使用；公开接口并非稳定服务承诺 | 继续检查基金净值、排名截止时间、异常空值和许可条件。 |
| 腾讯财经 | 已有免费行情/分钟与基金报价路径 | 与其他来源交叉核验证券代码、时间和价格口径；不要把展示报价用于无保护的自动下单。 |
| 币安 | 独立「数字资产模拟」扩展读取现货和 U 本位合约公开盘口、标记价、资金费率显示与数量规则；现货/合约虚拟账本独立 | 目前仅允许用户手动提交研究模拟订单。手续费为固定示例值，未计盘口深度、资金费率、真实阶梯保证金及强平，不能用于实盘推断；不启用合约自动交易。 |

## 除权因子

- 本机 `data/adj_factor` 与 `data/adj_factor_etf` 在本次检查时尚无文件，不能宣称本机已经完成复权数据回填。
- BaoStock 的 `query_adjust_factor` 已在隔离环境只读实测：`sh.600519` 从 2001-08-27 到 2026-06-26 返回 31 条记录，字段包括 `dividOperateDate`、`foreAdjustFactor`、`backAdjustFactor`、`adjustFactor`。`adjustFactor` 在样本中从 1.000000 累积到 7.669257，不能直接当作本项目逐事件 `ex_factor`。需要按相邻事件比值转换，并与原始价格、除权日交叉校验。
- 当前 `stock-sdk` 插件的 `adj_factor` 将后复权收盘价除以原价后**逐日**返回；这与本项目只接受“除权事件日单次比值”的契约不符。修正前不能批量导入。
- 价格复权因子可能含现金分红。模拟盘仅在因子文件另有独立 `share_factor` 时变更持股数量；现金分红尚未记账。导入价格因子之前还需完善现金分红与送转股的独立事件账本，并在隔离副本核对净值、持仓和回测。

## 参考资料

- [通达信 tqserver 官方接口](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1hjbgqpdhv114/mindoc-1hjbht3176oi0.html)
- [通达信后台数据订阅说明](https://help.tdx.com.cn/quant/docs/markdown/mindoc-1cfsjkbf8f3is/mindoc-1d00kk3jsibbc.html)
- [AKShare 官方仓库](https://github.com/akfamily/akshare)
- [BaoStock 复权因子说明](https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf)
- [币安现货公开行情 API](https://developers.binance.com/en/docs/products/spot/rest-api)
- [币安 U 本位合约公开行情 API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

## 数字资产模拟边界

- 仅支持 `BTCUSDT`、`ETHUSDT`、`SOLUSDT`。现货与 U 本位合约各有 10,000 USDT 初始虚拟资金；账本写入 `data/user_data/crypto_paper.json`，不与 A 股模拟仓混用。
- 现货按公开最优买卖价估算，固定示例手续费 0.1%；合约为独立逐仓式研究模型，1–5 倍、固定示例手续费 0.05%，用公开标记价估算浮动盈亏。费率并非用户真实等级。
- 不计算资金费率、真实维持保证金阶梯、强平、滑点与盘口深度；币安断流时拒绝创建订单。合约自动策略和真实交易均未接入，界面必须持续展示这些限制。
- 不请求交易 API、私有账户 API 或密钥。今后若做真实交易，需先选定平台与账户权限，再单独设计密钥隔离、风控、审计和停机机制。
