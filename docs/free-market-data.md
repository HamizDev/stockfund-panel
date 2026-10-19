# 免费官方研究数据

本批保留看板现有布局、行情源和账户配置，新增的数据只用于页面研究，未接入自动下单规则。

## 可用位置

- **A股看板 → 人民币资金利率**：中国货币网 / 中国外汇交易中心公开的 FR001、FR007、FR014。保留原始数据日、上一观测日及变化基点。FR 是银行间回购定盘利率，不能当作 ETF 回报率或券商融资报价。
- **美股看板 → 美债收益率**：美国财政部日度国债平价收益率，展示 1个月至30年的选定期限，以及10年减2年的利差。曲线期限等距排列，逐项数值以卡片为准。
- **A股 / ETF 分析 → ETF官方份额**：选定沪深 ETF 时按需查看交易所公布的份额，单位为万份。上交所支持指定日期；深交所只提供当前快照，保留来源日期。份额变化不等于资金净流入，也不能单凭份额判定涨跌。
- **数据设置 → BaoStock**：扩展既有可选插件的原始日线能力，实际支持范围、数量限制及未接入项见 [BaoStock说明](baostock-data.md)。默认数据源保持原配置。

## 缓存与失败行为

利率独立于首页核心行情请求，一小时内复用后端共享缓存；页面切换使用现有 TanStack Query。源异常时可保留一天内的旧缓存并明确显示“旧缓存”和原始数据日期。冷启动读不到数据会直接显示不可用，不生成零值。

`percent` 数值 `5.09` 表示 **5.09%**，与股票涨跌幅使用的小数口径不同。1 bp = 0.01个百分点。所有日期均是来源观测日，抓取时间另存，不冒充实时报价。

## 参考与归属

本批从下列固定版本核对官方端点与字段，按看板已有服务、缓存和类型合同适配实现；没有把外部 Skill 安装进系统或执行其中的提示词。

- [simonlin1212/global-stock-data](https://github.com/simonlin1212/global-stock-data/tree/cc6afb5bad45715a7395fcccb09d786d8a1c160d)，参考美国财政部收益率入口。
- [simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data/tree/29b07d2e37005b9fd568bca1262f6e572b5f9407)，参考中国货币网回购定盘利率与沪深交易所 ETF 份额入口及校验思路。

上述项目采用 Apache-2.0；保留其[许可文本](licenses/market-data-apache-2.0.txt)及本处来源记录。看板自身许可保持不变。代码许可与各数据源的使用条款分别适用。

官方来源：[美国财政部](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve)、[中国货币网](https://www.chinamoney.com.cn/chinese/bkfrr/)、[上交所基金](https://www.sse.com.cn/assortment/fund/)、[深交所基金](https://fund.szse.cn/)。无需付费 API Key，不把本机获取的数据打包到公开仓库。

## 本批未启用

纽约联储接口在本机实测连接超时；SEC EDGAR 尚未配置其要求的真实联系声明；期权、做空余额、逐笔及其他抓取入口未默认启用。项目列出的接口数量不代表本看板已经实现的能力。港股看板不混入人民币定盘利率作为港元基准。

上游 tick-stock-panel 的 enriched 缓存预热有限重试已按本地实现适配，避免发布代际切换丢弃预热后一直保持冷缓存。此改动在后台执行，不在 HTTP 请求中循环重试。
