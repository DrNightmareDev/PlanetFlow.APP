# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

适用于 EVE Online 的自托管行星工业管理面板。

如果这个项目对你有帮助，欢迎向 `DrNightmare` 发送游戏内 ISK 赞助。

## 功能

- 管理主号与小号的 PI 殖民地，支持无限账号和角色
- **Celery + RabbitMQ 后台刷新** — ESI 数据每 30 分钟在后台自动更新；仪表盘始终从缓存即时加载
- **基于 ETag 的 ESI 缓存** — 未变化的行星返回 HTTP 304，跳过重新处理（首次运行后约减少 60–70% 的 ESI 请求）
- 对市场价格、仪表盘数值、Skyhook 数值、ETag 响应、GUI 翻译和静态行星信息使用数据库缓存
- 通过 Celery Beat 自动执行 15 分钟市场价格刷新和 30 分钟殖民地刷新
- 仪表盘支持状态筛选、ISK/天、到期提醒、提取器平衡指示、提取速率筛选、Tier 筛选、自动刷新倒计时和 Dotlan 链接
- **PI 模板** — 保存、分享和导入殖民地布局，支持等比例 Canvas 渲染和 GitHub 社区模板导入
- Skyhook 库存支持历史记录与数据库价值缓存
- 角色页面支持卡片视图与列表视图下的 PI 技能展示
- 包含 Corporation、System Analyzer、Compare、System Mix 和 PI Chain Planner
- Manager 页面支持数据库中的 GUI 翻译管理，语言包含德语、英语和简体中文
- 可选 **Sentry** 错误追踪和 **Flower** Celery 任务监控

## 界面页面

- `Dashboard`：显示全部 PI 殖民地、每日 ISK 价值、到期时间、仓储状态、Skyhook 信息、自动刷新倒计时，以及活跃、已过期、停滞、平衡、失衡、提取速率和 Tier 筛选。
- `PI Templates`：殖民地布局 Canvas 编辑器，支持等比例行星渲染，并可从 GitHub 社区源（DalShooth、TheLegi0n-NBI）导入。
- `Skyhooks`：按行星编辑并保存 Skyhook 库存，同时查看历史记录和缓存价值。
- `Characters`：显示所有已绑定角色、主号/小号关系、Token 状态，以及卡片和列表视图下的 PI 技能。
- `Corporation`：汇总自己军团的 PI 数据，并显示主角色、殖民地、PI 类型和跨军团殖民地的产品搜索。
- `Jita Market`：显示来自缓存的 Jita / The Forge 市场数据中的 PI 产品买价、卖价、价差、趋势和成交量。
- `PI Chain Planner`：构建 P1-P4 完整生产链，并显示所需行星类型、P0 原料和适合的星系。
- `System Analyzer`：分析单个星系，显示可用行星类型、P0 资源、推荐的 PI 产品，以及可展开的行星详情表（包含行星编号和半径）。
- `System Mix`：组合多个星系或星座，显示在共享行星组合下可以生产哪些 PI 产品。
- `Compare`：将多个星系并排比较，直接对照行星类型和 PI 推荐。

## 所需 ESI Scopes

```
esi-planets.manage_planets.v1
esi-planets.read_customs_offices.v1
esi-location.read_location.v1
esi-search.search_structures.v1
esi-characters.read_corporation_roles.v1
esi-skills.read_skills.v1
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
```

### 可选集成

```env
SENTRY_DSN=          # Sentry 错误追踪，留空禁用
FLOWER_USER=admin    # Flower 任务监控用户名（--profile monitoring）
FLOWER_PASS=change_me_flower
NGINX_PORT=80        # nginx 端口（--profile nginx）
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
bash scripts/update_linux.sh --compose
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
| `eve-pi-manager-beat` | Celery Beat 调度器（每 30 分钟触发刷新）|

### 从旧版本升级

```bash
sudo bash scripts/upgrade_to_latest.sh
```

自动处理：安装 RabbitMQ、补全 `.env` 缺失配置项、将 Web 服务从 uvicorn 升级到 gunicorn、创建 Celery systemd 单元、执行 pip install 和数据库迁移、重启所有服务。

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

## Windows 原生运行

注意：Windows 脚本不配置 RabbitMQ 和 Celery，应用将回退到 APScheduler（单进程）。生产环境推荐使用 Linux 或 Docker。

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
→ {"status": "ok", "database": "ok", "rabbitmq": "ok"}
```

## 管理员脚本

```bash
cd /opt/eve-pi-manager
./scripts/add_administrator.py --name "Character Name"
./scripts/add_administrator.py --eve-id 123456789
./scripts/remove_administrator.py --name "Character Name"
./scripts/remove_administrator.py --eve-id 123456789
```

## 翻译

- GUI 翻译从数据库表 `translation_entries` 加载
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
