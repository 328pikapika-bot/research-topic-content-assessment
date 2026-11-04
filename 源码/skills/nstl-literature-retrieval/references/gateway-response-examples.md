# NSTL 统一检索网关响应示例

## 成功响应（code:1，含文献数据）

```json
{
  "code": 1,
  "msg": "success",
  "data": [
    {
      "id": "NSTL_ZZ_20240101_0001",
      "title": "钙钛矿太阳能电池界面工程优化策略研究进展",
      "hasAuthor": [
        { "fullName": "张三", "name": "张三" },
        { "fullName": "李四", "name": "李四" }
      ],
      "year": 2024,
      "hasSource": {
        "title": "科学通报",
        "volume": "69",
        "issue": "12"
      },
      "doi": "10.1360/TB-2024-0001",
      "hasFile": "https://www.nstl.gov.cn/paper_detail?id=NSTL_ZZ_20240101_0001",
      "language": "zh",
      "abstract": "钙钛矿太阳能电池因其高效的光电转换效率而备受关注，然而稳定性问题制约了其商业化进程。本文综述了近年来界面工程在提升钙钛矿太阳能电池稳定性方面的研究进展...",
      "score": 0.9523,
      "keywords": ["钙钛矿", "太阳能电池", "界面工程", "稳定性"],
      "docType": "JournalPaper",
      "indexLevel": "SCI",
      "hasFulltext": true,
      "rerank_score": 0.9871,
      "citation_count": 15
    },
    {
      "id": "NSTL_EN_20230615_0042",
      "title": "Interface Engineering for Stable Perovskite Solar Cells: A Comprehensive Review",
      "hasAuthor": [
        { "fullName": "John Smith", "name": "Smith" },
        { "fullName": "Jane Doe", "name": "Doe" }
      ],
      "year": 2023,
      "hasSource": "Advanced Energy Materials",
      "doi": "10.1002/aenm.202300042",
      "hasFile": "https://www.nstl.gov.cn/paper_detail?id=NSTL_EN_20230615_0042",
      "language": "en",
      "abstract": "Perovskite solar cells have achieved remarkable power conversion efficiencies, yet stability remains a critical challenge. This review provides a comprehensive analysis of interface engineering strategies...",
      "score": 0.9107,
      "keywords": ["perovskite", "solar cell", "interface engineering", "stability"],
      "docType": "JournalPaper",
      "indexLevel": "SCI",
      "hasFulltext": true,
      "rerank_score": 0.9432,
      "citation_count": 87
    }
  ]
}
```

## hasAuthor 数组格式

hasAuthor 为数组，每项是包含 fullName 和 name 字段的对象：

```json
{
  "hasAuthor": [
    { "fullName": "王小明", "name": "王小明" },
    { "fullName": "David Brown", "name": "Brown" }
  ]
}
```

解析时优先取 `fullName`，其次取 `name`。

## hasSource 格式

### dict 格式（含卷期信息）

```json
{
  "hasSource": {
    "title": "科学通报",
    "volume": "69",
    "issue": "12"
  }
}
```

解析时取 `title` 字段作为 venue。

### string 格式（仅期刊名）

```json
{
  "hasSource": "Advanced Energy Materials"
}
```

解析时直接作为 venue。

## 空结果响应（code:1，无文献数据）

```json
{
  "code": 1,
  "msg": "success",
  "data": []
}
```

## 错误响应

### 网关业务错误（code:-1）

```json
{
  "code": -1,
  "msg": "内部服务异常",
  "data": null
}
```

### 参数错误（code:0）

```json
{
  "code": 0,
  "msg": "参数错误：question 不能为空",
  "data": null
}
```

## 字段映射表

| 网关字段 | StandardPaper 字段 | 说明 |
|---|---|---|
| `id` | `paper_id` | 文献唯一标识 |
| `title` | `title` | 文献标题 |
| `hasAuthor` | `authors` | 作者数组，提取 fullName/name |
| `year` | `year` | 发表年份 |
| `hasSource` | `venue` | 来源，兼容 dict 和 string |
| `doi` | `doi` | DOI 标识 |
| `hasFile` | `url` | 文献链接 |
| `language` | `language` | 语种 |
| `abstract` | `abstract` | 摘要 |
| `keywords` | `keywords` | 关键词列表 |
| `docType` | `doc_type` | 文献类型 |
| `indexLevel` | `index_level` | 索引级别 |
| `hasFulltext` | `has_fulltext` | 是否有全文 |
| `score` | `retrieval_score` / `final_score` | 检索得分（final_score 优先取 final_score 字段，缺省回退 score） |
| `rerank_score` | `rerank_score` | 重排序得分（可选） |
| `citation_count` | `citation_count` | 被引次数 |
| `references` | `references` | 参考文献 ID 列表 |
| *(整条原始记录)* | `raw` | 保留完整原始数据供排障 |
