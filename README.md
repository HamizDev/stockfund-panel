# stockfund-panel

股票与基金数据看板，提供自选、行情与 K 线、策略筛选、回测、基金中心及可选的 AI 分析。项目以本地运行为主，数据和密钥由使用者自行管理。

> 本项目基于 [tick-stock-panel](https://github.com/shy3130/tick-stock-panel) 开发。原项目及本项目的版权声明见 [LICENSE](LICENSE)。行情数据和 AI 服务分别受各自提供方的条款约束。

## 功能

| 模块 | 当前能力 |
| --- | --- |
| 股票看板 | 市场概览、自选、K 线、指数、资金与数据同步状态 |
| 策略研究 | 条件选股、历史回测、策略命中追踪与个股分析 |
| 基金中心 | 基金搜索、自选与持仓、净值和历史业绩筛选；部分数据取决于已配置的数据源 |
| AI 选股 | 按已运行的日线策略生成候选；配置模型后可进行个股分析 |
| AI 选基 | 按历史收益区间获取研究候选，再由已配置的模型辅助比较；会显示可用数据的缺口 |
| 数据源 | TickFlow、扶摇及可选的插件或自定义数据源；各来源的权限和覆盖范围不同 |

选股和选基结果用于研究。页面会标出行情时间、来源和已知缺失字段；不要把历史收益、策略命中或模型文字当成交易结论。

## 快速开始

### Docker Compose

需要 Docker Desktop 或 Docker Engine。克隆仓库后先创建自己的配置文件：

```bash
git clone https://github.com/HamizDev/stockfund-panel.git
cd stockfund-panel
cp .env.example .env
docker compose up --build -d
```

Windows PowerShell 中使用 `Copy-Item .env.example .env`。默认打开 <http://127.0.0.1:3018>。Compose 默认只向宿主机回环地址发布端口，运行数据保存在项目的 `data/`，不会写入 Git。启动前检查端口 3018 是否已被其他服务使用；需要改端口时修改 `.env` 中的 `PORT`。需要让其他设备访问时，先设置访问密码，再自行调整 `BIND_HOST` 与网络防火墙。

Docker 镜像默认不安装可选的 `stock-sdk` 数据插件，也不会挂载宿主机 Codex 登录文件。AI 分析可在设置页配置外部 API；使用本机 Codex CLI 时，建议按下述本地开发方式在已登录的同一用户环境中运行。不要把 `~/.codex/auth.json` 复制进镜像或提交到仓库。

### 本地开发

需要 Python 3.11+、Node.js 20、pnpm 9 和 uv。Windows 可运行 `./dev.ps1`，macOS/Linux 可运行 `./dev.sh`。脚本发现后端或前端端口已被占用时会安全退出并提示更换端口，不会结束已有服务。

```powershell
Copy-Item .env.example .env
./dev.ps1 -BackendPort 3020 -FrontendPort 3021
```

开发模式后端和前端分别监听脚本输出的端口。需要单独检查构建时，在 `frontend/` 执行 `pnpm install --frozen-lockfile` 与 `pnpm build`；在 `backend/` 执行 `uv sync --frozen --extra dev` 与 `uv run --frozen pytest -q tests`。

## 数据源与密钥

没有任何个人账号、API Key、持仓或历史行情随源码提供。首次启动后，进入 **设置 → 数据源**，按需要配置：

| 来源 | 获取方式 | 说明 |
| --- | --- | --- |
| [TickFlow](https://tickflow.org/) | 在其官网注册并获取 API Key | 股票数据能力随账号档位变化；部分历史日线能力可在未填 Key 时使用 |
| [扶摇](https://fuyao.aicubes.cn/docs/quickstart/) | 登录扶摇后，在 API Key 管理页创建 Key | 基金及部分增强行情接口需要 Key；未配置时相关请求可能显示 503 |
| AkShare | 见 [AkShare 项目](https://github.com/akfamily/akshare) | `bin/akshare-proxy.py` 是可选的本地基金数据辅助服务，需自行安装 `akshare`；目前仅支持与后端在同一主机运行。默认 Docker Compose 无法连接宿主机上的代理，AI 选基的部分榜单会因此不可用；第三方接口可用性会变化 |
| `stock-sdk` 插件 | 见 `backend/app/plugins/stocksdk/` | 可选插件，不随 Docker 默认构建安装；使用前核对数据来源条款 |
| 市场资金流、概念与行业 | [shy313.com](https://shy313.com/) 的公开接口 | 对应页面和预设数据会请求 `shy313.com/api/plugins/market_flow/exports`；接口失败时相关内容可能为空 |

密钥可以在看板设置页填写，也可按 `.env.example` 的字段配置 `.env`。实际密钥文件、`data/` 和本地日志均不应提交。免费或公开网页数据可能延迟、限流或变更接口；软件不会保证实时性或完整性。

## AI 分析

进入 **设置 → AI** 选择服务。支持 OpenAI 兼容接口与本机 Codex CLI 模式；具体模型需由使用者在自己的服务中选择和授权。

- **Codex CLI**：先按 [Codex 官方登录说明](https://learn.chatgpt.com/docs/auth) 在运行看板的同一用户环境完成 `codex login`，再在设置页选择 Codex CLI。登录态属于个人敏感信息，不在本项目中分发。Docker 默认不提供主机登录态，因此不能仅因镜像中有 `codex` 命令就认为已连接。
- **API 模式**：到所选服务的官方网站申请 API Key，在设置页或自己的 `.env` 中配置地址、模型和 Key。仓库只提供空白示例，不附带可用额度或账号。

AI 选股的候选来自已有策略计算，不是模型自行发现的股票；模型负责可选的个股分析。AI 选基先读取历史业绩候选，再生成对比文字。基金榜单可能缺少统计截止日、费率、回撤和持仓，因此页面会提示这些缺口。

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

改动代码前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)；扩展机制和上游升级注意事项见 [docs/secondary-development.md](docs/secondary-development.md)。

## 许可证与来源

项目代码按 [MIT License](LICENSE) 发布，并保留上游 `tick-stock-panel contributors` 的版权与许可声明。本项目与 TickFlow、扶摇、AkShare、OpenAI 等数据或 AI 服务方无官方隶属关系；这些名称用于说明可选集成。第三方依赖和数据接口仍须遵守各自许可与服务条款。
