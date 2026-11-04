# NSTL统一认证接入技能

本目录是项目组内部复用的 Codex skill，定位为**正式NSTL统一认证接入方案**。开发者或智能体只要调用 `$nstl-unified-login`，再按本目录内说明和模板落地代码，即可完成 NSTL 统一登录、用户作用域透传和后端数据隔离。该 skill 只允许真实 NSTL 认证，不实现、不保留、不依赖本地假登录、测试账号登录或 `default` 用户作用域。

## 什么时候使用

- 新智能体需要接入 NSTL 统一登录认证。
- 现有智能体需要审查登录接入完整性（isLogin、X-User-Scope、user_scope 隔离等）。
- 登录后业务请求需要统一携带 `X-User-Scope`。
- 后端任务、历史记录、用户数据需要按 `user_scope` 隔离。
- 本地或生产环境需要配置 `isLogin` 代理、Vite HTTPS、Nginx 代理。

## 复用逻辑

统一登录复用固定分成四步：

1. 前端接入 `nstlAuth.ts`，通过 iframe 打开 `https://login.nstl.gov.cn`。
2. 登录完成后只信任 `/api/service/nstl/web/vilidate/isLogin` 返回的真实 NSTL 用户。
3. 所有用户相关业务请求携带 `X-User-Scope: <真实 NSTL userId>`。
4. 后端所有用户数据按 `user_scope` 写入和查询，详情、更新、删除必须同时校验业务 ID 和 `user_scope`。

正式链路不能用本地假用户、测试账号、本地生成 token 或 `default` 用户作为登录成功依据。审查已有项目时，一旦发现这些残留，应作为阻断问题报告并移除。

## 目录说明

- `SKILL.md`：skill 触发后给 Codex 读取的主流程说明。
- `references/unified-login-guide.md`：完整接入手册，包含接口、步骤、验收和常见问题。
- `assets/templates/nstlAuth.ts`：前端统一登录管理器模板。
- `assets/templates/main.ts.example`：前端入口初始化示例。
- `assets/templates/api-client.ts.example`：业务 API 请求头示例。
- `assets/templates/backend_auth.py`：后端用户作用域解析和隔离示例。
- `assets/templates/vite.config.ts.example`：本地 HTTPS 与代理配置示例。
- `assets/templates/nginx.conf.snippet`：生产 Nginx 代理片段。

## 推荐调用方式

在开发新智能体时，可以直接这样要求 Codex：

```text
使用 $nstl-unified-login 为当前智能体接入 NSTL 统一登录，确保业务请求携带真实 X-User-Scope，后端按 user_scope 隔离数据，并移除本地假登录、测试账号登录、本地生成 token 和 default 用户作用域。
```

在审查已有智能体时，可以这样要求：

```text
使用 $nstl-unified-login 检查当前项目的统一登录接入是否完整，重点检查 isLogin、微信扫码回调、X-User-Scope、user_scope 隔离，以及是否残留本地假登录或 default 用户作用域。
```

## 验收口径

接入完成后必须满足：

- 页面通过 NSTL 登录 iframe 登录。
- 登录成功只以 `isLogin` 返回真实用户为准。
- 业务请求携带真实用户的 `X-User-Scope`。
- 后端拒绝空值和非真实用户作用域。
- 项目中不存在本地假登录、测试账号登录、本地生成 token 和 `default` 用户作用域。
- 不同用户不能互相查看、继续、更新或删除对方数据。
- 本地真实登录使用 HTTPS 的 `*.nstl.gov.cn` 子域联调。

## 内部使用边界

本 skill 只面向项目组内部开发使用。为了方便复制到不同智能体项目，文档和模板不写入个人机器路径、固定本地域名、未脱敏凭据或某个具体业务智能体的源码路径。项目自身的端口、域名、路由前缀和仓储名称，由接入时按实际情况替换。
