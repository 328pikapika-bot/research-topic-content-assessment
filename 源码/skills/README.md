# Skills 一览

本项目 `skills` 目录存放了团队复用的技能包，供智能体（Codex/Claude）在对应场景下自动加载执行。

---

## 技能列表

### 1. context-skill
**上下文增量同步** — 将用户原话、模型处理逻辑、执行动作、结果和文件变更，按"逐问题单元"持续增量写入 `E:\all-context` 下的 Markdown 档案文件，不覆盖历史。适用于需要跨会话沉淀对话记录、回看某个具体问题处理过程的场景。

### 2. docker-image-packaging
**Docker 镜像打包** — 为前端 Vue 项目（Nginx 部署）和后端 Java/Python 服务项目生成标准化的 Docker 打包文件，包括 `Dockerfile`、`docker-compose.yml` 编排文件，并统一处理时区、远程构建命令提示等。

### 3. nstl-literature-retrieval
**NSTL 统一文献检索接入** — 提供可复用的文献检索模块接入方案。业务代码只需构造 `SearchRequest`，即可通过统一检索网关完成文献搜索，无需直连底层 Milvus/Solr/RRF/Reranker，六步完成接入。

### 4. nstl-unified-login
**NSTL 统一登录认证接入** — 面向新智能体的 NSTL 平台统一登录接入技能。涵盖 `login.nstl.gov.cn` iframe 登录、登录态校验、微信扫码回调、`X-User-Scope` 用户作用域透传、后端数据隔离、本地 HTTPS 联调及 Vite/Nginx 代理配置。

### 5. python-comment-guidelines
**Python 注释规范** — goodIdea 项目的 Python 代码注释编写规范。适用于 FastAPI 路由、配置、仓库层、Agent 节点、脚本、测试等的注释编写与审查。强调用中文简洁说明"为什么这样做"，不重复解释语法本身。

### 6. talk-skill
**对话整理工具** — 将用户与 AI 的历史对话、问题讨论、方案推导和处理结论整理为结构化 Markdown 文档。保留 AI 原文、添加 AI 思路摘要，区分纯讨论与含代码变更的讨论，便于复盘和知识沉淀。

---

## 目录说明

- `nstl-skills/` 目录下为部分 NSTL 相关技能的副本，内容与根级同名技能保持一致。
- 每个 skill 目录下包含 `SKILL.md`（技能定义文件），部分还包含 `README.md`、`scripts/`（执行脚本）、`assets/templates/`（代码模板）、`references/`（参考文档）等。
