# PlanetFlow — 简体中文

[Deutsch](README.de.md) | [English](README.en.md) | [ZH-CN](README.zh-Hans.md)

EVE Online 自托管星球工业平台。

> **[planetflow.app](https://planetflow.app)** — 体验托管版本，或自行部署。

如果这个项目对你有帮助，欢迎向 `DrNightmare` 发送游戏内 ISK 赞助。

---

## 从 EVE PI Manager 迁移？

EVE PI Manager 已归档。PlanetFlow 是其继任者——所有功能均已移植并进行了扩展。

**数据不会自动迁移。** 两个应用使用独立的数据库。迁移过程很简单：

### 第一步 — 全新安装 PlanetFlow

按照下方的[本地安装](#本地安装个人电脑无需域名)或[服务器部署](#服务器部署带域名--https)步骤操作。PlanetFlow 是全新安装，只需要 EVE SSO 登录即可。

### 第二步 — 注册新的 EVE 开发者应用（或复用现有应用）

- 你可以复用 EVE PI Manager 的 EVE 开发者应用，只需将 **Callback URL** 更新为 PlanetFlow 的地址。
- 或者在 [https://developers.eveonline.com](https://developers.eveonline.com) 创建新应用，所需 Scopes 完全相同。

### 第三步 — 登录并重新添加角色

PlanetFlow 使用相同的 EVE SSO 流程。登录后进入 Characters 页面，重新授权每个角色。ESI 数据将在后台自动同步——殖民地、到期时间和星球数据会在几分钟内出现。

### 第四步 — 重新录入手动数据

存储在 EVE PI Manager 本地、无法从 ESI 获取的数据需要手动重新录入：
- **库存批次** — 在 Inventory 页面重新添加
- **运输路线 / 桥接连接** — 在 Hauling 中重新配置
- **Skyhook 记录** — 在 Skyhooks 中重新录入
- **PI 模板** — 在 PI Templates 中重新上传

### 第五步 — 关闭 EVE PI Manager

PlanetFlow 运行正常并完成同步后：
```bash
# 在旧的 eve-pi-manager 目录中
docker compose down
```

旧的数据库 volume 可以保留作为备份，也可以完全删除：
```bash
docker compose down -v   # 同时删除 volume，不可撤销
```

### 与 EVE PI Manager 的主要区别

| | EVE PI Manager | PlanetFlow |
|---|---|---|
| HTTPS / TLS | 可选 nginx profile | 内置（Let's Encrypt 或代理模式）|
| 计费与访问控制 | 无 | 内置 |
| 管理后台 | `/manager` | `/admin` |
| 后台 Worker | Celery + APScheduler 回退 | 仅 Celery（需要 RabbitMQ）|
| 配置键 | `CELERY_BROKER_URL` | `RABBITMQ_USER` / `RABBITMQ_PASS` |
| 本地 HTTP 模式 | `COOKIE_SECURE=false` | `COOKIE_SECURE=false` + `NGINX_MODE=proxy` |

---

## 前提条件

你只需要安装 **Docker Desktop**，其他什么都不需要。

- Windows / Mac：[https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
- Linux：通过包管理器安装 `docker` 和 `docker compose`

---

## 第一步 — 创建 EVE 开发者应用

1. 打开 [https://developers.eveonline.com](https://developers.eveonline.com) 并登录
2. 点击 **Create New Application**
3. 填写以下信息：
   - **Name：** 随意（例如 `My PlanetFlow`）
   - **Connection Type：** `Authentication & API Access`
   - **Callback URL：**
     - 本地运行：`http://localhost/auth/callback`
     - 服务器部署：`https://你的域名.com/auth/callback`
   - **Scopes** — 添加以下所有权限：
     ```
     esi-planets.manage_planets.v1
     esi-planets.read_customs_offices.v1
     esi-location.read_location.v1
     esi-characters.read_corporation_roles.v1
     esi-skills.read_skills.v1
     esi-fittings.read_fittings.v1
     ```
4. 保存并复制 **Client ID** 和 **Client Secret**

---

## 本地安装（个人电脑，无需域名）

PlanetFlow 将在你的电脑上以 `http://localhost` 运行。  
无需域名、无需 TLS 证书、无需配置 nginx。

### 1. 下载项目

```bash
git clone https://github.com/your-org/planetflow.app.git
cd planetflow.app
```

### 2. 创建配置文件

```bash
cp .env.example .env
```

用文本编辑器打开 `.env`，填写以下内容：

```env
# 内部数据库密码 — 随意设置
DB_PASSWORD=my_local_password

# 你的 EVE 角色 ID（可在 https://evewho.com 查询）
EVE_OWNER_CHARACTER_ID=123456789

# 来自你的 EVE 开发者应用（第一步）
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=http://localhost/auth/callback

# 生成随机密钥（在终端中运行）：
# python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=粘贴你生成的密钥

# 消息队列密码 — 随意设置
RABBITMQ_PASS=my_local_rabbit_password

# 本地运行重要：必须为 false（本地无 HTTPS）
COOKIE_SECURE=false

# 本地运行重要：使用 proxy 模式，无需 TLS 证书
NGINX_MODE=proxy
```

其他选项保持默认即可。

### 3. 启动

```bash
docker compose up -d
```

Docker 会自动下载和构建所有内容。第一次需要几分钟。

### 4. 在浏览器中打开

[http://localhost](http://localhost)

使用 EVE SSO 登录。第一个登录的账号自动成为 Owner（管理员）。

### 5. 停止

```bash
docker compose down
```

---

## 服务器部署（带域名 + HTTPS）

### 1. 准备服务器

在全新的 Ubuntu 服务器上（已在 Hetzner 测试）：

```bash
bash scripts/setup_hetzner.sh
```

这会自动安装 Docker 和所有依赖。

### 2. 创建配置文件

```bash
cp .env.example .env
nano .env
```

必填项：

```env
DB_PASSWORD=强密码
EVE_OWNER_CHARACTER_ID=123456789
EVE_CLIENT_ID=your_client_id
EVE_CLIENT_SECRET=your_client_secret
EVE_CALLBACK_URL=https://你的域名.com/auth/callback
SECRET_KEY=生成的随机密钥
RABBITMQ_PASS=强密码
COOKIE_SECURE=true
NGINX_MODE=https
```

### 3. 启动（自动申请 TLS 证书）

```bash
bash scripts/start.sh
```

该脚本会验证配置、申请 Let's Encrypt 证书并启动所有服务。

### 4. 更新已有部署

```bash
bash scripts/update.sh
```

---

## 授予管理员权限

首次登录后运行：

```bash
docker compose exec app python scripts/add_administrator.py
```

---

## 常用命令

```bash
# 查看日志
docker compose logs -f app
docker compose logs -f celery_worker

# 查看所有容器状态
docker compose ps

# 重启所有服务
docker compose restart

# 停止容器（数据保留在 volume 中）
docker compose down
```

---

## 常见问题

**登录失败 / Callback 报错**
- `.env` 中的 `EVE_CALLBACK_URL` 必须与 EVE 开发者门户中注册的 Callback URL 完全一致
- 本地运行时必须为 `http://localhost/auth/callback`（不是 https）
- 本地运行时 `COOKIE_SECURE` 必须为 `false`

**页面加载但没有数据**
- 检查应用日志：`docker compose logs -f app`
- 检查 Worker 日志：`docker compose logs -f celery_worker`

**端口 80 已被占用**
- 其他程序（IIS 等）正在使用端口 80，请停止它，或修改 `docker-compose.yml` 中的端口映射

**http://localhost 连接被拒绝**
- `docker compose up -d` 后请等待 30–60 秒，应用需要时间启动
- 运行 `docker compose ps` 确认所有服务显示 `healthy` 或 `running`

---

## 服务说明

| 服务 | 用途 |
|---|---|
| `db` | PostgreSQL 数据库 |
| `rabbitmq` | 后台任务消息队列 |
| `app` | Web 应用 |
| `celery_worker` | 后台任务 Worker（ESI 同步等） |
| `celery_beat` | 定时任务调度器 |
| `nginx` | Web 服务器 / 反向代理 |
| `certbot` | 自动续期 TLS 证书（仅服务器模式） |

---

## 功能概览

- Dashboard：殖民地状态、到期时间、ISK/天、CSV 导出
- Characters、Corporation、Inventory、Hauling、Intel、Killboard、Skyhooks、Templates
- PI Chain Planner、Colony Assignment Planner、System Analyzer、Compare、Fittings
- 内置 Billing 页面和基于页面的访问控制
- 支持德语、英语和简体中文界面

## 技术栈

- FastAPI + PostgreSQL + Celery + RabbitMQ + nginx
- Bootstrap 5 · 通过 Docker Compose 部署

## 许可证

MIT。详见 [LICENSE](LICENSE)。
