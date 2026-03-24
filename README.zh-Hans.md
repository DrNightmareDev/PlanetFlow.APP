# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

适用于 EVE Online 的自托管行星工业管理面板。

如果这个项目对你有帮助，欢迎向 `DrNightmare` 发送游戏内 ISK 赞助。

## 功能

- 主号与小号的 PI 殖民地管理
- 市场价格、仪表盘数值与 Skyhook 数值的数据库缓存
- 每 15 分钟自动刷新价格与估值数据
- 带状态筛选、ISK/天、到期提醒与 Dotlan 链接的仪表盘
- 带历史记录与数据库价值缓存的 Skyhook 库存
- 角色 PI 技能卡片视图与列表视图
- 公司总览、星系分析器、比较、星系组合、PI 生产链规划器
- 管理面板，以及基于数据库的德语、英语、简体中文界面翻译

## 所需 ESI Scope

- `esi-planets.manage_planets.v1`
- `esi-planets.read_customs_offices.v1`
- `esi-location.read_location.v1`
- `esi-search.search_structures.v1`
- `esi-characters.read_corporation_roles.v1`
- `esi-skills.read_skills.v1`

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

说明：

- `DATABASE_URL`：应用使用的 PostgreSQL 连接串
- `EVE_CLIENT_ID` / `EVE_CLIENT_SECRET`：来自 CCP Developer Portal
- `EVE_CALLBACK_URL`：必须与 CCP 应用中的回调地址完全一致
- `SECRET_KEY`：用于会话签名，必须替换为自己的安全随机值

### 常用可调项

```env
EVE_SCOPES=esi-planets.manage_planets.v1 esi-planets.read_customs_offices.v1 esi-location.read_location.v1 esi-search.search_structures.v1 esi-characters.read_corporation_roles.v1 esi-skills.read_skills.v1
APP_PORT=8000
DEBUG=false
JANICE_API_KEY=
DB_PASSWORD=
```

- `EVE_SCOPES`：登录时请求的 ESI 权限
- `APP_PORT`：本地应用端口
- `DEBUG`：仅建议开发时开启
- `JANICE_API_KEY`：可选
- `DB_PASSWORD`：主要用于 Compose 或容器场景

### 修改 `.env` 之后

- 重启服务或容器
- 如果修改了 Scope，相关角色需要重新通过 EVE SSO 授权
- 如果 `EVE_CALLBACK_URL` 或 `EVE_SCOPES` 配置错误，登录或 Scope 刷新通常会立即失败

## Docker Compose

```bash
docker compose up -d
```

更新：

```bash
git pull origin main
docker compose pull
docker compose build
docker compose up -d
docker compose exec app alembic upgrade head
```

如果你直接基于本地工作目录更新，通常下面这样就够了：

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
```

推荐顺序：

- 拉取最新代码
- 拉取或重新构建镜像
- 重启容器
- 使用 `alembic upgrade head` 执行迁移
- 然后简单检查日志

日志检查：

```bash
docker compose logs -n 100 app
```

Compose 中的管理员脚本：

```bash
docker compose exec app python /app/scripts/add_administrator.py --name "Character Name"
docker compose exec app python /app/scripts/add_administrator.py --eve-id 123456789
docker compose exec app python /app/scripts/remove_administrator.py --name "Character Name"
docker compose exec app python /app/scripts/remove_administrator.py --eve-id 123456789
```

## Linux

```bash
chmod +x scripts/setup_linux.sh
bash scripts/setup_linux.sh
```

更新：

```bash
bash ~/PI_Manager/scripts/update_linux.sh
```

## Windows 原生运行

可以，系统可以原生运行在 Windows 上。

要求：

- Python 3.11+
- 本地或远程 PostgreSQL
- 已填写好的 `.env`

安装：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

更新：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1
```

说明：

- 应用本体可以原生运行在 Windows 上
- `systemd` 等 Linux 专属组件仅适用于 Linux
- 生产环境依然更推荐 Linux 或 Docker

## 管理员脚本

在主机上直接执行：

```bash
cd /opt/eve-pi-manager
./scripts/add_administrator.py --name "Character Name"
./scripts/add_administrator.py --eve-id 123456789
./scripts/remove_administrator.py --name "Character Name"
./scripts/remove_administrator.py --eve-id 123456789
```

作用：

- `add_administrator.py`：授予 `Administrator` 与 `Manager`
- `remove_administrator.py`：移除 `Administrator` 与 `Manager`

## 翻译

- GUI 翻译从数据库表 `translation_entries` 加载
- `app/locales/` 中的种子文件提供初始内容
- 官方 PI 产品名称从 EVE SDE (`types.json`) 导入
- 例如 `type.<id>.name` 这类 API/SDE 条目在 Manager 中为只读

## 部署流程

- 快速 UI / 模板测试运行在 `192.168.2.44` (`pitest`)
- 持久修改会提交并推送到 `main`
- 之后再通过现有更新脚本同步到目标环境

## 技术栈

- Python 3.11
- FastAPI
- PostgreSQL
- SQLAlchemy 2.0
- Alembic
- Jinja2
- Bootstrap 5
- APScheduler

## 许可证

MIT。见 [LICENSE](LICENSE)。
