# 科技文献智能服务平台

本仓库包含平台的内容评估智能体源码，包括 Vue 前端、FastAPI 后端、MongoDB 初始化、测试和部署配置。它将项目材料、指南、查新和补充证据组织为可追溯的评估报告。

## 本地运行

需要安装并启动 Docker Desktop（Windows/macOS）或 Docker Engine 与 Compose plugin（Linux）。首次构建需要能够访问公开容器镜像和 Python/npm 包源。

基础镜像默认使用 Docker Hub 的 Python/Node/Nginx 镜像，Python 包默认使用官方 PyPI，npm 默认使用 npmmirror。网络策略不同的环境可在 `.env.local` 中设置 `PYTHON_BASE_IMAGE`、`PIP_INDEX_URL`、`NODE_BASE_IMAGE`、`NGINX_BASE_IMAGE` 和 `NPM_REGISTRY`，切换为本机可访问的镜像源。

PowerShell：

```powershell
Copy-Item .env.template .env.local
docker compose --env-file .env.local -f docker/compose.local.yml up --build -d
docker compose --env-file .env.local -f docker/compose.local.yml ps
```

macOS/Linux：

```sh
cp .env.template .env.local
docker compose --env-file .env.local -f docker/compose.local.yml up --build -d
docker compose --env-file .env.local -f docker/compose.local.yml ps
```

Compose 只读取 `.env.local`，不会把仓库已有的 `.env` 中其他配置带入容器。需要启用云端模型或其他外部 API 时，在 `.env.local` 中填写对应地址和密钥。

服务健康后访问：

- 前端：<http://localhost:18093/ai/agent/content_assessment/>
- 后端 API 文档：<http://localhost:17607/docs>
- 后端健康：<http://localhost:17607/system/health>
- MongoDB 健康：<http://localhost:17607/system/health/mongo>

本地演示登录为 `admin` / `123456`，仅用于本地开发，不可作为生产账号。MongoDB、日志和上传内容使用 Docker 命名卷持久化。停止服务运行 `docker compose --env-file .env.local -f docker/compose.local.yml down`；删除数据库及其他卷会丢失本地数据，只有明确需要清空时才运行 `down --volumes`。

MongoDB 数据库名默认为 `content_assessment_agent`。首次创建数据卷时 Compose 自动执行 `database/init/001_indexes.js`。数据库结构、检查、索引重建和数据清理说明见 [database/README.md](database/README.md)。

## 外部服务配置

默认本地环境不访问 NSTL 内部服务。服务启动和页面打开不要求模型或文献检索 API；没有配置模型时后端跳过模型连通性预检，模型生成能力不可用或按业务规则降级。需要启用云端模型时，在本地 `.env.local` 配置 `CONTENT_ASSESSMENT_LLM_ENABLED=true`、`CONTENT_ASSESSMENT_LLM_API_URL`、`CONTENT_ASSESSMENT_LLM_API_KEY` 和 `CONTENT_ASSESSMENT_LLM_MODEL_NAME`，再重启服务。密钥只保存在本地 `.env.local`，不得提交。

需要文献检索时配置 `LITERATURE_SEARCH_API_URL`。上传文件的敏感内容校验默认关闭配置；因为校验采用 fail-closed 策略，上传能力需先配置 `SENSITIVE_CHECK_API_URL` 并确保服务可访问。外部服务不可达不会阻止 Docker 栈和前端启动，但相应业务能力会不可用或降级。

`.env.template` 提供本地 Docker 栈的启动默认值；`.env.example` 是通用后端配置参考；`.env.test.example` 用于 NSTL 测试部署。不要将 `.env.local`、`.env`、`.env.test` 或任何备份凭据文件提交到 Git。

## 开发验证

```sh
python -m pytest -q
ruff check backend
cd frontend/web
npm ci
npm run typecheck
npm run build
npm run security:render-sinks
```

Compose 文件语法检查：

```sh
docker compose --env-file .env.local -f docker/compose.local.yml config --quiet
```

## 目录

- `backend/`：FastAPI、智能体、数据访问和服务
- `frontend/web/`：Vue 前端
- `database/`：MongoDB 初始化脚本和本地数据库说明
- `docker/`：本地/测试 Compose、镜像构建和 Nginx 配置
- `docs/`：开发、测试和部署文档
- `tests/`：后端和前端自动化测试
