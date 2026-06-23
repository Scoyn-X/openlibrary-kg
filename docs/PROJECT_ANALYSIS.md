# Openlibrary-KG 项目详细梳理

## 整体架构

```
Phase 1            Phase 2            Phase 3-5               Phase 6           Phase 7
概念抽取     →     LLM生成定义   →   关系发现            →   KG组装       →   Neo4j导入
(静态AST)         (Claude/DeepSeek)  (同义词/多义/共现)      (JSON输出)        (图数据库)
```

每个 Phase 独立运行，中间产物写入 `output/` 目录，失败可单独重跑。

---

## Phase 1 — 概念抽取（Concept Extraction）

### 核心思路

从 OpenLibrary 的 Python 源码中，通过 **AST 静态分析** 提取所有标识符（函数名、类名、变量、参数、import、attribute），将它们拆分为名词 token（snake_case / CamelCase → 单词），保留每个出现位置的**代码上下文**，然后通过硬屏蔽词和语料库级过滤筛掉噪音。

### 方法

- **AST 遍历**（Python `ast` 模块）：`_IdentifierVisitor` 继承 `ast.NodeVisitor`，覆盖 `visit_FunctionDef`、`visit_ClassDef`、`visit_Assign`、`visit_Import` 等十余个节点类型
- **标识符拆分**：正则表达式将 `get_user_name` → `[get, user, name]`，将 `getUserBookData` → `[get, user, book, data]`
- **硬屏蔽词过滤**：内置一个包含 stdlib 模块名、Python builtins、web.py/infogami 框架符号、常见 stop words 的大屏蔽列表（`HARD_BLOCKLIST`），直接剔除
- **两轮过滤**：
  - Pass 1 — 逐 token 硬屏蔽 + 丢弃所有 `import` 类型的标识符
  - Pass 2 — 语料库级覆盖率过滤：丢弃出现在 >50% 文件的词（如 `self`、`return` 这样的框架管道词，而非领域概念）
- **上下文记录**：每个出现记录 `file_path`、`function_name`、`class_name`、`line_number`、`code_snippet`（前后各 3 行）、`block_type`（module/function/method/class）
- **文件发现**：按 glob pattern 扫描 Python 文件，默认排除 tests、vendor、mocks、`__pycache__`、node_modules 等目录

### 是否使用大模型

**否。** 此阶段完全基于静态分析和规则。

---

## Phase 2 — LLM 定义生成（Definition Generation）

### 核心思路

对 Phase 1 抽取出的每个概念出现（concept occurrence），让 LLM **结合该概念所在的代码上下文生成定义**，回答「这个概念**是什么**」，而非描述代码在做什么。例如，"A user is a person who has registered for an account on the Open Library platform to borrow books and manage reading lists."

### 方法

- **LLM API 调用**：支持 Anthropic Claude 和 OpenAI/DeepSeek 兼容 API
- **批量并发**：通过 `asyncio.Semaphore` 控制并发数，配合 `RateLimiter`（令牌桶算法）控制请求速率
- **DiskCache 持久化**：以 `MD5(system_prompt + user_prompt)` 为 key 缓存 LLM 响应到 `.llm_cache/`，支持中断后断点续跑
- **采样策略**：
  - `random`：随机采样 N 条（快速测试 prompt 质量）
  - `stratified`：对每个频次 ≥ min_freq 的概念取至多 per_concept 个出现（保证多义词分析有足够样本）
  - `full`：全部出现
- **Strict 模式**：若超过 50% 的定义生成失败，直接抛异常终止（防止生成一个表面正常但全是空定义的 JSON）
- **空结果不缓存**：API 失败返回空串时不写入缓存，下次运行会重试

### 是否使用大模型

**是，这是核心阶段。** LLM 的作用是：

> **输入**：概念名 + 原始标识符 + 所在文件 + 行号 + 所属类/函数 + 代码片段
> **输出**：一句定义，描述该概念在特定代码上下文中的**身份/角色/领域含义**
>
> Prompt 严格约束输出格式和内容方向 —— 只回答「它是什么」，禁止提及 function/variable/class 等编程术语。

---

## Phase 3 — 同义词检测（Synonym Detection）

### 核心思路

在代码仓库中，两个不同的词可能指向同一个领域概念（如 `user` 和 `member`，`book` 和 `title`）。用 **embedding 相似度** 做初筛，再对大模型无法自动区分的中等相似度区间用 **LLM 做最终判断**。

### 方法

- **双轨机制**：
  - **Track A（命名变体，自动接受）**：cosine ≥ 0.85 → 直接标记为同义词（处理 `validate_email ↔ valid_email`、`account ↔ accounts` 这类词汇变体）
  - **Track B（领域等价，LLM 把关）**：cosine ∈ [0.55, 0.85) → 让 LLM 判断两者是否指代同一个领域实体（处理 `user ↔ account` 这类形式不同但语义相近的词）
  - cosine < 0.55 → 直接丢弃
- **Embedding 文本构建**：对每个概念，拼接 canonical_name + LLM 定义 + 前 3 个 raw identifier → 统一文本 → embedding 向量
- **Embedding 提供方**：支持 SentenceTransformers（本地）和 OpenAI Embedding API
- **Top-K 剪枝**：每个概念只保留关系最密切的前 K 个候选对
- **LLM 判断缓存**：DiskCache 缓存 `SYNJUDGE|` 前缀的判断结果，已判定的不会再次调用 LLM。仅缓存有效判定的结果；LLM 失败（空响应）不缓存，下次运行重试。

### 是否使用大模型

**部分使用。** LLM 仅在 Track B 中使用，作用如下：

> - 当两个概念的 embedding 相似度落在中等区间时，由纯 embedding 无法可靠判断
> - LLM 接收两个概念各自的**名称、标识符列表、LLM 生成的定义**作为输入
> - 输出 `YES/NO` + 一句话理由，判断它们在此代码库中是否为同一领域实体
> - 例如：区分 `book` 和 `work`（在图书馆目录领域，「book 是物理载体，work 是抽象作品」，不是同义词）这种需要领域知识的微妙 case

---

## Phase 4 — 一词多义分析（Polysemy Analysis）

### 核心思路

同一个词（如 `user`）在整个代码仓库中可能出现 100 次，每次出现背后的**语义可能不同**。将同一概念的多个 occurrence 各自的 LLM 定义做 embedding，然后用 **DBSCAN 聚类**，每个簇 = 该概念在代码仓库中的一种不同含义。

### 方法

- **前置门槛**：概念必须出现 ≥5 次（`min_occurrences`）且分布在 ≥3 个不同文件中（`min_files`）—— 避免将单文件内重复使用误判为多义
- **Embedding**：对同一概念下所有 occurrence 的 LLM 定义（来自 Phase 2）进行向量化
- **DBSCAN 聚类**：自定义 `_dbscan_cluster` 实现，`eps=0.35` 为距离阈值，`min_samples=1`（避免孤立点被噪声吞掉）
- **聚类代表选取**：每个簇中取距离簇心最近的 occurrence 的定义作为该含义的 `canonical_definition`
- **distinctiveness 评分**：簇内平均距离越小 → distinctiveness 越高（含义越清晰）
- **去重优化**：若所有 occurrence 的定义完全相同（全部为同一字符串），跳过聚类，直接视为单一含义

### 是否使用大模型

**间接使用。** 本阶段本身不调用 LLM，但**完全依赖 Phase 2 中 LLM 为每个 occurrence 生成的定义**作为聚类输入。没有 Phase 2 的 LLM 定义，就无法对不同代码位置的同一概念进行语义区分。

---

## Phase 5 — 共现分析（Co-occurrence Analysis）

### 核心思路

两个概念频繁出现在**同一个函数/类/方法**中，说明它们之间存在某种关联（如 `username` 和 `password`、`invoice` 和 `reimburse`）。统计所有概念对在同一封闭上下文中的共现次数，归一化后筛选出显著的对。

### 方法

- **上下文构建**：以 `(file_path, class_name, function_name)` 三元组为原子上下文，收集每个上下文内出现的全部概念
- **丢弃模块级上下文**（`drop_module_level_context=True`）：忽略不在任何函数或类内的 import/变量（它们只是被批量 import 到同一个文件顶部，不代表逻辑关联）
- **归一化方式**（可配置）：
  - `jaccard`：`|A ∩ B| / |A ∪ B|`（默认）
  - `pmi`：点互信息
  - `npmi`：归一化点互信息
- **子域感知**：提取 OpenLibrary 子包名（`openlibrary/openlibrary/<subdomain>/`），若一对概念大部分共现发生在不同子域之间，则权重乘以 `cross_subdomain_factor`（默认 0.3）降权
- **阈值过滤**：仅保留归一化得分 ≥ threshold（默认 0.05）的对

### 是否使用大模型

**否。** 此阶段完全基于统计方法（Jaccard/PMI 归一化 + 子域加权），不涉及 LLM。

---

## Phase 6 — KG 组装（Knowledge Graph Assembly）

### 核心思路

将前五个阶段的产物合并为统一的 `KnowledgeGraph`：以概念名为 key 聚合 occurrence → 构造 `Concept` 节点，以同义词关系 + 共现关系构造 `Relationship` 边，附加多义词簇信息到对应概念上。

### 方法

- **数据聚合**：扫描 `output/` 目录下各阶段的 JSON 文件，自动检测哪些阶段已完成
- **Pydantic 数据模型**：`Concept`、`ConceptOccurrence`、`Relationship`、`KnowledgeGraph` 等结构体确保数据一致性和类型安全
- **导出格式**：
  - JSON（主格式，node-link 结构）
  - GEXF（Gephi 可视化）
  - NetworkX pickle（Python 分析）
- **统计报告**：自动计算并输出概念频次 Top30、多义词统计、同义词 Top20、共现 Top20、图度分布、中心性最高的概念等
- **元数据注入**：记录 generation timestamp、codebase source、phases included 等信息

### 是否使用大模型

**否。** 纯粹的组装和统计阶段。

---

## Phase 7 — Neo4j 导入（Neo4j Export）

### 核心思路

将 Phase 6 生成的最终 KG JSON 导入 Neo4j 图数据库，使概念之间的关系可通过 Cypher 查询，支持关系发现、中心性分析、路径追踪等图分析操作。

### 方法

- **数据模型映射**：
  - 概念 → `(:Concept)` 节点（属性：`concept_id`、`canonical_name`、`split_terms`、`frequency`、`num_occurrences`、`has_polysemy`、`num_definition_clusters`）
  - 同义词 → `[:SYNONYM]` 边（属性：`weight`、`track`、`method`、`llm_reason`）
  - 共现 → `[:CO_OCCURRENCE]` 边（属性：`weight`、`cooccurrence_count`、`dominant_subdomain`、`same_subdomain_ratio`）
  - 多义 → `[:POLYSEMY]` 边
- **批量导入**：使用 UNWIND 批量创建节点/边，`MERGE` 基于 `concept_id` 避免重复
- **约束和索引**：自动创建 `concept_id` 唯一性约束、`canonical_name` 索引、`SYNONYM` 和 `CO_OCCURRENCE` 的 `weight` 索引
- **可清空重导**：`--clear` 标志在导入前执行 `MATCH (n) DETACH DELETE n` 清空旧数据
- **CLI 灵活配置**：`--uri`、`--user`、`--password`、`--database` 可覆盖 config.yaml 中的 Neo4j 连接配置

### 是否使用大模型

**否。** 纯数据导入工具。

---

## 数据模型

```
ConceptOccurrence          — 概念在代码中的一个出现位置
  ├─ occurrence_id
  ├─ raw_identifier        — 原始标识符（如 "get_user_name"）
  ├─ split_name            — 拆分过滤后的概念名（如 "user_name"）
  ├─ identifier_type       — function_name | variable | parameter | import | ...
  ├─ context               — CodeContext (file_path, function_name, class_name,
  │                           line_number, code_snippet, block_type)
  └─ definition            — LLM 生成的定义（Phase 2 填充）

Concept                   — 聚合多个 occurrence 的唯一概念
  ├─ concept_id
  ├─ canonical_name        — 规范化名称
  ├─ all_raw_identifiers   — 所有出现过的原始标识符
  ├─ occurrences           — ConceptOccurrence 列表
  ├─ frequency             — 出现总次数
  └─ definition_clusters   — DefinitionCluster 列表（Phase 4 填充）

Relationship              — 概念间关系边
  ├─ relationship_id
  ├─ source_concept_id / target_concept_id
  ├─ relationship_type     — "synonym" | "polysemy" | "co-occurrence"
  ├─ weight
  └─ metadata              — 额外的 judge 信息

KnowledgeGraph            — 顶层容器
  ├─ metadata              — 生成时间、源码路径、包含的阶段
  ├─ concepts              — Concept 列表
  ├─ relationships         — Relationship 列表
  └─ concept_index         — concept_id / canonical_name → 数组下标
```

---

## 输出文件

| 文件 | 内容 |
|---|---|
| `phase_1_concepts.json` | 所有概念出现：原始标识符、拆分名词、文件位置、代码上下文 |
| `phase_2_definitions.json` | Phase 1 + LLM 定义（每个出现一条"是什么"的定义） |
| `phase_3_synonyms.json` | 同义词关系对（含 Track A/B 标记、相似度、LLM 理由） |
| `phase_4_polysemy_groups.json` | 一词多义分组（每个概念 → 多个含义簇） |
| `phase_5_cooccurrence.json` | 共现关系对（含共现次数、归一化得分、子域信息） |
| `phase_6_knowledge_graph.json` | 最终 KG：合并所有节点和关系 |
| `phase_6_knowledge_graph_stats.json` | 统计摘要：频次排名、多义词、图度分布等 |

---

## 总结一览

| Phase | 核心方法 | LLM 使用 | LLM 角色 |
|---|---|---|---|
| 1. 概念抽取 | AST 遍历 + 标识符拆分 + 硬屏蔽词过滤 + 语料库覆盖率过滤 | 否 | — |
| 2. 定义生成 | LLM API + 令牌桶限流 + DiskCache + 分批采样 | **是** | 为每个概念出现生成语境化定义 |
| 3. 同义词检测 | Embedding 相似度（双轨：高 cos 自动 / 中 cos LLM 判断） | **部分** | 判断中等相似度对是否域等价 |
| 4. 一词多义 | Embedding + DBSCAN 聚类（依赖 Phase 2 的 LLM 定义） | 间接 | 聚类输入来自 Phase 2 LLM 产生的定义 |
| 5. 共现分析 | Jaccard/PMI 归一化 + 子域加权 | 否 | — |
| 6. KG 组装 | Pydantic 数据模型 + JSON/GEXF/NetworkX 导出 | 否 | — |
| 7. Neo4j 导入 | UNWIND 批量 Cypher + 约束/索引 | 否 | — |

大模型在整个 pipeline 中承担了**语义理解中枢**的角色：

- **Phase 2** 用它将代码符号翻译为领域概念定义
- **Phase 3** 用它在 embedding 模糊区间做精确的同义/非同义裁决
- **Phase 4** 依赖 Phase 2 的输出做一词多义发现

没有 LLM，整个 KG 就只能停留在「标识符 → 标识符」的语法层面，无法上升到「概念 → 概念」的语义层面。
