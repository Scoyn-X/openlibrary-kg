# Openlibrary-KG 现状总结（写在改造之前）

> 写于：与你商讨完关系梳理思路、即将开始改造之前。  
> 目的：把"当前实现是什么样子、为什么这样设计、目前的实际产物、暴露出的具体问题"一次性讲清楚。后续的改动以这份文档为基线。

---

## 0. 课题定位（来自 Target.txt）

- **核心任务**：在 openlibrary 这个 Python 仓库上做"概念抽取 + 关系梳理 → KG"。
- **下游目标**：用这个 KG 服务 **issue 定位**。
- **关系范围**：导师明确说重点处理 **同义词、一词多义、共现** 这三种。
- **核心语义手段**：LLM —— 输入"概念 + 上下文"，让 LLM 输出"它是什么"（定义），不让它讲"代码做了什么"。

---

## 1. 项目骨架

```
openlibrary-kg/
├── config.yaml                # 全局配置：路径、模型、阈值、Neo4j 等
├── main.py                    # 等价于 scripts/run_pipeline.py
├── openlibrary_kg/
│   ├── config.py              # YAML → dataclass，环境变量覆盖
│   ├── models.py              # Pydantic：Concept / Occurrence / Relationship / KG
│   ├── extraction/            # ── Phase 1
│   │   ├── ast_parser.py      #   走 Python AST，收集所有标识符 + 上下文
│   │   ├── name_splitter.py   #   snake_case / CamelCase 拆分为 token
│   │   ├── noun_filter.py     #   过滤 stop words / 内置动词 / 数字
│   │   └── file_discovery.py  #   按 include/exclude 找 .py 文件
│   ├── llm/                   # ── Phase 2
│   │   ├── base.py            #   抽象客户端
│   │   ├── openai_client.py   #   兼容 Deepseek / OpenAI 的 HTTP 客户端
│   │   ├── anthropic_client.py
│   │   ├── prompt_templates.py#   生成定义的 system+user prompt
│   │   ├── definition_generator.py # 并发 + 限流 + 磁盘缓存
│   │   └── rate_limiter.py
│   ├── embeddings/            #   sentence-transformers / openai 两种
│   ├── relationships/
│   │   ├── synonyms.py        # ── Phase 3：embedding cosine
│   │   ├── polysemy.py        # ── Phase 4：DBSCAN 聚类定义向量
│   │   └── cooccurrence.py    # ── Phase 5：同 (file, class, func) 内共现 + Jaccard
│   ├── graph/
│   │   ├── builder.py         # ── Phase 6：聚合所有 phase 输出为 KG
│   │   ├── export.py
│   │   ├── neo4j_exporter.py  # ── Phase 7：导入 Neo4j
│   │   └── stats.py
│   └── utils/                 # io / logging / 磁盘缓存
├── scripts/                   # 每个 phase 一个独立 CLI
│   ├── extract_concepts.py    # phase 1
│   ├── generate_definitions.py# phase 2
│   ├── detect_synonyms.py     # phase 3
│   ├── analyze_polysemy.py    # phase 4
│   ├── analyze_cooccurrence.py# phase 5
│   ├── build_kg.py            # phase 6
│   ├── export_to_neo4j.py     # phase 7
│   └── run_pipeline.py        # 串起来跑
└── output/                    # 各 phase 的 JSON 中间产物
```

设计原则：**phase 之间走 JSON 落盘解耦**，失败可单独重跑；LLM/embedding 有磁盘缓存。

---

## 2. 数据模型（models.py）

```python
CodeContext         # 一次出现的代码位置：file_path / class_name / function_name / line / snippet / block_type
ConceptOccurrence   # 一次概念出现：raw_identifier / split_name / identifier_type / context / definition
DefinitionCluster   # 一词多义中的一个含义簇：canonical_definition / occurrence_ids / distinctiveness
Concept             # 聚合：canonical_name / split_terms / all_raw_identifiers / occurrences / frequency / definition_clusters
Relationship        # 边：source_concept_id / target_concept_id / relationship_type ("synonym"|"polysemy"|"co-occurrence") / weight / metadata
KnowledgeGraph      # 顶层：metadata + concepts + relationships + concept_index
```

`Concept.concept_id` 目前**等于 canonical_name**（在 `graph/builder.py` 里硬编码），方便和关系数据用名字串起来。

---

## 3. 七个 Phase 的逻辑

### Phase 1 — 概念抽取（不需要 API）
- `extraction/file_discovery.py` 按 `codebase.include_patterns` / `exclude_patterns` 发现 `.py` 文件。
- `extraction/ast_parser.py` 用 `ast.NodeVisitor` 遍历每个文件：
  - 抽取的标识符类型：函数名 / 类名 / 变量名 / 参数名 / 属性名 / import 名。
  - 每次抽取都记录 `CodeContext`（file/class/func/line + 周围 3 行 snippet + block_type）。
- `extraction/name_splitter.py`：对 `raw_identifier` 做 snake_case + CamelCase 拆分，过滤 stop words / 短 token / 纯数字，保留 `keep_abbreviations`（ISBN/OLID/OL/MARC 等），用 `_` 连回去作为 `split_name`。
- 输出：`output/phase_1_concepts.json`，结构 `{phase, metadata, occurrences:[...]}`。

### Phase 2 — LLM 生成定义（需要 API）
- `llm/prompt_templates.py`：system prompt 强调"只说概念是什么、不说代码做什么、单句、以概念名开头"；user prompt 拼上下文。
- `llm/definition_generator.py`：
  - 构造所有 prompt，先查 `.llm_cache/`（MD5 key 落盘）。
  - 未命中的按 `max_concurrent` 批并发，rate limiter 限速。
  - 失败时 `results = [""] * len(batch_prompts)` —— **静默把空字符串当成结果继续往下走**。
- 输出：`output/phase_2_definitions.json`，结构和 Phase 1 一样，多了 `definition` 字段。

### Phase 3 — 同义词检测
- `relationships/synonyms.py`：
  - 把每个概念变成文本 `"name | definition | raw_id1, raw_id2, raw_id3"`。
  - sentence-transformers (`all-MiniLM-L6-v2`) 全量向量化。
  - 计算两两 cosine 相似度，每个概念取 top-k 邻居中 ≥ `similarity_threshold` (默认 0.75) 的，产出 `relationship_type="synonym"`。
- 输出：`output/phase_3_synonyms.json`。

### Phase 4 — 一词多义
- `relationships/polysemy.py`：
  - 按 `split_name` 把 occurrences 分组，仅保留 `≥ min_occurrences_for_polysemy` (默认 3) 的概念。
  - 对每个概念的所有 `definition` 文本向量化 → 内置 DBSCAN（`eps=0.3, min_samples=1`）→ 每个簇 = 一个含义。
  - 每个簇取离质心最近的定义作为 `canonical_definition`。
- 输出：`output/phase_4_polysemy_groups.json`。

### Phase 5 — 共现
- `relationships/cooccurrence.py`：
  - 按 `(file_path, class_name, function_name)` 分组每个 occurrence 出现的 `split_name` 集合。
  - 在每个 context 内两两计数；按 Jaccard 归一化 `|A∩B|/|A∪B|`。
  - 过滤 `count < 3` 或 `score < 0.05`。
- 输出：`output/phase_5_cooccurrence.json`。

### Phase 6 — KG 装配
- `graph/builder.py`：聚合 Phase 1 + 2 + 4 的节点信息，把 Phase 3 + 5 的关系合并。Phase 4 的 cluster 挂到 `Concept.definition_clusters`。
- 输出：`output/phase_6_knowledge_graph.json` + `phase_6_knowledge_graph_stats.json`。

### Phase 7 — Neo4j 导入
- `graph/neo4j_exporter.py` 按 batch 写入 `:Concept` 节点和 `:SYNONYM` / `:POLYSEMY` / `:CO_OCCURRENCE` 边。

---

## 4. 当前 config.yaml 的关键值

```yaml
codebase.root: D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary
codebase.exclude_patterns: tests/ vendor/ mocks/

llm.provider: openai
llm.model: deepseek-v4-pro          # ⚠️ 这个模型名 deepseek 官方不存在
llm.api_base: https://api.deepseek.com/v1
llm.max_tokens: 1000
llm.rate_limit: 10 req/s, 5 并发

embedding.model: all-MiniLM-L6-v2   # 384 维

relationships.synonyms.similarity_threshold: 0.75
relationships.polysemy.min_occurrences_for_polysemy: 3
relationships.polysemy.embedding_distance_threshold: 0.3
relationships.cooccurrence.min_count: 3
relationships.cooccurrence.threshold: 0.05  # Jaccard
```

---

## 5. 当前真实产物（截至诊断时）

来自 `output/phase_6_knowledge_graph_stats.json`：

| 指标 | 数值 | 解读 |
|---|---|---|
| 总概念数 | **5369** | 抽出来了 |
| 总关系数 | **3447** | 全部 type=co-occurrence |
| 同义词关系数 | **0** | `phase_3_synonyms.json` 文件根本不存在 |
| 多义词数 | **0** | Phase 4 输出文件也不存在 |
| LLM 定义生成成功数 | **0**（`definitions_generated: 0`） | Phase 2 跑了但全失败 |
| 孤立节点 | **4411 / 5369 = 82%** | 绝大多数概念没有任何边 |

`top_concepts_by_frequency` 前 30 里典型噪声：
- Python 内置方法：`get` (1765), `append`, `split`, `startswith`, `strip`, `join`, `lower`, `replace`, `dumps`
- stdlib 模块：`datetime`, `json`, `re`, `os`, `logging`
- framework 符号：`init`, `path`, `type`

`top_central_concepts` 前 20：`literal`, `delegate`, `web`, `any`, `logging`, `logger`, `get_logger`, `render_template`, `cast`, `re`, `public`, `requests`, `stats`, `os`, `config`, `functools`, `context`, `accounts`, `type_checking`, `iterable` —— 绝大多数是框架/stdlib，不是 openlibrary 的领域概念。

---

## 6. 目前已经能看出的问题（按严重性排）

### 严重 P0：Phase 2 LLM 完全没跑通
- **现象**：`phase_2_definitions.json.metadata.definitions_generated = 0`，所有 `occurrence.definition = ""`。
- **根因（高度怀疑）**：`config.yaml` 里 `llm.model: deepseek-v4-pro` 不是 deepseek 官方模型名（应为 `deepseek-chat` 或 `deepseek-reasoner`）。API 返回 4xx，被 `openai_client.py` 捕获后返回空串。
- **次根因（毒缓存）**：`definition_generator.py:134` 失败时把 `""` 也 `cache.set(...)`，下次重跑直接命中空串，**永远跑不出定义**，除非手动 `rm -rf .llm_cache`。
- **次根因（脚本吞错）**：`generate_definitions.py` 的 `--sample` 解析是 `occurrences[:sample]`（前 N 条），但 README 说是"测试 prompt 质量"，含义不一致；而且 sample=50 与全量无 prompt 校验。
- **连锁影响**：
  - Phase 3 同义词只能基于"概念名+空字符串"做 embedding，等于纯命名相似度，**抓不到任何领域同义**。
  - Phase 4 一词多义直接无法运行（没有定义可聚类）。
  - 你那份 README 里写的"user 的 5 种含义"等示例是**期望产物**，不是 stats 文件里真有的产物。

### 严重 P1：概念抽取噪声占比极高
- `extraction/noun_filter.py` 的 `DEFAULT_STOP_WORDS` 主要挡的是 `self/cls/tmp` 这种短变量名。
- 但**没挡**：
  - Python 内置类型/函数：`list/dict/set/tuple/range/len/print/...`
  - 字符串方法：`split/strip/join/lower/upper/startswith/endswith/replace/format/...`
  - 列表/字典方法：`append/extend/insert/pop/items/keys/values/get/setdefault/...`
  - stdlib 模块：`os/re/sys/json/datetime/logging/functools/typing/collections/...`
  - web.py / infogami 框架符号：`web/delegate/public/render_template/...`
- 还有一个反作用：`noun_filter.is_noun_like` 把 verb-like token 排除，但 `name_splitter.split_name_filter_nouns` **没调用**它（只调用 stop_words/min_len/数字），所以"动词过滤"实际上是死代码。

### 严重 P2：同义词只能抓"命名变体"
- 现有方法只有 cosine 一条路径，阈值 0.75 偏高。
- 能抓：`validate_email ↔ valid_email`、`account ↔ accounts`、`get_username ↔ get_by_username` —— 全是字面相近的命名变体。
- 抓不到：`user ↔ account`（领域等价但字面差异大）、`work ↔ book`（字面相近但含义不同——还会被**误判为同义**）。
- 后者必须借助 LLM 做"在这个仓库的上下文里，这两个概念是不是指同一类实体"的判断。

### 严重 P2：一词多义的门槛太宽
- `min_occurrences_for_polysemy: 3` 太低，3 次出现的概念很容易"出现 ≥ 1 个含义"但其实是噪声。
- 没有"分布广度"约束 —— 3 次都在同一个文件里出现根本不该算多义候选。

### 严重 P2：共现把 stdlib 噪声放大成"强中心节点"
- 共现的 context 颗粒度是 `(file, class, func)`，但 `top_central_concepts` 是 `literal/cast/re/os/logging/...` —— 这些是因为"`import os` 跟 `import re` 出现在同一个 module-level context"被算成共现。
- Jaccard 门槛 0.05 + count ≥ 3 太宽，3447 条关系里有大量这种"两个 import 都在同一文件"型噪声。

### 中等 P3：identifier_type=import 该不该入图
- 一个文件的 `import os, re, sys` 不携带领域语义，但目前都被当成"出现"参与同义词 + 共现。
- 这会拉高 `os/re/sys` 的频率与中心度。

### 中等 P3：相似度数据建在"空定义"之上
- 即便修了 Phase 2，目前 Phase 3 的设计也只用了 `definitions[0]` 一条定义参与拼接。如果一个概念多义，第一条定义不能代表整体。

### 轻 P4：模型 ID 等 ID 一致性
- `graph/builder.py` 用 `canonical_name` 作为 `concept_id`；`relationships/synonyms.py` 在概念字典里也回退到 `split_name` —— 现在能凑齐，但脆弱。

---

## 7. 下游 issue 定位现在的状态

**完全没有实现**。`openlibrary_kg/` 下没有 `downstream/` 或类似目录，scripts/ 下也没有 issue 相关脚本。

---

## 8. 一句话总结现状

> 流水线骨架完整、能跑出 5369 节点 + 3447 边的 KG，但：(1) LLM 实际没产出任何定义；(2) 节点里 ≥ 50% 是 Python/框架噪声不是领域概念；(3) 三种关系里只有共现真跑出了产物，且产物被噪声放大；(4) 下游 issue 定位还没动手。所以"KG"目前更像"标识符共现图"，离"openlibrary 的概念图"还有不小距离。
