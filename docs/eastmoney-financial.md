# Eastmoney Financial 插件

`eastmoney_financial` 是无需 API Key 的内置财务插件，只声明 `financial` 数据集，
实现现有 `get_financials(table, symbols, latest_only=False)` 契约。插件通过东财公开接口
`https://datacenter-web.eastmoney.com/api/data/v1/get` 获取数据；它不写本地文件，
`fetch_period(table, period, symbols=None)` 可供 staging 调用，返回标准化 Polars DataFrame。

## 报告与字段映射

| 表 | 东财报告 | 字段映射 |
| --- | --- | --- |
| `income` | `RPT_DMSK_FN_INCOME` | `TOTAL_OPERATE_INCOME` → `revenue`; `OPERATE_COST` → `operating_cost`; `SALE_EXPENSE` → `selling_expense`; `MANAGE_EXPENSE` → `admin_expense`; `FINANCE_EXPENSE` → `financial_expense`; `OPERATE_PROFIT` → `operating_profit`; `TOTAL_PROFIT` → `total_profit`; `INCOME_TAX` → `income_tax`; `PARENT_NETPROFIT` → `net_income_attributable` |
| `balance_sheet` | `RPT_DMSK_FN_BALANCE` | `TOTAL_ASSETS` → `total_assets`; `TOTAL_LIABILITIES` → `total_liabilities`; `TOTAL_EQUITY` → `total_equity`; `MONETARYFUNDS` → `cash_and_equivalents`; `ACCOUNTS_RECE` → `accounts_receivable`; `DEBT_ASSET_RATIO` → `debt_to_asset_ratio` |
| `cash_flow` | `RPT_DMSK_FN_CASHFLOW` | `NETCASH_OPERATE` → `net_operating_cash_flow`; `NETCASH_INVEST` → `net_investing_cash_flow`; `NETCASH_FINANCE` → `net_financing_cash_flow`; `CONSTRUCT_LONG_ASSET` → `capex`; `CCE_ADD` → `net_cash_change` |
| `metrics` | `RPT_LICO_FN_CPD` | `BASIC_EPS` → `eps_basic`; `BPS` → `bps`; `WEIGHTAVG_ROE` → `roe`; `XSMLL` → `gross_margin`; `YSTZ` → `revenue_yoy`; `SJLTZ` → `net_income_yoy`; `MGJYXJJE` is retained under its source name |

报表金额单位为元；百分比字段保持东财的百分数口径；`MGJYXJJE` 是每股经营现金流，
保留东财数值和字段名。收入表的 `net_income` 保持 null，因为当前没有验证过的
合并净利润映射；`PARENT_NETPROFIT` 单独写入 `net_income_attributable`。`net_margin` 保持
Float64 null。`get_financials("metrics", ...)` 在每个有指标数据的报告期同步拉取同期间、
仅保留请求标的的资产负债表，用 `symbol` + `period_end` 左连接，仅用
`balance_sheet.debt_to_asset_ratio` 补 metrics 中的 null；指标值已有值时不覆盖。补值行的
`announce_date` 取指标与资产负债表日期的较晚值。余额表无该标的/报告期/有效比例时保留
null；余额表请求失败则整次 metrics 调用失败。这个合并规则也通过纯函数
`merge_metrics_with_balance(metrics, balance_sheet)` 暴露给 staging 复用，不使用现价或
估值快照推导债资比。`fetch_period("metrics", ...)` 返回尚未合并的原始指标期数据。
插件没有股本接口，`shares` 返回空 DataFrame。

## 报告期与可用日期

- 每次完整历史调用最多检查最近 8 个已结束季度，按报告期从新到旧查询。
- `latest_only=False` 返回这些期间内可用的全部记录。`latest_only=True` 对每个请求标的选
  最近一个有效报告期；如果最新季度尚未披露，会继续查更早季度。若所有请求标的已找到
  有效记录则提前结束查询。
- 东财在 2026-09-30 这个已结束季度尚未发布数据时返回 code 9201，插件将该明确空期
  视为无记录并继续查询；其他失败会抛出异常，整张表不会返回部分期间结果。
- 报表使用 `REPORT_DATE`，指标使用 `REPORTDATE`。所有报表都从可用的 `NOTICE_DATE` 和
  `UPDATE_DATE` 中取较晚日期作为 `announce_date`；缺少全部可用日期或日期晚于当前北京时间
  日期的记录会被跳过，避免未来信息进入历史记录。
- 相同标的和报告期的重复版本保留可用日期较晚的记录；同一可用日期出现内容冲突时抛错。
  东财端点返回当前记录/重述结果，不提供经验证的历史版本快照，所以这不等同于 point-in-time
  财报历史。需要逐日回测时，应使用带版本时间戳的历史来源。

## 请求限制和失败处理

- 报表按期批量获取，不逐股发 HTTP 请求；响应中只保留调用者请求的 `SECUCODE`。
- 每页 500 行，每个报告期最多 100 页；跨页 `count` / `pages` 必须稳定，`pages` 必须与
  `count` 和每页应返回行数吻合，最终实际行数也必须等于 `count`。空页、短页、页面数超限、
  结构无效、HTTP 失败或任一页失败都会使该期失败，不返回已经取得的部分页数据。
- 相邻请求至少间隔 0.3 秒。指标额外筛选 A 股证券类型 `058001001` / `058001008`；不以
  `ISNEW` 过滤，因为历史报告期可能标记为 `0`。历史新旧版本按报告期和公告/更新日期校验、
  去重。
- 非有限数值 (`NaN`、正负无穷) 转成 null。未知表名由 `get_financials` 返回空结果；
  `fetch_period` 对未知表名报错，便于 staging 调用方发现拼写错误。
