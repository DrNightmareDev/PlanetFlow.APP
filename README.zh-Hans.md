# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

适用于 EVE Online 的自托管行星工业管理面板。

如果这个项目对你有帮助，欢迎向 `DrNightmare` 发送游戏内 ISK 赞助。

---

## 功能

- 管理主号与小号的 PI 殖民地，支持无限账号和角色
- **Celery + RabbitMQ 后台刷新** — ESI 数据每 5 分钟在后台自动更新；仪表盘始终从缓存即时加载
- **基于 ETag 的 ESI 缓存** — 未变化的行星返回 HTTP 304，跳过重新处理（首次运行后约减少 60–70% 的 ESI 请求）
- 对市场价格、仪表盘数据、Skyhook 数值、ETag 响应、GUI 翻译和静态行星信息使用数据库缓存
- 通过 Celery Beat 自动执行 15 分钟市场价格刷新和 5 分钟殖民地刷新
- 仪表盘支持状态筛选、ISK/天、**实时到期倒计时**（每分钟在浏览器中更新，无需刷新页面）、提取器平衡指示、提取速率筛选、Tier 筛选、自动刷新倒计时和 Dotlan 链接
- **分页** — 客户端分页（默认每页 50 条，可配置为全部显示），适用于大型殖民地列表
- **Discord / Webhook 提醒** — 通过 Discord Webhook 进行服务端殖民地到期提醒，每账号可配置，带冷却时间；自动处理 Discord 频率限制（429）
- **Token 状态概览** — 仪表盘横幅及角色详情页显示过期/缺失 Token 状态，24 小时后自动重试；横幅仅在真实鉴权问题时显示，已恢复的错误不会触发
- **Corporation 页面异步** — 无缓存的 Corp 账号自动触发 Celery 后台刷新
- **CSV 导出** — 从仪表盘下载完整殖民地列表 CSV
- **移动端视图** — 小屏幕下的紧凑表格布局，支持横向滚动
- **PI 模板** — 保存、分享和导入殖民地布局，支持等比例 Canvas 渲染和 GitHub 社区模板导入
- Skyhook 库存支持历史记录与数据库价值缓存
- 角色页面支持卡片视图与列表视图下的 PI 技能展示
- 包含 Corporation、System Analyzer、Compare、System Mix 和 PI Chain Planner
- Manager 页面支持数据库中的 GUI 翻译管理，语言包含德语、英语和简体中文
- 可选 **Sentry** 错误追踪和 **Flower** Celery 任务监控

## 界面页面

| 页面 | 描述 |
|---|---|
| `Dashboard` | 全部 PI 殖民地、每日 ISK 价值、实时到期倒计时、仓储状态、Skyhook 信息、自动刷新倒计时，以及活跃/已过期/停滞/平衡/失衡/提取速率筛选 |
| `PI Templates` | 殖民地布局 Canvas 编辑器，支持等比例行星渲染，并可从 GitHub 社区源导入 |
| `Skyhooks` | 按行星编辑并保存 Skyhook 库存，查看历史记录和缓存价值 |
| `Characters` | 所有已绑定角色、主号/小号关系、Token 状态，以及卡片和列表视图下的 PI 技能 |
| `Corporation` | 军团 PI 数据汇总：主角色、殖民地、PI 类型、产品搜索 |
| `Jita Market` | 来自缓存 Jita 市场数据的 PI 产品买价、卖价、价差、趋势和成交量 |
| `PI Chain Planner` | 构建 P1-P4 完整生产链，显示所需行星类型、P0 原料和适合的星系 |
| `System Analyzer` | 分析单个星系：行星类型、P0 资源、PI 推荐、行星详情 |
| `System Mix` | 组合多个星系或星座，显示可生产的 PI 产品 |
| `Compare` | 将多个星系并排比较 |
| `Fittings` | 并排比较所有角色的 ESI 装配（需要 `esi-fittings.read_fittings.v1` 授权）|

## 所需 ESI Scopes

```
esi-planets.manage_planets.v1
esi-planets.read_customs_offices.v1
esi-location.read_location.v1
esi-search.search_structures.v1
esi-characters.read_corporation_roles.v1
esi-skills.read_skills.v1
```

可选（用于装配比较）：
```
esi-fittings.read_fittings.v1
```

## 快速开始

```bash
git clone https://github.com/DrNightmareDev/PI_Manager.git
cd PI_Manager
cp .env.example .env
```

然后填写 `.env`，再根据环境选择 Docker Compose、Linux 或 Windows 原生部署。

## 配置 `.env`

### 必填项

```env
DATABASE_URL=postgresql://evepi:PASSWORD@localhost/evepi
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=http://YOUR-IP-OR-DOMAIN/auth/callback
SECRET_KEY=a_long_random_secret_with_at_least_32_characters
```

- `EVE_CLIENT_ID` / `EVE_CLIENT_SECRET`：从 [CCP Developer Portal](https://developers.eveonline.com) 获取
- `EVE_CALLBACK_URL`：必须与 CCP 应用中配置的回调地址完全一致
- `SECRET_KEY`：用于会话签名 — 必须替换为安全的随机值（至少 32 个字符）。若保留默认值，应用将拒绝启动。

> **注意：** 若 `EVE_CLIENT_ID`、`EVE_CLIENT_SECRET` 或 `SECRET_KEY` 未配置，应用将拒绝启动。

### RabbitMQ / Celery（后台刷新所需）

```env
RABBITMQ_USER=evepi
RABBITMQ_PASS=change_me_rabbit
CELERY_BROKER_URL=amqp://evepi:change_me_rabbit@rabbitmq:5672//
```

- Docker Compose 使用 `@rabbitmq:5672`，Linux 原生安装使用 `@localhost:5672`
- `CELERY_BROKER_URL` 留空则使用 APScheduler 回退模式（单进程，不推荐用于大型军团）

### 性能

```env
# 每个 gunicorn Worker 加载完整应用（约 400-500 MB）
# 2 GB 内存建议 2 个 Worker，4 GB 以上可用 2-4 个
WEB_WORKERS=2

# 数据库连接池（可选，默认值对小型实例已足够）
DB_POOL_SIZE=5
DB_POOL_OVERFLOW=10
DB_POOL_RECYCLE=3600
```

### 可选集成

```env
JANICE_API_KEY=

# Sentry 错误追踪，留空禁用
SENTRY_DSN=

# Flower 任务监控用户名（--profile monitoring）
FLOWER_USER=admin
FLOWER_PASS=change_me_flower

# nginx 端口（--profile nginx）
NGINX_PORT=80
```

### 完整示例

```env
DATABASE_URL=postgresql://evepi:supersecret@localhost/evepi
DB_PASSWORD=supersecret
EVE_CLIENT_ID=1234567890abcdef
EVE_CLIENT_SECRET=abcdef1234567890
EVE_CALLBACK_URL=http://192.168.2.44/auth/callback
EVE_SCOPES=esi-planets.manage_planets.v1 esi-planets.read_customs_offices.v1 esi-location.read_location.v1 esi-search.search_structures.v1 esi-characters.read_corporation_roles.v1 esi-skills.read_skills.v1
SECRET_KEY=replace_me_with_a_long_random_secret_key
APP_PORT=8000
DEBUG=false
WEB_WORKERS=2
RABBITMQ_USER=evepi
RABBITMQ_PASS=supersecret_rabbit
CELERY_BROKER_URL=amqp://evepi:supersecret_rabbit@localhost:5672//
SENTRY_DSN=
```

## Docker Compose

### 启动

```bash
docker compose up -d
```

启动核心组件：**PostgreSQL**、**RabbitMQ**、**Web 应用**（gunicorn）、**Celery Worker** 和 **Celery Beat** 调度器。

### 可选 Profile

| Profile | 命令 | 用途 |
|---|---|---|
| `nginx` | `--profile nginx` | 内置 nginx 反向代理（已有 nginx 则跳过）|
| `pgbouncer` | `--profile pgbouncer` | PgBouncer 连接池（超大型部署）|
| `monitoring` | `--profile monitoring` | Flower Celery 任务监控（localhost:5555）|

示例：
```bash
docker compose --profile nginx up -d
```

### 更新

```bash
bash scripts/update_compose.sh
```

常用参数：

```bash
bash scripts/update_compose.sh --branch main
bash scripts/update_compose.sh --no-pull
```

该脚本会更新 Git 检出、拉取或构建镜像、重启整个栈，并在 `app` 容器内执行 Alembic 迁移。

或手动更新：
```bash
git pull origin main
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

### 日志

```bash
docker compose logs -f app
docker compose logs -f celery_worker
docker compose logs -f celery_beat
```

### 管理员脚本

```bash
docker compose exec app python /app/scripts/add_administrator.py --name "Character Name"
docker compose exec app python /app/scripts/add_administrator.py --eve-id 123456789
docker compose exec app python /app/scripts/remove_administrator.py --name "Character Name"
docker compose exec app python /app/scripts/remove_administrator.py --eve-id 123456789
```

## Linux 原生

### 全新安装

```bash
sudo bash scripts/setup_linux.sh
```

自动安装配置：PostgreSQL、RabbitMQ、Python venv、Alembic 迁移，以及三个 systemd 服务：

| 服务 | 描述 |
|---|---|
| `eve-pi-manager` | Web 应用（gunicorn）|
| `eve-pi-manager-worker` | Celery Worker（ESI 后台刷新）|
| `eve-pi-manager-beat` | Celery Beat 调度器（每 5 分钟触发刷新）|

### 从旧版本升级

```bash
sudo bash scripts/upgrade_to_latest.sh
```

自动处理：
- 安装 RabbitMQ（如缺失）
- 补全 `.env` 缺失配置项，不修改已有值
- 将 Web 服务从 uvicorn 升级到 gunicorn（如需要）
- 创建 Celery Worker 和 Beat systemd 单元
- 执行 `pip install` 和 `alembic upgrade head`
- 重启所有服务

### 常规更新

```bash
sudo bash scripts/update_linux.sh
```

### 查看服务状态

```bash
systemctl status eve-pi-manager eve-pi-manager-worker eve-pi-manager-beat
```

### 日志

```bash
journalctl -u eve-pi-manager -f
journalctl -u eve-pi-manager-worker -f
journalctl -u eve-pi-manager-beat -f
```

### 内存参考

| 服务器内存 | WEB_WORKERS |
|---|---|
| 1 GB | 1 |
| 2 GB | 2 |
| 4 GB+ | 2–4 |

## Windows 原生运行

前提条件：
- Python 3.11+
- PostgreSQL（本地或外部）
- 已填写的 `.env`

> 注意：Windows 脚本不配置 RabbitMQ 和 Celery，应用将回退到 APScheduler（单进程）。生产环境推荐使用 Linux 或 Docker。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

更新：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

## 健康检查

```
GET /health
```

返回 PostgreSQL 和 RabbitMQ 状态：

```json
{
  "status": "ok",
  "database": "ok",
  "rabbitmq": "ok"
}
```

该端点同时用于 `app` 容器的 Docker 健康检查。

## 架构

```
Browser
  └─► nginx (可选) ──► gunicorn (2–4 Worker)
                            └─► FastAPI / Jinja2
                            └─► PostgreSQL (SQLAlchemy 2.0)

RabbitMQ ──► Celery Worker (并发数 4，prefetch=1)
                └─► ESI API（ETag 缓存，304 感知，Error-Budget 保护）
                └─► DashboardCache 表（PostgreSQL）

Celery Beat ──► auto_refresh_stale_accounts  （每 5 分钟）
            └─► refresh_market_prices_task   （每 15 分钟）
            └─► send_webhook_alerts_task     （每 15 分钟）
            └─► cleanup_sso_states_task      （每 1 小时）
```

**仪表盘加载流程：**
1. 请求到达 gunicorn — 从数据库读取 `DashboardCache`（快速，无 ESI）
2. 缓存缺失或过期：派发 Celery 任务，显示加载动画
3. JS 每 3 秒轮询 `/dashboard/refresh-status?since=<timestamp>`
4. Celery Worker 完成并写入缓存后，轮询检测到更新并重新加载
5. 后续请求直接从缓存提供

**ESI 错误处理：**
- Token 刷新失败：最多 3 次重试，指数退避（2s、4s）；401/403 立即视为永久失败
- ESI Error Budget：每次调用后检查 `X-ESI-Error-Limit-Remain` 响应头；低于 20 时等待 10s
- 连续错误 >= 3 次的角色将被跳过 24 小时，之后自动重置

## Manager 管理面板

Manager 面板（`/manager`）供管理员使用：

- **账号管理**：查看所有账号和角色，授予/撤销 Manager 权限，删除账号，模拟账号
- **访问策略**：为军团和联盟配置白名单或黑名单
- **重置 ESI 错误**：ESI 错误的角色以红色徽章显示；↺ 按钮立即重置 `esi_consecutive_errors`，无需等待
- **重载殖民地缓存**：手动为任意账号刷新仪表盘缓存
- **翻译管理**：直接在 Manager 中编辑自定义 GUI 翻译

## 安全说明

- **Cookie**：会话 Cookie 设置了 `httponly`、`samesite=lax`，在生产环境（`DEBUG=false`）下自动启用 `secure`
- **Webhook**：仅接受 Discord Webhook URL（`discord.com/api/webhooks/…`），其他 URL 在服务端被拒绝
- **启动校验**：若 `SECRET_KEY`、`EVE_CLIENT_ID` 或 `EVE_CLIENT_SECRET` 未配置，应用拒绝启动
- **Docker**：应用容器以非特权用户 `appuser`（uid 1000）运行
- **错误信息**：原始异常详情不会发送到浏览器，所有错误均在服务端记录日志

## 管理员脚本

```bash
cd /opt/eve-pi-manager
./scripts/add_administrator.py --name "Character Name"
./scripts/add_administrator.py --eve-id 123456789
./scripts/remove_administrator.py --name "Character Name"
./scripts/remove_administrator.py --eve-id 123456789
```

- `add_administrator.py` 授予 `Administrator` 和 `Manager` 角色
- `remove_administrator.py` 移除这些角色

## 翻译

- GUI 翻译从数据库表 `translation_entries` 加载（内存缓存，修改时自动失效）
- `app/locales/` 中的种子文件提供初始内容
- 官方 PI 产品名称从 EVE SDE（`types.json`）导入
- 静态行星详情（行星编号、半径）来自 SDE 宇宙数据
- `type.<id>.name` 等 SDE 条目在 Manager 中为只读

## 技术栈

| 组件 | 技术 |
|---|---|
| Web 框架 | FastAPI + Jinja2 |
| 数据库 | PostgreSQL + SQLAlchemy 2.0 + Alembic |
| 后台任务 | Celery 5 + RabbitMQ |
| Web 服务器 | gunicorn + UvicornWorker |
| 前端 | Bootstrap 5 |
| ESI 缓存 | ETag / If-None-Match（HTTP 304）|
| 开发回退 | APScheduler（无需 RabbitMQ）|
| 错误追踪 | Sentry SDK（可选）|
| 任务监控 | Flower（可选，`--profile monitoring`）|

## CCP 声明

EVE Online 及其相关标志与设计均为 CCP ehf. 的商标或注册商标。本项目与 CCP ehf. 没有任何关联，也未获得 CCP ehf. 的认可或支持。

## 许可证

MIT。见 [LICENSE](LICENSE)。
