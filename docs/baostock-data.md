# BaoStock 复权因子插件

`baostock` 是一个免费、可选的数据源插件，只声明 `adj_factor` 能力。它只接受
`600519.SH`、`000001.SZ` 形式的沪深股票代码，不提供 ETF、北交所、指数、日线、分钟线或
全市场分钟数据。BaoStock Python 包未安装时，插件在数据源设置中显示为不可用并提供依赖
安装入口；缺少此包不会阻止应用启动。单次请求最多接受 100 个标的；插件拆成每批至多 40
个标的的独立子进程取数，并为每批设置有界超时。

当前插件用于限定标的查询。现有全市场因子同步入口可能一次传入全部证券，因此不能直接将
本插件设为全市场默认源：超过 100 个标的或包含未支持的证券会明确报错。批量支持与证券覆盖
完善前，应保留原默认源，并按受支持的标的清单分别查询。

## 上游字段与标准化

插件调用 BaoStock `query_adjust_factor(code, start_date, end_date)`。实测响应字段为
`code`、`dividOperateDate`、`foreAdjustFactor`、`backAdjustFactor`、`adjustFactor`；代码
格式为 `sh.600519` / `sz.000001`。API 日期范围两端均包含。

内部标准输出为 `symbol, trade_date, ex_factor`。插件以 `backAdjustFactor` 作为累计后复权
因子，按事件日期升序，用当前累计值除以前一事件的累计值推导单次事件倍率。上市日的
`backAdjustFactor=1` 是基准水平，不作为除权事件输出。只有完成全部历史的比值推导后，才按
请求窗口筛选事件。查询固定从 `1990-01-01` 开始，即使调用方只请求较晚日期，也会读取窗口
首个事件之前的因子水平。

选择 `backAdjustFactor` 有实时字段证据：BaoStock 0.9.4 对 `sh.600519` 的
`2023-06-30` 记录返回 `backAdjustFactor=6.889798`，但同一行 `adjustFactor=0.988591`；
`2023-07-03` 又返回二者均为 `6.889798`。因此不能把 `adjustFactor` 整列当作累计水平。
官方《BaoStock 复权因子简介》定义后复权因子为前一日收盘价除以除权日的前收盘价，且
上市首日后复权因子为 1；事件间累计水平的比值对应单次事件倍率。

该输出是价格复权事件倍率，不包含实际现金分红金额、送转股数量等行动明细，也不构成完整的
现金流或证券行动账本。当前仅对沪深股票的 BaoStock 返回值做严格校验，不承诺 ETF、北交所
或所有证券行动类型的完整覆盖。

2026-10-01 的隔离只读验证使用 `baostock` 0.9.4 查询 `sh.600519`，范围
`1990-01-01` 至 `2026-10-01`：返回 31 行，日期为 `2001-08-27` 至 `2026-06-26`。首行
`backAdjustFactor=1.000000` 是上市基准；首个后续事件 `2002-07-25` 的累计值为
`1.118280`，前值为 `1.000000`，导出的 `ex_factor=1.118280`。末行累计值
`7.669257`，前一事件累计值 `7.491968`，导出的 `ex_factor=1.0236638758`。此检查只读取
公开行情，不写本地业务数据。

输出因子必须是有限正数。响应代码与请求代码不符、字段缺失、查询状态失败、无效日期或
因子、同日冲突记录均作为错误返回。完全相同的同日记录折叠为一条；成功查询但没有事件时，
返回具有固定列和类型的空 DataFrame。

## 登录与超时

BaoStock SDK 的登录和 socket 状态是模块级状态。插件在进程内串行化查询，并把 SDK 会话放进
有界超时的子进程，每批最多 40 个标的。每个子进程只登录一次、串行查询该批标的，并在
`finally` 中调用登出；
若查询超过上限，父进程终止子进程，操作系统回收其 socket。插件不修改 Python 全局 socket
默认超时。

## 安装

在数据源设置页使用插件的依赖安装操作。手动安装时，从仓库 `backend` 目录安装清单：

```powershell
uv pip install --python .venv\Scripts\python.exe -r app\plugins\baostock\requirements.txt
```

macOS/Linux 可将解释器路径替换为 `.venv/bin/python`。插件不需要账号密钥。

## 参考

- [BaoStock 官方网站](https://www.baostock.com/)
- [BaoStock Python API 文档](http://www.baostock.com/baostock/index.php/Python_API文档)
- [BaoStock 复权因子简介（官方 PDF）](https://www.baostock.com/helpdocs/pdf/BaoStock%E5%A4%8D%E6%9D%83%E5%9B%A0%E5%AD%90%E7%AE%80%E4%BB%8B.pdf)
