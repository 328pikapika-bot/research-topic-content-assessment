---
name: python-comment-guidelines
description: Clear Chinese commenting rules for Python code in the goodIdea project. Use when Codex writes, modifies, reviews, or refactors Python files, including FastAPI routes, config, repositories, agent nodes, scripts, tests, bug fixes, MongoDB logic, NSTL authentication logic, user-scope isolation, model-output normalization, or any request to add, improve, or clean Python comments.
---

# Python 注释规范

## Core Rule

注释要帮助后续维护者快速理解“这段代码为什么这样做”，不要重复解释语法本身。

优先使用中文注释，风格保持简洁、准确、自然。注释不要写得太复杂，也不要为了加注释而给每一行都写说明。

如果一次任务同时修改前端和后端，只把本规范应用到 Python 部分。

## Module Overview

新增重要 Python 文件时，在文件顶部加入模块 docstring，说明文件职责、边界和关键约束。

已有 Python 文件如果已经有模块 docstring，修改代码时检查它是否仍然准确；如果本次改动明显改变文件职责，要同步更新。

已有 Python 文件如果没有模块 docstring，只有在本次改动较大、文件职责不够直观时补充，不要为了形式主义改动很小的文件。

示例：

```python
"""
好问题任务历史仓储。

封装 MongoDB 读写，所有查询都必须携带 user_scope，
避免不同 NSTL 用户之间的任务历史互相可见。
"""
```

## Functions And Classes

为复杂函数、公共函数、类、跨模块调用入口写 docstring。重点说明：

- 这个函数或类负责什么
- 关键输入代表什么
- 返回值或副作用是什么
- 是否有重要业务约束

简单的私有小函数如果名称已经足够清楚，可以不写 docstring。

示例：

```python
def ping(self) -> dict[str, Any]:
    """返回 MongoDB 连接诊断信息，供本地联调和健康检查使用。"""
```

## Intent Comments

遇到复杂业务规则、异常兼容、认证链路、数据库隔离、模型输出容错时，在逻辑块前加一行短注释说明意图。

示例：

```python
# 大模型有时会把列表字段返回成编号字符串，这里先归一化，避免格式漂移导致接口 500。
items = normalize_string_list(raw_items)
```

```python
# 生产环境必须拒绝 default，避免未登录用户共享同一份任务历史。
if not ALLOW_DEFAULT_USER_SCOPE and user_scope in BLOCKED_SCOPES:
    raise HTTPException(status_code=401, detail="请先登录")
```

## High-Value Targets

优先注释这些地方：

- NSTL 登录、扫码回调、用户作用域等认证逻辑
- MongoDB 连接、健康检查、用户数据隔离
- 大模型输出格式兼容和兜底处理
- 后台任务、异步流程、错误恢复流程
- 不直观的业务规则和历史兼容逻辑
- 为了安全、性能或稳定性做出的特殊判断

## Avoid

避免这些低价值注释：

```python
# 设置变量
count = 0

# 遍历列表
for item in items:
    ...

# 返回结果
return result
```

也不要写过长、过泛、和代码不同步的注释。注释不能替代清晰的命名和结构；如果代码本身混乱，优先适度整理代码。

## Final Check

完成 Python 代码修改后，快速检查：

- 新增或修改的重要文件是否需要模块总览 docstring
- 复杂函数是否有简短 docstring
- 复杂逻辑是否有说明“为什么”的注释
- 注释是否和最新代码一致
- 是否存在明显废话注释或过度注释

保持注释少而准，让下次维护的人能更快进入上下文。
