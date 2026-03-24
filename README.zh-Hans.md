# EVE PI Manager

[Deutsch](README.de.md) | [English](README.en.md) | [简体中文](README.zh-Hans.md)

适用于 EVE Online 的自托管行星工业管理面板。

如果这个项目对你有帮助，欢迎向 `DrNightmare` 发送游戏内 ISK 赞助。

## 功能

- 管理主号与小号的 PI 殖民地
- 对市场价格、仪表盘数值、Skyhook 数值、GUI 翻译和静态行星信息使用数据库缓存
- 每 15 分钟自动刷新价格与估值数据
- 仪表盘支持状态筛选、ISK/天、到期提醒、提取器平衡指示、提取速率筛选、Tier 筛选和 Dotlan 链接
- Skyhook 库存支持历史记录与数据库价值缓存
- 角色页面支持卡片视图与列表视图下的 PI 技能展示
- 包含 Corporation、System Analyzer、Compare、System Mix 和 PI Chain Planner
- Manager 页面支持数据库中的 GUI 翻译管理，语言包含德语、英语和简体中文

## 界面页面

- `Dashboard`：显示全部 PI 殖民地、每日 ISK 价值、到期时间、仓储状态、Skyhook 信息，以及活跃、已过期、停滞、平衡、失衡、提取速率和 Tier 筛选。
- `Skyhooks`：按行星编辑并保存 Skyhook 库存，同时查看历史记录和缓存价值。
- `Characters`：显示所有已绑定角色、主号/小号关系、Token 状态，以及卡片和列表视图下的 PI 技能。
- `Corporation`：汇总自己军团的 PI 数据，并显示主角色、殖民地、PI 类型和跨军团殖民地的产品搜索。
- `Jita Market`：显示来自缓存的 Jita / The Forge 市场数据中的 PI 产品买价、卖价、价差、趋势和成交量。
- `PI Chain Planner`：构建 P1-P4 完整生产链，并显示所需行星类型、P0 原料和适合的星系。
- `System Analyzer`：分析单个星系，显示可用行星类型、P0 资源、推荐的 PI 产品，以及可展开的行星详情表（包含行星编号和半径）。
- `System Mix`：组合多个星系或星座，显示在共享行星组合下可以生产哪些 PI 产品。
- `Compare`：将多个星系并排比较，直接对照行星类型和 PI 推荐。

## 所需 ESI Scopes

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

也可以直接使用项目自带的更新脚本：

```bash
bash scripts/update_linux.sh --compose
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

可选：

- 使用 `--branch <name>` 从其他分支更新
- 使用 `--compose` 将同一脚本用于 Docker Compose 部署

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

Windows 上的 Docker Compose 更新：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update_windows.ps1 -Compose
```

可选：

- 使用 `-Branch <name>` 从其他分支更新

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

## CCP 声明

EVE Online 及其相关标志与设计均为 CCP ehf. 的商标或注册商标。本项目与 CCP ehf. 没有任何关联，也未获得 CCP ehf. 的认可或支持。

## 许可证

MIT。见 [LICENSE](LICENSE)。
