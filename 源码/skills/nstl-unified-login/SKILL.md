---
name: nstl-unified-login
description: "NSTL智能体平台统一登录认证正式接入技能。用于新智能体接入或审查现有智能体的NSTL统一认证，涵盖login.nstl.gov.cn iframe登录、/api/service/nstl/web/vilidate/isLogin登录态校验、微信扫码父窗口回调、X-User-Scope用户作用域透传、后端user_scope数据隔离、本地*.nstl.gov.cn HTTPS联调、Vite/Nginx代理配置。只允许真实NSTL认证，不实现、不保留、不依赖本地假登录、测试账号登录或default用户作用域。"
---

# NSTL统一登录

本技能面向项目组内部开发者，用于把 NSTL 智能体平台统一登录能力复用到新的或已有的智能体项目中。调用本技能后，应能仅依赖本技能内的说明、模板和验收清单完成统一登录接入，不需要再去查本地设计文档或其他项目源码。

## 使用入口

- 给开发者快速查看复用方式时，先读 `README.md`。
- 需要完整设计、接入步骤和验收标准时，读 `references/unified-login-guide.md`。
- 需要落地代码时，优先复用 `assets/templates/` 下的模板：
  - `nstlAuth.ts`：前端统一登录管理器。
  - `main.ts.example`：前端入口初始化示例。
  - `api-client.ts.example`：业务请求携带 `X-User-Scope` 示例。
  - `backend_auth.py`：FastAPI 后端解析 `X-User-Scope` 和用户隔离示例。
  - `vite.config.ts.example`：本地 NSTL HTTPS 和代理配置示例。
  - `nginx.conf.snippet`：生产 Nginx 代理片段。

## 固定规则（6条强制规则）

本技能定位为**正式NSTL统一认证接入方案**，以下6条规则在接入中必须无条件遵守：

1. **只接受 isLogin 返回的真实用户身份**：登录成功唯一判据是 `/api/service/nstl/web/vilidate/isLogin` 返回 `code === 0` 且解析出真实 NSTL `userId`，不接受任何本地假用户、占位值或绕过逻辑。
2. **用户相关业务请求必须携带真实 X-User-Scope**：所有涉及用户数据的请求必须通过 `requireUserScope()` 获取真实用户后再携带 `X-User-Scope` 头，禁止硬编码或从本地假登录模块获取。
3. **后端必须拒绝空值和非真实用户作用域**：`user_scope` 为空或明显不是 NSTL 真实用户时，后端必须返回 401，不得以任何兜底逻辑放行。
4. **前端在用户相关操作前必须调用 requireUserScope()**：创建任务、访问个人历史、写入用户数据等操作前，必须先调用 `getAuthManager().requireUserScope()`，确保已获取真实用户身份后才继续。
5. **禁止本地假登录和 default 用户作用域**：不得实现、保留或依赖本地假登录、测试账号登录、本地生成 token、硬编码用户或 `default` 用户作用域。
6. **审计中发现假登录必须报告并移除**：审查时若发现本地假登录、`default` 用户作用域或测试账号硬编码，必须作为阻断问题报告并要求移除。

### 核心接入锚点

- 登录 iframe：`https://login.nstl.gov.cn`
- 登录态校验：`GET /api/service/nstl/web/vilidate/isLogin`
- 业务用户作用域：`X-User-Scope: <真实 NSTL userId>`
- 后端数据隔离：所有用户数据按 `user_scope` 写入、查询、更新、删除。
- 本地完整联调：必须在 HTTPS 的 `*.nstl.gov.cn` 子域下进行。

注意：接口路径里的 `vilidate` 是 NSTL 当前真实接口拼写，接入时不要改成 `validate`。

## 执行流程

1. 判断任务类型：新接入、审查现有登录、排查登录问题。
2. 读取 `README.md` 明确复用方法；复杂或不确定时继续读取 `references/unified-login-guide.md`。
3. 复制或对照模板实现前端登录、业务请求头、后端用户隔离和代理配置。
4. 只替换项目自身信息：智能体路由前缀、后端地址、端口、本地域名、storage key、仓储或集合名称。
5. 按参考手册中的验收清单逐项验证，未通过时继续修正。

## 前端实现清单

- 从 `assets/templates/nstlAuth.ts` 创建或更新 `src/services/nstlAuth.ts`。
- 在 `main.ts` 中初始化 `createAuthManager(...)`。
- 生产登录按钮调用 `getAuthManager().showLoginModal()`。
- 创建任务、访问个人历史或写入用户数据前调用 `getAuthManager().checkAuth()`。
- 业务请求必须通过 `requireUserScope()` 获取真实用户作用域。
- 用户相关操作前必须调用 `getAuthManager().requireUserScope()`，获取真实用户身份后才继续。
- 所有用户相关业务请求都携带 `X-User-Scope`。
- 真实登录联调必须使用 HTTPS 的 `*.nstl.gov.cn` 域名。

## 后端实现清单

- 所有用户相关接口读取 `X-User-Scope`。
- 后端拒绝空值和非真实用户作用域，返回 401。
- 创建用户数据时写入 `user_scope`。
- 列表接口按 `user_scope` 查询。
- 详情、更新、删除接口按 `task_id + user_scope` 查询。
- 不得有本地假登录、测试账号登录、本地生成 token 或任何兜底放行逻辑。

## 代理配置清单

- 将 `/api/service/nstl/web/vilidate/isLogin` 代理到 `https://www.nstl.gov.cn/api/service/nstl/web/vilidate/isLogin`。
- 代理必须转发 Cookie，并允许响应透传 `Set-Cookie`。
- 业务接口应挂在同一个可信前端网关下。
- 生产环境必须使用 HTTPS 和 `nstl.gov.cn` 体系下的域名。

## 审查清单

审查已有项目时，优先报告这些问题：

- `main.ts` 是否真正初始化 NSTL 登录管理器。
- 业务请求是否通过 `requireUserScope()` 获取真实用户作用域。
- 登录成功是否只以 `isLogin` 返回真实用户为准。
- 微信扫码是否暴露 `wxRegist`、`WxRegist`、`initWeichatLogin`。
- 后端生产环境是否拒绝占位用户作用域。
- 任务列表、详情、继续执行、删除是否都带 `user_scope` 过滤。
- Vite 和 Nginx 是否正确代理 `isLogin`。
- 是否存在本地假登录、`default` 用户作用域或测试账号硬编码。
- 发现本地假登录或 `default` 时，是否作为阻断问题报告并要求移除。

## 完成标准

只有同时满足以下条件，才算完成统一登录复用：

- 未登录用户在创建用户数据前会触发 NSTL 登录 iframe。
- 短信登录和微信扫码登录都必须等到 `isLogin` 返回 `code === 0` 且解析出真实 `userId` 后才结束。
- 业务请求携带真实用户的 `X-User-Scope`。
- `X-User-Scope` 为空或非真实用户时返回 401。
- 用户 A 不能列表、读取、更新、继续执行或删除用户 B 的任务。
- 不存在本地假登录、测试账号登录、本地生成 token 和 `default` 用户作用域。
