# Platform Common 公共能力目录

## 目录定位

`backend/app/common` 存放创新性评估智能体复用的基础能力。公共层只处理稳定、可复用的技术契约；业务语义由 `content_assessment` 模块负责，避免把主张、判定或报告格式耦合进检索客户端。

## 当前能力

### `retrieval`

位置：

```text
backend/app/common/retrieval
```

用途：封装统一文献检索网关调用，并将响应规范化为 `StandardPaper` 和 `SearchTrace`。

公共层负责：

- 构造统一检索请求并调用真实混合检索服务。
- 解析检索网关响应、作者、年份、链接与摘要。
- 返回成功、失败和耗时等可审计的检索追踪信息。

公共层不负责：

- 解析项目、指南或查新材料。
- 拆分主张、生成评估结论或评分卡。
- 伪造外部证据或把检索未命中解释为确定性结论。
- 生成创新性评估报告。

## `retrieval` 子模块

```text
retrieval/
├── client.py          # 统一文献检索客户端
├── schemas.py         # SearchRequest / StandardPaper / SearchResponse / SearchTrace
├── query_builder.py   # 构造网关查询文本
├── parser.py          # 解析检索网关响应
└── adapters/
    └── content_assessment.py
```

### 调用边界

创新性评估智能体在“检索规划与证据池构建”阶段，根据已提取的主张和指标生成 `SearchRequest`；`LiteratureSearchClient` 返回标准化文献；`adapters/content_assessment.py` 再将文献映射为业务侧 `EvidenceRecord`。

```text
材料与任务描述
  -> 主张与指标提取
  -> 检索计划
  -> LiteratureSearchClient
  -> StandardPaper + SearchTrace
  -> content_assessment adapter
  -> EvidenceRecord
  -> 主张级比对、风险判定和报告
```

Adapter 是业务边界。公共检索客户端不能 import 创新性评估的业务结构，只有 Adapter 可以完成 `StandardPaper` 到 `EvidenceRecord` 的转换。

## 检索可用性与降级

检索请求使用当前网关协议字段，例如 `question`、`paperNum`、`rerank`、`intent_classification` 与 `solr_type`。客户端记录 `SearchTrace`，让报告能区分“有外部证据”“检索失败”和“尚未配置网关”。

当网关未配置、调用失败或没有返回可用文献时，创新性评估流程仍会完成材料登记、主张提取与风险提示，但报告只能标为探索性结论，不能断言绝对原创、领先或已满足指南要求。

## 配置变量

优先使用平台级配置：

```text
LITERATURE_SEARCH_API_URL
LITERATURE_SEARCH_TIMEOUT
NSTL_RETRIEVAL_API_URL
NSTL_RETRIEVAL_TIMEOUT
```

创新性评估部署可使用以下模块配置：

```text
CONTENT_ASSESSMENT_MILVUS_API_URL
CONTENT_ASSESSMENT_MILVUS_HTTP_TIMEOUT
```

读取优先级：

```text
LITERATURE_SEARCH_API_URL
-> NSTL_RETRIEVAL_API_URL
-> CONTENT_ASSESSMENT_MILVUS_API_URL
-> MILVUS_API_URL
```

其他智能体需要复用检索能力时，应新增自己的 Adapter 并保持公共客户端契约稳定。
