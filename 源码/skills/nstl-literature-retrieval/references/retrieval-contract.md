# NSTL 统一文献检索复用契约

本文档是项目组内部 Skill 自带的详细契约。开发人员或其他智能体只需要按本文档实现或调用共享检索模块，就可以复用 NSTL 统一检索服务。

## 1. 代码入口

新智能体优先导入：

```python
from app.common.retrieval import LiteratureSearchClient, SearchRequest
```

推荐公共实现位置：

```text
backend/app/common/retrieval
├── __init__.py
├── client.py
├── schemas.py
├── query_builder.py
├── parser.py
└── adapters/
    └── example.py
```

如果项目中已有旧兼容入口，可在迁移期保留类似导出：

```python
from app.clients.literature_search_client import LiteratureSearchClient
```

旧入口只做重新导出。新代码不要继续以旧入口为主。

## 2. 环境变量

网关地址读取优先级：

```text
LITERATURE_SEARCH_API_URL
-> NSTL_RETRIEVAL_API_URL
-> GOOD_QUESTION_MILVUS_API_URL
-> MILVUS_API_URL
```

超时读取优先级：

```text
LITERATURE_SEARCH_TIMEOUT
-> NSTL_RETRIEVAL_TIMEOUT
-> GOOD_QUESTION_MILVUS_HTTP_TIMEOUT
-> MILVUS_HTTP_TIMEOUT
-> 600
```

新代码和新文档优先使用 `LITERATURE_SEARCH_*` 或 `NSTL_RETRIEVAL_*`。`MILVUS_*` 只作为兼容旧部署的变量名。

## 3. 最小调用

```python
from app.common.retrieval import LiteratureSearchClient, SearchRequest


client = LiteratureSearchClient()
request = SearchRequest(
    query="钙钛矿太阳能电池稳定性优化",
    paper_num=20,
    year_range=(2021, 2026),
    languages=["zh", "en"],
    rerank=True,
    intent_classification=True,
    solr_type=2,
    source_agent="research_question_agent",
    user_scope="optional_user_or_org_scope",
    context={
        "topic": "钙钛矿太阳能电池",
        "evidence_purpose": "检索研究现状、方法路径、争议和可研究空白",
    },
)
response = client.search(request)

if response.success and response.papers:
    for paper in response.papers:
        print(paper.title, paper.year, paper.authors, paper.final_score)
else:
    print(response.error)
    print(response.search_trace)
```

旧式同步调用：

```python
response = LiteratureSearchClient().search_sync(
    "钙钛矿太阳能电池稳定性优化",
    paper_num=20,
    year_range=(2021, 2026),
    languages=["zh", "en"],
    source_agent="research_question_agent",
)
```

## 4. SearchRequest 字段

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `query` | `str` | 必填 | 干净、聚焦的科研问题或检索式 |
| `paper_num` | `int` | `20` | 请求篇数，客户端发送前约束到 `0-100` |
| `year_range` | `tuple[int, int]` 或 `None` | `None` | 不传时 `QueryBuilder` 可按默认年份范围构造 |
| `languages` | `list[str]` | `["zh", "en"]` | `zh`、`en`，同时存在时为中英混合 |
| `rerank` | `bool` | `True` | 是否启用网关重排 |
| `intent_classification` | `bool` | `True` | 是否启用网关意图识别 |
| `solr_type` | `int` | `2` | 现有网关关键词检索类型 |
| `user_scope` | `str` 或 `None` | `None` | 透传为 `X-User-Scope` |
| `request_id` | `str` 或 `None` | 自动 UUID | 透传为 `X-Request-Id` |
| `source_agent` | `str` | `""` | 透传为 `X-Source-Agent` 和 `log_params.id` |
| `context` | `dict` | `{}` | 业务上下文，只辅助构造检索 question |

`QueryBuilder` 推荐按顺序合并并去重：

```text
topic
section_title
candidate_question
evidence_purpose
query
```

务必保证 `query` 本身已经能独立表达检索意图。

## 5. 网关 Payload

业务代码不要自己拼 payload。公共客户端实际发送：

```json
{
  "question": "检索内容 | 语种：中英混合 | 年份：2021-2026",
  "paperNum": 20,
  "rerank": true,
  "intent_classification": true,
  "solr_type": 2,
  "log_params": {
    "id": "research_question_agent",
    "request_id": "自动或调用方传入的 request_id"
  }
}
```

请求头：

```text
X-Request-Id: <request_id>
X-Source-Agent: <source_agent 或 unknown_agent>
X-User-Scope: <user_scope，存在时才发送>
```

## 6. SearchResponse 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `success` | `bool` | NSTL 真实检索调用是否成功 |
| `total` | `int` | 解析后的 `StandardPaper` 数量 |
| `papers` | `list[StandardPaper]` | 标准化文献列表 |
| `query` | `str` | 原始检索 query |
| `question` | `str` | 实际发送给网关的 question |
| `error` | `str` 或 `None` | 失败原因 |
| `duration_ms` | `float` | 调用耗时 |
| `search_trace` | `dict` | 排障追踪信息 |

真实检索成功判据：

```python
real_retrieval_ok = response.success and len(response.papers) > 0
```

`success=True` 但 `papers=[]` 表示网关调用成功但无可用文献，业务层仍需降级、放宽条件或提示用户。

## 7. StandardPaper 字段

| 字段 | 说明 |
|---|---|
| `paper_id` | 文献 ID |
| `source` | 来源，业务映射时可加 `nstl_` 前缀 |
| `title` | 标题 |
| `authors` | 作者列表 |
| `year` | 年份，解析失败为 `0` |
| `venue` | 期刊、会议或来源名 |
| `doi` | DOI |
| `url` | 文件或详情地址 |
| `language` | 语言 |
| `abstract` | 摘要 |
| `keywords` | 关键词 |
| `doc_type` | 文献类型 |
| `index_level` | 索引级别 |
| `has_fulltext` | 是否有全文 |
| `retrieval_query` | 原始查询 |
| `retrieval_score` | 检索分 |
| `rrf_score` | RRF 分 |
| `quality_score` | 质量分 |
| `final_score` | 最终分 |
| `rerank_score` | 语义重排分，可能为 `None` |
| `citation_count` | 引用量 |
| `references` | 参考文献列表 |
| `raw` | 原始记录，排障使用 |

## 8. SearchTrace 字段

常用排障字段：

| 字段 | 说明 |
|---|---|
| `api_url_configured` | 是否配置网关地址 |
| `gateway_status_code` | HTTP 状态码 |
| `gateway_code` | 网关业务码，当前 `1` 表示成功 |
| `gateway_msg` | 网关消息 |
| `paper_num_requested` | 实际请求篇数 |
| `paper_num_returned` | 解析后返回篇数 |
| `rerank_enabled` | 是否启用重排 |
| `intent_classification_enabled` | 是否启用意图识别 |
| `duration_ms` | 耗时 |
| `errors` | 错误列表 |
| `degradation_level` | 公共客户端标记的失败等级，当前失败通常为 `4` |

推荐 NSTL 使用判据：

```python
trace = response.search_trace or {}
used_nstl_gateway = (
    response.success
    and bool(response.papers)
    and trace.get("api_url_configured") is True
    and trace.get("gateway_code") == 1
)
```

如果已经映射为业务对象，也可按来源判断：

```python
item.source_type.startswith("nstl_")
```

## 9. Adapter 模板

```python
from app.common.retrieval import SearchRequest
from app.common.retrieval.schemas import StandardPaper


class YourAgentSearchAdapter:
    source_agent = "research_question_agent"

    def to_search_request(self, task) -> SearchRequest:
        return SearchRequest(
            query=task.search_query,
            paper_num=task.top_k or 20,
            year_range=task.year_range,
            languages=task.languages or ["zh", "en"],
            rerank=True,
            intent_classification=True,
            solr_type=2,
            user_scope=getattr(task, "user_scope", None),
            source_agent=self.source_agent,
            context={
                "topic": getattr(task, "topic", ""),
                "section_title": getattr(task, "section_title", ""),
                "evidence_purpose": getattr(task, "purpose", ""),
            },
        )

    def to_business_items(self, papers: list[StandardPaper]):
        return [
            {
                "id": paper.paper_id,
                "source_type": f"nstl_{paper.source}",
                "title": paper.title,
                "authors": paper.authors,
                "year": paper.year,
                "venue": paper.venue,
                "doi": paper.doi,
                "url": paper.url,
                "abstract": paper.abstract,
                "score": paper.final_score,
            }
            for paper in papers
        ]
```

Adapter 可以 import 业务模型；公共 `client.py` 不可以 import 业务模型。

## 10. 批量检索边界

可使用：

```python
client.search_multi_sync(requests_, max_concurrency=5)
```

仅适用于确实独立的多个检索任务：

- 报告章节
- 主题簇
- claim
- 独立证据需求

科研好问题/研究问题生成主链路不使用批量检索，应保持一个标准化问题和一次检索请求。

## 11. 扩展建议

以下方法为可选扩展能力，各项目可根据需要自行实现：

- `LiteratureSearchClient.health_check()` — 网关健康检查，建议在服务启动时调用，用于确认网关连通性。参考实现：发送一次 `paper_num=1` 的小规模检索请求。

```python
response = LiteratureSearchClient(timeout=30).search_sync(
    "机器学习 科研问题",
    paper_num=1,
    source_agent="retrieval_smoke_test",
)
print(response.success, response.total, response.error, response.search_trace)
```

## 12. 接入验收清单

- 已配置 `LITERATURE_SEARCH_API_URL` 或 `NSTL_RETRIEVAL_API_URL`。
- 代码从 `app.common.retrieval` 导入公共能力。
- 业务代码构造 `SearchRequest`。
- 业务代码不手写 NSTL HTTP payload。
- 当前智能体有 `StandardPaper -> 业务对象` 的 Adapter。
- `source_agent` 稳定。
- `search_trace` 已保存或透出。
- 真实检索成功使用 `response.success and response.papers` 判断。
- 失败或空结果在业务层降级。
- 降级结果不会和真实 NSTL 文献混淆。
- 业务代码没有直接依赖私有检索器、Milvus、Solr、RRF 或 Reranker。
