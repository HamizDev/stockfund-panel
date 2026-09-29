# 部署指南

以下说明适用于 `HamizDev/stockfund-panel` 源码。当前以本地构建为准；不要把上游 `tick-stock-panel` 的镜像当作本项目镜像。配置字段见 [configuration.md](configuration.md)。

## Docker Compose

```bash
git clone https://github.com/HamizDev/stockfund-panel.git
cd stockfund-panel
cp .env.example .env
docker compose up --build -d
```

Windows PowerShell 把复制命令改为 `Copy-Item .env.example .env`。默认打开 <http://127.0.0.1:3018>。端口已被占用时，在 `.env` 里修改 `PORT`。`BIND_HOST=127.0.0.1` 限制宿主机只在本机发布端口；容器内部仍监听 `0.0.0.0`。运行数据在仓库的 `data/` 目录，配置与密钥在自己的 `.env` 或运行时数据中。

Docker 构建默认不安装 `stock-sdk` 的抓取依赖。如需自行启用，可使用 `docker compose build --build-arg INCLUDE_STOCKSDK=1`，并确认所使用数据接口的服务条款。Docker 镜像不包含或自动挂载 Codex 登录态。

## 本地开发

需要 Python 3.11+、Node.js 20、pnpm 9、uv。在根目录复制 `.env.example` 为 `.env` 后：

```bash
./dev.sh
```

Windows 使用 `./dev.ps1`。目标端口已被占用时，开发脚本会安全退出并提示另选端口，例如 `./dev.ps1 -BackendPort 3020 -FrontendPort 3021`。本地 Python 环境可使用当前 Windows 用户登录的 Codex CLI；AI 设置页仍需明确选择 Codex CLI 并通过连接测试。

## 老 CPU 兼容(avx2/fma 缺失)

若 CPU 不支持 AVX2/FMA，可在 `.env` 设置 `BACKEND_EXTRAS=legacy-cpu` 后重新安装或构建依赖；需要回测时设置 `BACKEND_EXTRAS=legacy-cpu backtest`。这只用于指令集不兼容的旧 CPU，不能作为 WSL、内存或文件系统故障的修复方式。

## 更新代码(已部署用户必读)

先备份 `.env` 和整个 `data/`。然后拉取代码并重新构建：

```bash
git pull
docker compose up --build -d
```

本地开发方式拉取后重新运行开发脚本。`data/` 被 Git 忽略，但 `git clean -fdx` 会删除它；不要在含有真实数据的工作目录使用该命令。现有数据的迁移应以备份副本核对后进行。

## 访问密码设置(公网部署必读)

只在本机使用时保留默认 `BIND_HOST=127.0.0.1`。确需让局域网或公网访问时，先在 `.env` 设置一次性初始化的 `AUTH_PASSWORD`，启动后确认登录生效，再调整 `BIND_HOST` 并配置 HTTPS、防火墙和访问控制。已有密码应从页面修改；更改 `.env` 中的初始化密码不会覆盖已保存的密码。

```ini
AUTH_PASSWORD='自行设置的强密码'
```

不得把真实密码、API Key、`secrets.json`、`~/.codex/auth.json` 或个人持仓提交到 GitHub。更多字段说明见 [configuration.md](configuration.md)。
