---
name: nstl-literature-retrieval
description: NSTL 统一文献检索接入工具包。通过脚本自动复制检索模块、配置环境变量、编写 Adapter，六步完成接入。业务代码只构造 SearchRequest，不直连 Milvus/Solr/RRF/Reranker，不手写网关 payload。
---

# NSTL 统一文献检索接入工具包

## 核心定位

本工具包是可落地的 NSTL 文献检索接入方案，不是说明文档。智能体和开发人员按以下六步即可完成接入，无需自行编写 HTTP 调用、解析网关响应或直连底层检索基础设施。

复用链路：

```text
智能体业务任务
→ SearchRequest
→ LiteratureSearchClient
→ NSTL 统一检索网关
→ StandardPaper[]
→ 业务 Adapter
→ 智能体自己的证据/文献对象
```

## 接入流程

### Step 1: 运行 apply_retrieval.py 复制公共检索模块

```bash
python scripts/apply_retrieval.py --backend-dir <your-backend-path>
```

此脚本将公共检索模块自动复制到目标项目的 `app/common/retrieval/` 目录，包含：

```text
app/common/retrieval
├── __init__.py        # 导出 LiteratureSearchClient, SearchRequest
├── client.py          # 统一检索客户端
├── schemas.py         # SearchRequest, SearchResponse, StandardPaper, SearchTrace
├── query_builder.py   # 检索式构造器
├── parser.py          # 网关响应解析器
└── adapters/
    ├── __init__.py
    └── example.py       # Adapter 示例
```

### Step 2: 配置环境变量

必配：

```text
LITERATURE_SEARCH_API_URL=<NSTL 检索网关地址>
```

可选：

```text
LITERATURE_SEARCH_TIMEOUT=600
```

兼容变量（仅旧部署使用，新代码不依赖）：

```text
NSTL_RETRIEVAL_API_URL / GOOD_QUESTION_MILVUS_API_URL / MILVUS_API_URL
NSTL_RETRIEVAL_TIMEOUT / GOOD_QUESTION_MILVUS_HTTP_TIMEOUT / MILVUS_HTTP_TIMEOUT
```

### Step 3: 编写业务 Adapter

参考 `assets/templates/adapters/` 中的模板，创建当前智能体的 Adapter。Adapter 职责：

- 将业务输入转换为 `SearchRequest`
- 将 `StandardPaper` 列表转换为业务对象
- 设置稳定的 `source_agent`（如 `research_question_agent`、`deep_research_agent`）

最小 Adapter 示例：

```python
from app.common.retrieval import SearchRequest
from app.common.retrieval.schemas import StandardPaper


class YourAgentSearchAdapter:
    source_agent = "your_agent_name"

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

### Step 4: 在业务代码中导入使用

```python
from app.common.retrieval import LiteratureSearchClient, SearchRequest

client = LiteratureSearchClient()
response = client.search(request)

if response.success and response.papers:
    business_items = adapter.to_business_items(response.papers)
else:
    business_items = build_agent_fallback(response.error, response.search_trace)
```

只有确实有多个独立检索任务时才使用 `client.search_multi_sync(requests, max_concurrency=5)`。科研好问题主链路保持单次检索。

### Step 5: 运行 audit_retrieval.py 审计接入完整性

```bash
python scripts/audit_retrieval.py --project-dir <your-project-root>
```

审计项：
- `app/common/retrieval` 模块存在且可导入
- `LITERATURE_SEARCH_API_URL` 已配置
- 业务代码无直连 Milvus/Solr/私有检索器的残留导入
- Adapter 完成 `StandardPaper → 业务对象` 映射
- `source_agent` 稳定且非空

### Step 6: 运行 smoke_retrieval.py 冒烟测试

```bash
python scripts/smoke_retrieval.py --api-url "$LITERATURE_SEARCH_API_URL" --query "人工智能 科研问题" --paper-num 1 --expect-real
```

验证端到端链路通畅。

## 必须遵守

- **只从 `app.common.retrieval` 导入公共检索能力**，不手写 NSTL 网关 HTTP payload。
- **不直连底层基础设施**：业务代码不依赖 Milvus、Solr、RRF、Reranker 或私有检索器（如 `HttpMilvusRetriever`）。
- **`LiteratureSearchClient` 只负责检索、响应标准化和追踪**，不承载业务模型。Adapter 可 import 业务模型，公共客户端不可。
- **真实检索成功判据**：`response.success and response.papers`。
- **降级不伪造**：失败或空结果可在业务层降级，但必须标记为降级线索，不能伪造 NSTL 文献题名、作者、DOI、URL 或来源。
- **保留 `search_trace`**：便于判断是否真正走了 NSTL 检索网关。

## 文献来源规范

| source_type | 含义 | 判断方式 |
|---|---|---|
| `nstl_*` | 真实 NSTL 文献 | `source_type.startswith("nstl_")` |
| `model_fallback_signal` | 模型降级线索 | `source_type == "model_fallback_signal"` |

业务层可以降级，但前端和后续链路必须能区分来源。

推荐 NSTL 网关使用判据：

```python
trace = response.search_trace or {}
used_nstl_gateway = (
    response.success
    and bool(response.papers)
    and trace.get("api_url_configured") is True
    and trace.get("gateway_code") == 1
)
```

## 科研好问题智能体边界

科研好问题/研究问题生成类智能体必须保持单次检索设计：

```text
用户输入
→ 输入预处理 / 任务识别 / 必要澄清
→ 一个标准化科研问题
→ 一个 SearchRequest
→ 约 20 篇已重排 NSTL 文献
→ 研究现状分析
→ 好问题生成、评分和展示
```

不要改成多章节检索，不要对多个候选问题分别反复检索。

## 数据模型与契约详情

字段级契约、网关 Payload、SearchRequest/SearchResponse/StandardPaper/SearchTrace 完整字段定义见：

```text
references/retrieval-contract.md
```
