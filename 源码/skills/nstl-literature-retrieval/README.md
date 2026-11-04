# NSTL 统一文献检索复用 Skill

## 这个 Skill 是给谁用的

本 Skill 面向项目组内部开发人员和后续智能体，用于统一复用 NSTL 文献检索能力。目标是让其他智能体不再重复理解底层网关协议，只要按本 Skill 的契约执行，就能接入统一检索服务。

## 它解决什么问题

不同智能体都可能需要检索文献，但不能各自：

- 手写一套 HTTP 请求。
- 直接访问 Milvus、Solr 或 Reranker。
- 复制深度检索里的私有检索器。
- 各自解析 NSTL 网关返回字段。
- 把模型降级线索误当成真实文献。

本 Skill 统一要求：

```text
业务输入
-> SearchRequest
-> LiteratureSearchClient
-> NSTL 统一检索网关
-> StandardPaper[]
-> 当前智能体 Adapter
-> 当前智能体业务对象
```

## 使用方式

当智能体或开发人员要接入 NSTL 统一检索时，先读：

```text
SKILL.md
```

它定义了必须遵守的规则和执行步骤。

需要字段、示例、Adapter 模板时，再读：

```text
references/retrieval-contract.md
```

## 最小接入流程

1. 在项目中准备公共检索模块：

   ```text
   backend/app/common/retrieval
   ```

2. 业务代码只导入公共入口：

   ```python
   from app.common.retrieval import LiteratureSearchClient, SearchRequest
   ```

3. 配置检索网关地址：

   ```text
   LITERATURE_SEARCH_API_URL
   ```

   或兼容使用：

   ```text
   NSTL_RETRIEVAL_API_URL
   ```

4. 在业务层构造 `SearchRequest`。

5. 调用：

   ```python
   response = LiteratureSearchClient().search(request)
   ```

6. 真实检索成功统一判断：

   ```python
   response.success and response.papers
   ```

7. 把 `StandardPaper` 通过当前智能体自己的 Adapter 转成业务对象。

8. 如果失败或无结果，只能在业务层降级，并明确标记为降级线索，不能伪造真实文献。

## 科研好问题智能体的特殊约束

科研好问题/研究问题生成类智能体只对标准化后的一个科研问题发起一次检索：

```text
一个标准化问题 -> 一个 SearchRequest -> 约 20 篇已重排文献
```

不要改成多章节检索，也不要对多个候选问题分别检索。

## 验收标准

接入完成后至少满足：

- 没有散落手写 NSTL HTTP payload。
- 没有直接调用 Milvus、Solr、RRF、Reranker。
- 没有继续复制私有 `HttpMilvusRetriever`。
- 业务层有 Adapter 完成 `StandardPaper -> 业务对象`。
- 真实文献来源和降级线索来源能区分。
- `search_trace` 可用于排查是否走了真实 NSTL 检索。

做到以上，就表示这个智能体已经按本 Skill 完成了文献检索能力复用。
