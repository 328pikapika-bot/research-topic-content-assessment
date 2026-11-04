# 本地 MongoDB

本项目使用 MongoDB 7 保存任务历史、任务事件、上传文件元数据和反馈。全新数据库由 Docker Compose 创建，不需要手动导入数据；首次创建数据卷时会自动执行 `init/001_indexes.js` 建立索引。

## 启动

在仓库根目录将 `.env.template` 复制为 `.env.local` 后，按根目录 [README](../README.md) 启动 `docker/compose.local.yml`。MongoDB 容器只向宿主机发布 `127.0.0.1` 可访问的本地端口，后端通过 Compose 网络连接 `mongo:27017`。

默认数据库为 `content_assessment_agent`，认证库为 `admin`。数据库文件保存在 Compose 管理的 `mongo-data` 命名卷中。`docker compose down` 会停止服务并保留数据。

## 检查

```powershell
docker compose --env-file .env.local -f docker/compose.local.yml ps
docker compose --env-file .env.local -f docker/compose.local.yml exec mongo mongosh --quiet --username admin --password --authenticationDatabase admin content_assessment_agent --eval "db.getCollectionNames()"
docker compose --env-file .env.local -f docker/compose.local.yml exec mongo mongosh --quiet --username admin --password --authenticationDatabase admin content_assessment_agent --eval "db.content_assessment_tasks.getIndexes().map(x => x.name)"
```

macOS/Linux 命令相同；MongoDB 会交互式提示输入 `.env` 中配置的密码。

Compose 健康检查会等待 MongoDB 认证 ping 成功后再启动后端。后端健康接口为 `http://localhost:17607/system/health/mongo`。

## 重建索引

Mongo 官方镜像只在空数据卷首次初始化时自动运行初始化目录。若已有数据卷需要重新确保索引，可在仓库根目录运行：

```powershell
docker compose --env-file .env.local -f docker/compose.local.yml exec mongo mongosh --quiet --username admin --password --authenticationDatabase admin content_assessment_agent /docker-entrypoint-initdb.d/001_indexes.js
```

脚本使用固定索引名和定义，可重复执行；不会清除集合内容。

## 删除本地数据库

只有明确要删除本机所有项目数据时才运行：

```powershell
docker compose --env-file .env.local -f docker/compose.local.yml down --volumes
```

删除卷不可恢复。需要保留数据时不要添加 `--volumes`。
