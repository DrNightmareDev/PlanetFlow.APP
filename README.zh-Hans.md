# PlanetFlow

[Deutsch](README.de.md) | [English](README.en.md) | [ZH-CN](README.zh-Hans.md)

`PlanetFlow` 是一个面向 EVE Online 的自托管 PI 平台，在传统仪表盘基础上增加了计费、页面访问控制以及带 TLS 的生产级 Docker 部署方案。

> **[planetflow.app](https://planetflow.app)** — 体验托管版本，或直接开始自托管部署。

如果这个项目对你有帮助，欢迎向 `DrNightmare` 发送游戏内 ISK 赞助。

## 功能概览

- Dashboard：殖民地状态、到期时间、ISK/天、CSV 导出和分页
- Characters、Corporation、Inventory、Hauling、Intel、Killboard、Skyhooks、Templates
- PI Chain Planner、Colony Assignment Planner、System Analyzer、System Mix、Compare、Fittings
- 内置 Billing 页面和基于页面的访问控制
- 通过 Celery + RabbitMQ 执行后台任务
- Docker Compose 中集成 nginx + certbot 的 HTTPS 部署
- 支持德语、英语和简体中文界面

## 主要页面

- `Dashboard`
- `Characters`
- `Corporation`
- `Inventory`
- `Hauling`
- `Intel`
- `Killboard`
- `Skyhooks`
- `PI Templates`
- `Jita Market`
- `PI Chain Planner`
- `Colony Assignment Planner`
- `System Analyzer`
- `System Mix`
- `Compare`
- `Fittings`
- `Billing`
- `Admin`
- `Director`

## 必需的 ESI Scopes

```text
esi-planets.manage_planets.v1
esi-planets.read_customs_offices.v1
esi-location.read_location.v1
esi-characters.read_corporation_roles.v1
esi-skills.read_skills.v1
esi-fittings.read_fittings.v1
```

如果需要结构相关功能，建议额外启用 `esi-search.search_structures.v1`。

## 快速开始

```bash
cp .env.example .env
docker compose up -d
```

至少填写以下配置：

```env
DB_PASSWORD=change_me
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=https://planetflow.app/auth/callback
SECRET_KEY=replace_me_with_a_long_random_secret_key
RABBITMQ_PASS=change_me_rabbit
```

注意：

- 通过 HTTPS 提供服务时应设置 `COOKIE_SECURE=true`
- 默认 Compose 部署已经包含 `nginx` 和 `certbot`
- `sde_init` 会为应用用户准备可写的 SDE 缓存卷

## Docker Compose 服务

- `db`
- `rabbitmq`
- `sde_init`
- `app`
- `celery_worker`
- `celery_wallet`
- `celery_beat`
- `nginx`
- `certbot`

可选 profile：

- `pgbouncer`
- `monitoring`

常用命令：

```bash
docker compose up -d
docker compose logs -f app
docker compose logs -f celery_worker
docker compose logs -f celery_wallet
docker compose logs -f celery_beat
docker compose ps
```

## 脚本

初始化 Hetzner Ubuntu 服务器：

```bash
bash scripts/setup_hetzner.sh
```

检查配置、申请证书并启动整个栈：

```bash
bash scripts/start.sh
```

更新现有部署：

```bash
bash scripts/update.sh
```

## 管理工具

- `scripts/add_administrator.py`
- `scripts/remove_administrator.py`
- 应用内 Admin 页面可管理访问策略、账号和翻译

## 健康检查

```text
GET /health
```

该接口返回数据库和 RabbitMQ 状态，也被容器健康检查使用。

## 技术栈

- FastAPI + Jinja2
- PostgreSQL + SQLAlchemy + Alembic
- Celery + RabbitMQ
- Gunicorn / Uvicorn
- nginx + certbot
- Bootstrap 5

## License

MIT。详见 [LICENSE](LICENSE)。
