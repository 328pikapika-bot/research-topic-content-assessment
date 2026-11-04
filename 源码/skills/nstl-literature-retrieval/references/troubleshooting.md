# NSTL 统一文献检索排障指南

## 1. 环境变量未配置

**症状**：`LITERATURE_SEARCH_API_URL` 为空，检索直接返回 `success=False`，`search_trace` 中 `api_url_configured=False`。

**排查**：

```python
from app.common.retrieval.schemas import LITERATURE_SEARCH_API_URL
print(f"API URL: '{LITERATURE_SEARCH_API_URL}'")
```

**解决**：在 `.env` 或部署环境中配置 `LITERATURE_SEARCH_API_URL=<网关地址>`。公共模块支持多级 fallback，依次尝试以下环境变量名：

1. `LITERATURE_SEARCH_API_URL`
2. `NSTL_RETRIEVAL_API_URL`
3. `GOOD_QUESTION_MILVUS_API_URL`
4. `MILVUS_API_URL`

只要其中任一非空即可工作。

---

## 2. 网关超时

**症状**：请求耗时超过超时阈值后抛出 `requests.exceptions.ReadTimeout`，`search_trace.errors` 中包含超时信息。

**默认超时**：600 秒（10 分钟）。通过 `LITERATURE_SEARCH_TIMEOUT` 环境变量配置，同样支持多级 fallback：

1. `LITERATURE_SEARCH_TIMEOUT`
2. `NSTL_RETRIEVAL_TIMEOUT`
3. `GOOD_QUESTION_MILVUS_HTTP_TIMEOUT`
4. `MILVUS_HTTP_TIMEOUT`

**调整建议**：

- 常规检索：30~60 秒通常足够
- 大批量（paper_num=100）或复杂查询：120~300 秒
- 网络不稳定环境：可适当调高，但不宜超过 600 秒

**排障字段**：`SearchTrace.duration_ms` 记录了实际耗时（毫秒），可用于判断是网关慢还是网络慢。

---

## 3. 代理干扰

**症状**：在内网环境调用网关时，请求被系统代理拦截，导致连接超时或返回非预期响应。

**原因**：Python `requests` 库默认继承系统代理设置（`HTTP_PROXY` / `HTTPS_PROXY` 环境变量，或 Windows 系统代理）。

**解决**：公共客户端已内置代理绕过，发送请求时自动附加：

```python
proxies={"http": None, "https": None}
```

如果仍出现代理干扰，检查是否有其他代码在全局设置了 `os.environ["HTTP_PROXY"]`，或使用 `session.trust_env = False` 禁用环境代理。

---

## 4. SSL 证书问题

**症状**：`requests.exceptions.SSLError`，常见于内网自签名证书环境。

**排查**：

- 确认网关地址是否使用 HTTPS
- 内网自签名证书需将 CA 证书添加到系统信任链或设置 `REQUESTS_CA_BUNDLE` 环境变量
- 临时调试可设置 `verify=False`（不推荐用于生产环境）

**注意**：公共客户端默认 `verify=True`，如需调整应在客户端初始化时传入参数。

---

## 5. 网关返回 code != 1

**症状**：HTTP 请求成功（200），但网关返回 `code` 不为 1，`search_trace` 中 `gateway_code` 非 1。

**常见原因**：

| gateway_code | 含义 | 处理建议 |
|---|---|---|
| `-1` | 网关内部错误 | 检查 `gateway_msg`，联系网关运维 |
| `0` | 参数错误 | 检查 `gateway_msg`，确认 payload 格式正确 |
| 其他非 1 值 | 未知业务错误 | 查看网关日志，提供 `request_id` 定位 |

**排障字段**：

- `SearchTrace.gateway_code`：网关业务状态码
- `SearchTrace.gateway_msg`：网关返回的消息
- `SearchTrace.request_id`：请求追踪 ID，可提供给网关运维排查

---

## 6. 解析异常

**症状**：网关返回 code=1 且 data 非空，但解析后 `paper_num_returned` 少于 `data` 数组长度，日志中出现 "跳过无法解析的文献记录"。

**常见原因**：

- 必要字段缺失（如 `id` 为空）
- 字段类型变化（如 `year` 从整数变为字符串）
- `hasAuthor` 格式变化（预期为数组，实际为其他类型）

**parser.py 的逐项容错机制**：

- 单条解析异常不影响其他记录，异常记录被跳过并记录 warning 日志
- `_safe_int` / `_safe_float`：类型转换失败时返回 0 / 0.0
- `_parse_venue`：兼容 dict 和 string 两种 `hasSource` 格式
- `raw` 字段保留完整原始数据，可供排障深度查看

---

## 7. 检索无结果

**症状**：网关返回 code=1 但 `data` 为空数组，`paper_num_returned=0`。

**排查步骤**：

1. **检查 query 构造**：查看 `SearchTrace.question`，确认构造后的查询文本是否合理
2. **语种过滤**：`languages=["zh"]` 可能过滤掉英文文献，建议使用 `["zh", "en"]`
3. **年份范围**：`year_range` 是否过于狭窄，尝试放宽或使用默认范围
4. **网关日志**：使用 `SearchTrace.request_id` 联系网关运维查看详细检索日志
5. **query 过长**：查询文本过长可能被网关截断，检查 context 字段是否注入了过多信息

---

## 8. SearchTrace 字段解读

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | `str` | 请求唯一标识，用于跨系统追踪 |
| `source_agent` | `str` | 调用方智能体标识 |
| `query` | `str` | 原始检索查询文本 |
| `question` | `str` | 经 QueryBuilder 构造后的完整查询字符串 |
| `api_url_configured` | `bool` | 是否已配置检索网关地址，`False` 表示环境变量缺失 |
| `gateway_status_code` | `int` 或 `None` | HTTP 状态码（200、404、500 等） |
| `gateway_code` | `int` 或 `None` | 网关业务状态码，`1` 表示成功 |
| `gateway_msg` | `str` | 网关返回的消息文本 |
| `paper_num_requested` | `int` | 请求的文献数量 |
| `paper_num_returned` | `int` | 实际解析后返回的文献数量 |
| `rerank_enabled` | `bool` | 是否启用了重排序 |
| `intent_classification_enabled` | `bool` | 是否启用了意图分类 |
| `duration_ms` | `float` | 检索耗时（毫秒） |
| `errors` | `list[str]` | 错误信息列表 |
| `degradation_level` | `int` | 降级级别，见下表 |

---

## 9. 降级等级（degradation_level）含义

| degradation_level | 含义 | 说明 |
|---|---|---|
| `0` | 不适用 | 检索未执行（如 API URL 未配置），此值表示未进入降级流程 |
| `1` | 轻微降级 | 检索成功但结果部分受损（如部分记录解析失败） |
| `2` | 中度降级 | 检索成功但无结果（网关返回空 data） |
| `3` | 显著降级 | 检索失败但获得了部分有用信息（如网关返回错误但附带提示） |
| `4` | 完全降级/失败 | 检索完全失败（网络异常、超时、网关错误等），业务层应走降级路径 |

当前公共客户端在检索失败时通常标记为 `4`，业务层应根据此值决定是否使用模型降级线索。
