# stockfund-panel

**HamizDev 独立维护 · MIT 开源。** stockfund-panel 是股票与基金数据看板，提供自选、行情与 K 线、策略筛选、回测、基金中心及可选的 AI 分析。项目以本地运行为主，数据和密钥由使用者自行管理。

## 功能

| 模块 | 当前能力 |
| --- | --- |
| 股票看板 | 市场概览、自选、K 线、指数、资金与数据同步状态 |
| 策略研究 | 条件选股、历史回测、策略命中追踪与个股分析 |
| 基金中心 | 基金搜索、自选与持仓、净值和历史业绩筛选；部分数据取决于已配置的数据源 |
| AI 选股 | 按已运行的日线策略生成候选；配置模型后可进行个股分析 |
| AI 选基 | 按历史收益区间获取研究候选，再由已配置的模型辅助比较；会显示可用数据的缺口 |
| 模拟盘 | 手动虚拟账户与每个策略独立的虚拟账户；策略信号可自动生成**模拟**订单，统计收益、胜率和净值 |
| 数字资产模拟 | 币安现货/合约、Bitget USDT 合约的免费公开行情驱动独立策略账户；均线趋势、通道突破和 1/5/10/20 倍对照；仅研究模拟，默认暂停，详见[运行口径](docs/automatic-paper.md) |
| 数据源 | TickFlow、扶摇及可选的插件或自定义数据源；各来源的权限和覆盖范围不同 |

选股和选基结果用于研究。页面会标出行情时间、来源和已知缺失字段；不要把历史收益、策略命中或模型文字当成交易结论。

## 🚀 快速开始

<p align="center"><strong>已安装 Docker？从源码构建并启动 ⬇️</strong></p>

```bash
git clone https://github.com/HamizDev/stockfund-panel.git
cd stockfund-panel
cp .env.example .env
docker compose up --build -d
```

**打开 <http://127.0.0.1:3018> 即可访问。** 上面是 macOS/Linux 命令；Windows PowerShell 请看方式 A。宿主机需要 Git 和 Docker Compose，无须另装 Python 或 Node.js。当前没有本项目的 GHCR 现成镜像，Compose 会在本机从源码构建；不要使用其他项目的镜像。

| 方式 | 适合谁 | 前置要求 |
| --- | --- | --- |
| **A · Compose 本地构建** ⭐ 推荐 | 想运行看板，不打算改源码 | Git、Docker Desktop 或 Docker Engine + Compose 插件 |
| **B · 本机 AI 代部署** | 希望 AI 编程助手检查环境、执行部署步骤 | 能操作本机文件和终端的 AI 编程助手；由其确认所需依赖 |
| **C · Dev 模式** | 修改前后端、调试和二次开发 | Git、Python ≥ 3.11、Node.js ≥ 20、pnpm 9、uv |

### 方式 A：Docker Compose 本地构建

在 Windows PowerShell 中逐条执行：

```powershell
git clone https://github.com/HamizDev/stockfund-panel.git
Set-Location stockfund-panel
Copy-Item .env.example .env
docker compose up --build -d
```

macOS/Linux 使用上方的快速启动命令。若宿主机端口 `3018` 已被占用，**先**在新仓库的 `.env` 中修改 `PORT`，再运行最后一条命令；不要停止已有服务。默认 `BIND_HOST=127.0.0.1`，只允许本机访问。运行数据保存在新仓库的 `data/`，不会写入 Git；密钥由使用者在设置页或自己的 `.env` 中填写。

镜像默认不安装可选的 `stock-sdk` 插件，也不挂载宿主机 Codex 登录文件。要在看板中使用本机 Codex CLI，建议采用方式 C，并在运行看板的同一用户环境中登录；外部 AI API 可在设置页单独配置。

### 方式 B：本机 AI 代部署

把下面整段提示词交给**能够操作你本机文件和终端**的 AI 编程助手。它会先检查环境和已有服务，再向你确认具体操作；无需把 API Key、密码或 Codex 登录文件粘贴进对话。

```text
请在我的电脑上部署 https://github.com/HamizDev/stockfund-panel ，优先使用仓库自带的 Docker Compose 本地构建。

先只读检查操作系统、Git、Docker、目标目录和端口 3018，并在线阅读仓库的 README.md、docs/deployment.md、docker-compose.yml 与 .env.example。列出准备执行的命令、目标目录、端口和对现有服务的影响，得到我确认后再克隆仓库、安装依赖、创建配置或启动服务。

如果目标目录已有 .env 或 data/，保留它们并先给出备份方案；如果 3018 被占用，给新部署选其他端口，不要停止或覆盖已有看板。不要重装或重置 Docker/WSL，不要清理容器、镜像或数据，也不要修改其他项目。

密钥和登录信息由我在本机设置页填写；不要索取、打印、上传或提交 API Key、密码、私钥、Codex 登录文件或个人数据。完成后检查 docker compose ps、/health 和首页 HTTP 响应，告诉我实际访问地址、数据目录、已启用功能及仍需我配置的项目。若 Docker 不可用，先说明原因和可选的本地开发方案，不要自行修复系统环境。
```

AI 助手需要本机文件和终端权限才能代为执行；只有聊天能力的助手可以指导你，但无法直接完成安装。首次启用数据源和 AI 服务仍需要你自己的账号或 Key。

### 方式 C：Dev 模式

需要 Python 3.11+、Node.js 20+、pnpm 9 和 uv。先克隆仓库并从 `.env.example` 建立自己的 `.env`；然后在 Windows 运行 `./dev.ps1`，在 macOS/Linux 运行 `./dev.sh`。例如，Windows 上已有服务占用默认端口时：

```powershell
Copy-Item .env.example .env
./dev.ps1 -BackendPort 3020 -FrontendPort 3021
```

开发脚本遇到端口占用会安全退出，不会结束已有服务。前后端分别监听脚本输出的端口。单独验证构建时，在 `frontend/` 执行 `pnpm install --frozen-lockfile` 与 `pnpm build`；在 `backend/` 执行 `uv sync --frozen --extra dev` 与 `uv run --frozen pytest -q tests`。

## 数据源与密钥

没有任何个人账号、API Key、持仓或历史行情随源码提供。首次启动后，进入 **设置 → 数据源**，按需要配置：

| 来源 | 获取方式 | 说明 |
| --- | --- | --- |
| [TickFlow](https://tickflow.org/) | 在其官网注册并获取 API Key | 股票数据能力随账号档位变化；部分历史日线能力可在未填 Key 时使用 |
| [扶摇](https://fuyao.aicubes.cn/docs/quickstart/) | 登录扶摇后，在 API Key 管理页创建 Key | 基金及部分增强行情接口需要 Key；未配置时相关请求可能显示 503 |
| AkShare | 见 [AkShare 项目](https://github.com/akfamily/akshare) | `bin/akshare-proxy.py` 是可选的本地基金数据辅助服务，需自行安装 `akshare`；目前仅支持与后端在同一主机运行。默认 Docker Compose 无法连接宿主机代理；基金榜单和单位净值有东方财富直接回退，其他资料仍取决于可用来源 |
| 东方财富 / 天天基金公开档案 | [免费基金研究数据](docs/fund-public-data.md) | 无需 Key：费率条件、披露股票持仓、相关报告公告、区间观测回撤和单位净值；不保证实时、完整或持续可用 |
| `stock-sdk` 插件 | 见 `backend/app/plugins/stocksdk/` | 可选插件，不随 Docker 默认构建安装；使用前核对数据来源条款 |
| 市场资金流、概念与行业 | [shy313.com](https://shy313.com/) 的公开接口 | 对应页面和预设数据会请求 `shy313.com/api/plugins/market_flow/exports`；接口失败时相关内容可能为空 |
| 币安公开行情 | [币安开发者文档](https://developers.binance.com/en/docs/products/spot/rest-api) | 数字资产研究模拟仅访问公开现货及 U 本位合约行情，无需 Key；接口可用性和合约风险口径见 [数据源状态](docs/data-source-status.md) |
| Bitget 公开行情 | [Bitget 官方合约文档](https://www.bitget.com/docs/catalog/classic-contract-market/classic-contract-market) | USDT 合约自动研究模拟使用公开报价、K 线和资金费率，无需 Key；不连接交易所账户，详见[运行口径](docs/automatic-paper.md) |

密钥可以在看板设置页填写，也可按 `.env.example` 的字段配置 `.env`。实际密钥文件、`data/` 和本地日志均不应提交。免费或公开网页数据可能延迟、限流或变更接口；软件不会保证实时性或完整性。

## AI 分析

进入 **设置 → AI** 选择服务。支持 OpenAI 兼容接口与本机 Codex CLI 模式；具体模型需由使用者在自己的服务中选择和授权。

- **Codex CLI**：先按 [Codex 官方登录说明](https://learn.chatgpt.com/docs/auth) 在运行看板的同一用户环境完成 `codex login`，再在设置页选择 Codex CLI。登录态属于个人敏感信息，不在本项目中分发。Docker 默认不提供主机登录态，因此不能仅因镜像中有 `codex` 命令就认为已连接。
- **API 模式**：到所选服务的官方网站申请 API Key，在设置页或自己的 `.env` 中配置地址、模型和 Key。仓库只提供空白示例，不附带可用额度或账号。

AI 选股的候选来自已有策略计算，不是模型自行发现的股票；模型负责可选的个股分析。AI 选基先读取历史业绩候选，再补充免费公开费率、观测回撤、披露持仓和日期后生成对比文字。回撤窗口、持仓报告期、公告日期与缺口分别标注；统一榜单统计截止日、实时完整持仓等未公开信息不会推测补齐。详见 [数据口径](docs/fund-public-data.md)。

## 数据与安全

- `.env`、`data/`、`secrets.json`、Codex 登录态、个人持仓和本地备份属于运行时资料，应留在自己的电脑或服务器。
- 默认只在 `127.0.0.1` 发布 Docker 端口。对外开放前设置访问密码，并自行配置 HTTPS、网络访问控制和备份。
- 更新源码或重建镜像前，先备份自己的 `.env` 和 `data/`。不要把备份提交到 GitHub。
- 当前仓库不包含作者本机的历史行情库、基金持仓、扶摇 Key 或 Codex 账号。

## 项目结构

```text
backend/               FastAPI API、数据与策略服务
frontend/              React / TypeScript 前端
backend/app/custom/    后端扩展（含基金、AI 选股）
frontend/src/custom/   前端扩展（含基金、AI 选基）
bin/                   可选的 AkShare 本地辅助服务
docs/                  使用与二次开发文档
```

改动代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；扩展机制和版本升级注意事项见 [docs/secondary-development.md](docs/secondary-development.md)。

## 许可证与来源

项目代码按 [MIT License](LICENSE) 发布。本仓库由 HamizDev 独立维护；[LICENSE](LICENSE) 保留适用于仓库代码的版权与许可声明。本项目与 TickFlow、扶摇、AkShare、OpenAI 等数据或 AI 服务方无官方隶属关系；这些名称用于说明可选集成。第三方依赖和数据接口仍须遵守各自许可与服务条款。
