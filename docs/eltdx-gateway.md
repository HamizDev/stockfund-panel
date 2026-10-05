# ELTDX 单只分钟 K 研究接入

这是可选的本机研究数据源。面板只包含独立编写的 HTTP 连接器，**不包含 ELTDX 本体、上游 adapter 的源码或行情数据**，也不将其安装进面板的后端环境或容器镜像。

## 适用范围和许可

- 供个人、非商业学习和研究使用。ELTDX 3.2.2 使用 [Research-Only License](https://github.com/electkismet/eltdx/blob/v3.2.2/LICENSE)，并非 MIT；限制包括商业、生产服务以及自动交易服务。请先阅读该许可，不要用此源连接真实自动交易、提供付费数据或生产行情服务。
- 本连接器自身使用项目的 MIT 许可；该许可不会替代外部程序或数据的使用条件。
- 接入思路参考用户提供的 [eltdx-tick-adapter](https://github.com/mynameisbreak/eltdx-tick-adapter)。其分钟请求没有处理原生每页最多 800 根的边界，因此本实现直接连接固定版本的 ELTDX `/rpc`，单独做有界分页和字段校验。

## 提供哪些数据

| 项目 | 本次接入 |
| --- | --- |
| 股票、ETF 1 分钟 K | 每个请求只查一只；支持日期区间和最近 240 根试拉 |
| 分页 | 每页最多 800 根、最多 6 页；超出预算报错，不发布预算截断的数据 |
| 查询档位 | 页面收窄到最近 5 个交易日；实际历史覆盖取决于上游返回，并非保证五天都有数据 |
| 价格 | 上游显式 `adjust=none`，原始价；面板既有复权流程继续负责换算 |
| 时间、成交量 | 北京时间墙钟；`volume_lots` 保留为“手” |
| 分钟成交额 | 单位尚未有可靠文档证明，返回 `null`；不会生成虚假的成交均价线 |
| 全市场/全量分钟、实时快照、五档、财务、除权因子 | 本插件不声明这些能力，现有其他源继续负责 |

健康检查只验证本机 HTTP 进程和版本。设置页“试拉”才会访问上游并显示实际观察到的起止时间；旧交易日数据不会被标记成今天的数据。已注册插件的请求失败、资产类型不支持或返回的证券/周期/复权口径不一致时明确报错，不自动换成其他源的分钟结果。

插件未能加载或注册时，框架仍按既有偏好回退规则处理，并可能使用 TickFlow。请先确认设置页显示该插件已适配、实际分钟源为 `eltdx_gateway`，再核对试拉结果；插件加载状态不能当成行情在线证明。

## Windows 安装和启动

在项目根目录，用已有 Python 创建独立环境，然后只安装已验证的版本：

```powershell
python -m venv .eltdx-venv
.\.eltdx-venv\Scripts\python.exe -m pip install --only-binary=:all: --index-url https://pypi.org/simple "eltdx[http]==3.2.2"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-eltdx-gateway.ps1
```

脚本只在 `127.0.0.1:3022` 启动独立进程。它不会停止占用端口的其他程序、修改系统代理或执行 WSL 命令。日志和启动器 PID 写入忽略于 Git 的 `local-runtime/eltdx/`；独立环境 `.eltdx-venv/` 也不会进入 Git。

`-ExecutionPolicy Bypass` 只作用于这个 PowerShell 进程，不更改系统脚本策略。脚本启动检查不代表行情服务器可达，启动后还应在页面试拉。

Linux/macOS 也可使用独立 venv 启动上游命令：

```bash
python3 -m venv .eltdx-venv
.eltdx-venv/bin/python -m pip install --only-binary=:all: --index-url https://pypi.org/simple 'eltdx[http]==3.2.2'
.eltdx-venv/bin/eltdx-http --host 127.0.0.1 --port 3022 --timeout 2 --pool-size 2 --server-count 2 --connections-per-server 1
```

当前版本需要平台对应的可用 wheel；`--only-binary` 找不到时会停止，不自动改成源码编译。

## 在面板里选择

1. 启动独立网关和面板，进入“设置 → 数据源”。
2. 找到“通达信 ELTDX(研究分钟)”，点击股票或 ETF 试拉；分别查询单只 `600519.SH` 或 `510300.SH`，不写行情缓存。
3. 试拉成功后，只将“分钟 K”数据源改为 `eltdx_gateway`。不需要 API Key 或桌面通达信登录。
4. 保持全市场分钟历史同步、全量分钟刷新及自动分钟任务关闭。在股票/ETF 图表里按需查单只；已有本地缓存优先使用，并不会因换源自动删除或重新下载。

直接访问网关默认使用本机回环地址，连接器设置 `trust_env=False`；不会把本机 RPC 送进 HTTP 代理。外部 ELTDX 程序通过 TCP 连接其行情主站；是否可达仍取决于本机网络和上游限制，不能保证某个出口永不被限制。

需要自选端口时，可给脚本 `-Port 3023`，同时在**后端启动进程**中设置 `STOCKFUND_ELTDX_GATEWAY_URL=http://127.0.0.1:3023`。仅允许无凭据、无路径参数的回环 URL；不用把这项个人设置提交到 Git。

## 关闭与回滚

将分钟源切回原选择，再停止自己启动的独立网关进程。不会删除任何原有分钟数据、财务表或因子。重启电脑后需再次启动网关；个人部署脚本可在确认为此源时调用上述启动脚本，但项目不会自动修改用户的启动方式或默认启用批量任务。
