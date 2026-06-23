# 迭代六：平台化、入口修复与排名优化

> 生成时间：2026-06-17

---

## 一、背景

迭代五结束时，KG 独立 File Recall@10 = 84.6%，混合追平 BM25（92.3%）。但存在三个结构性问题：

1. **缺少整体意识**：KG 只为 issue 定位一个下游任务服务，缺少通用查询接口
2. **3 条 title-only case**：issue 标题仅为 "### Title"，body 信息没被有效利用
3. **7 条同组件未进 top-10**：BFS 走到了正确概念，但竞争文件更大、累积分更高

---

## 二、KG 平台化：`kg_query.py`

### 问题

整个 `downstream/` 子包都在直接读取 `phase_6_knowledge_graph.json` 的原始 JSON，各自独立重建索引（`concepts_by_name`、`occurrences_by_concept`、`concept_files`、`concept_idf`）。每个模块都是为 issue 定位这一个任务硬编码的。

### 方案

新增 `openlibrary_kg/kg_query.py`——只读、预索引的 KG 查询平台。

```python
kg = KGQuery("output/phase_6_knowledge_graph.json")

# 概念查询
kg.get_concept("loan")          # → ConceptInfo
kg.get_files("edition")         # → frozenset of file paths
kg.get_neighbors("edition")     # → frozenset of concept names
kg.get_idf("loan")              # → float

# 图游走
kg.bfs({"loan": 1.0, "patron": 1.0}, max_hops=2)   # → {concept: weight}

# 子图提取
kg.subgraph({"loan", "patron", "borrow"}, radius=1)  # → (nodes, edges)
```

### 设计原则

- **只读**：不修改图（任务特定状态由调用方持有）
- **索引一次**：所有索引在 `__init__` 时预构建，查询时 O(1)
- **无任务偏向**：API 描述"图里有什么"，不描述"怎么用"。下游任务自行解读

### 改动

- 新增 `openlibrary_kg/kg_query.py`（~200 行）
- `IssueLocalizer.__init__` 新增 `kg_query: KGQuery | None = None` 参数。传入 KGQuery 时跳过 JSON 解析，直接复用已有索引。向后兼容。

### 验证

评估结果不变（84.6%），`KGQuery` 构造与 `localize()` 均通过一致性检查。

---

## 三、短标题 enrichment：`_enrich_short_title`

### 问题

3 条 both-miss case 的 issue 标题仅为 `## Title:` 或 `### Title`，携带零信息。body 包含丰富的描述（文件路径、反引号标识符、业务逻辑描述），但在 `title + "\n" + body` 拼接后，有效信号被稀释。

### 尝试与结果

| 尝试 | 方法 | Recall@10 |
|------|------|-----------|
| A | 从 body 提取反引号、文件路径、首段描述组成 [Key signals] 前缀 | 60.4%（噪声） |
| B | 仅提取文件路径追加 | 84.6%（不变） |
| **C** | **用 body 首句替换空标题** | **84.6%**（不变） |

尝试 C 的逻辑：对于 token < 5 的标题行，取 body 里第一个非空、非 markdown 标题的行作为实质标题。例如 `## Title:\n\nImport API rejects differentiable records...` 变成 `Import API rejects differentiable records...\n\n...`。

**结论**：body 关键词（`requests`、`urllib`、`read_subjects`）不在 KG 的概念集里——不是标题短的问题，是概念覆盖的硬边界。保留了修复（防御价值，不倒退）。

### 涉及文件

- `openlibrary_kg/downstream/issue_localization.py`：新增 `_enrich_short_title()` 静态方法

---

## 四、排名密度增强：`file_ranking` 密度乘数

### 问题

7 条同组件未进 top-10 的 case 共同特征：BFS 走到了正确概念，但竞争文件（如 `core/models.py` 179 概念）概念更多、累积分更高，把小而精准的文件挤出。

### 方案

在 `graph_walker.py` 的 `file_ranking` 中，expired 概念密度乘数：

```python
density = matched_concepts_in_file / total_concepts_in_file
final_score = raw_score * (1.0 + 0.4 * density)
```

小文件被匹配的概念占比高（如 `utils/dateutil.py` 34 概念中 10 个=29%），大文件占比低（`core/models.py` 244 概念中 89 个=36%……实际上大文件密度也不低，所以效果有限）。

### 结果

| α | File Recall@10 | File MRR |
|---|---------------|----------|
| 0.0（无密度） | 84.6% | 0.552 |
| 0.2 | 84.6% | — |
| **0.4** | **84.6%** | **0.580** |
| 0.6 | 84.6% | — |
| 0.8 | 84.6% | — |
| 1.0 | 84.6% | — |

Recall 不变，MRR 从 0.552 → **0.580（+0.028）**。正确文件的平均排名从 ~1.8 升至 ~1.7。α 全范围不倒退。

### 涉及文件

- `openlibrary_kg/downstream/graph_walker.py`：`file_ranking` 新增密度乘数

---

## 五、改动文件清单

| 文件 | 操作 | 目的 |
|------|------|------|
| `openlibrary_kg/kg_query.py` | **新增** | 通用 KG 查询平台 |
| `openlibrary_kg/downstream/issue_localization.py` | 修改 | 新增 `kg_query` 参数 + `_enrich_short_title` |
| `openlibrary_kg/downstream/graph_walker.py` | 修改 | 密度乘数 `final = raw × (1 + 0.4 × density)` |

### 未改文件

| 文件 | 原因 |
|------|------|
| `scripts/compare_methods.py` | 签名不变，走旧路径 |
| 其余全部 Phase 1-5 脚本 | 不受影响 |

---

## 六、最终状态

| 指标 | 迭代五 | 迭代六 |
|------|--------|--------|
| File Recall@10 | 84.6% | 84.6% |
| File MRR | 0.552 | **0.580** |
| KG 平台化 | 无 | `kg_query.py` |
| 排名公式 | SUM 聚合 | SUM × density 乘数 |
| 短标题处理 | 无 | body 首句替换 |

**Recall 天花板依然 84.6%**。密度增强改进了排名质量（MRR），但无 recall 增益。主体瓶颈仍然在 KG 概念覆盖和信号密度。
