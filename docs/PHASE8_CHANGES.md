# Phase 8 导航层实现记录

> 生成时间：2026-05-27

---

## 一、改动总览

### 新建文件（4 个模块 + 1 个脚本）

| 文件 | 行数 | 作用 |
|---|---|---|
| `openlibrary_kg/downstream/query_rewriter.py` | ~220 | embedding 语义检索 + 多义簇锁定 + token match 辅助信号 |
| `openlibrary_kg/downstream/graph_walker.py` | ~260 | 多跳 BFS 路径探索 + 高频概念剪枝 + 文件排名 |
| `openlibrary_kg/downstream/path_explainer.py` | ~120 | 导航路径 → 自然语言解释（模板化，无 LLM 依赖） |
| `openlibrary_kg/downstream/skeleton_generator.py` | ~130 | 概念聚焦的代码骨架（保留签名 + 仅匹配函数体） |
| `scripts/navigate_issue.py` | ~200 | Phase 8 入口：评估/单条/BM25 对比/Markdown 报告 |
| `scripts/demo_data.py` | ~350 | Demo 数据探索工具：共现/同义词/多义词/ego network/子图导出 |

### 重写文件（1 个）

| 文件 | 改动说明 |
|---|---|
| `openlibrary_kg/downstream/issue_localization.py` | `_seed_concepts` 从 naive token match → QueryRewriter 语义入口；图扩展从两跳独立方法 → GraphWalker 统一 BFS；输出增加 skeleton + explanation；`evaluate()` 增加单条失败隔离 |

### 修改文件（2 个）

| 文件 | 改动说明 |
|---|---|
| `scripts/run_pipeline.py` | 注册 Phase 8（`--navigate` 启用），phase range 9，新增 `--navigate-top-k`/`--navigate-max-hops` 参数 |
| `run_all.bat` | 新增 `navigate`/`eval` 快捷模式；默认流程增加 Phase 8 步骤；`--also-bm25` 对比 |

### 未改文件

| 文件 | 原因 |
|---|---|
| `locate_issue.py` | `IssueLocalizer` 构造函数及 `localize()`/`evaluate()` 签名向后兼容 |
| `compare_methods.py` | 同上 |
| `baselines.py` | BM25 基线完全独立 |
| Phase 1-7 全部脚本 | 不受影响 |
| `prompt_templates.py` | 保持 Target.txt 要求的定义纯净性 |
| `config.yaml` | 无需新增配置项 |

---

## 二、数据流（Phase 8 完整链路）

```
navigate_issue.py
  │
  ├─ IssueLocalizer.localize(title, body)
  │   │
  │   ├─ [1] QueryRewriter.rewrite(issue_text)
  │   │   ├─ 对每个 KG 概念构建代表文本（定义 + split_terms + raw id）
  │   │   ├─ 预计算所有概念 embedding（init 时一次性）
  │   │   ├─ issue 文本 embedding → cosine 与所有概念 → top-50
  │   │   ├─ token match 作为辅助信号（精确/词干匹配）
  │   │   └─ 对 top 概念遍历 Phase 4 的 definition_clusters，
  │   │      每个多义概念锁定至与 issue 最近的簇 → occurrence_filter
  │   │   输出: IssueQuery(matches=[ConceptMatch(name, weight, cluster_id, filter), ...])
  │   │
  │   ├─ [2] GraphWalker.walk(seed_weights)
  │   │   ├─ 从种子概念出发，BFS 遍历 SYNONYM + CO_OCCURRENCE 边
  │   │   ├─ max_hops=3，min_path_weight=0.01
  │   │   ├─ 高频概念剪枝（出现于 >30% 文件的概念停止扩展）
  │   │   ├─ 每跳衰减: synonym_track_b ×0.5, cooccurrence ×0.5
  │   │   └─ 记录完整路径（seed → hop1 → hop2 → destination）
  │   │   输出: WalkResult(paths, concept_weights, concept_paths)
  │   │
  │   ├─ [3] GraphWalker.file_ranking(walk_result)
  │   │   ├─ 对每个到达概念的 occurrence，加权: weight × IDF
  │   │   ├─ 若 occurrence 被多义 filter 排除则跳过
  │   │   └─ 按文件聚合得分 → top-K 文件 + top-5 函数
  │   │
  │   ├─ [4] SkeletonGenerator.generate(file, concepts)
  │   │   ├─ 按文件索引 concept → function → code_snippet
  │   │   ├─ 计算每个函数包含多少个 matched concepts
  │   │   └─ 输出匹配函数代码片段（保留模块级 import 区域）
  │   │
  │   └─ [5] path_explainer.explain_file_ranking()
  │       └─ 模板化生成：种子概念列表 + 路径文本 + 概念定义摘要
  │
  └─ evaluate(localizer, ground_truth)
      ├─ 逐条 issue try/except 隔离（单条失败不影响整体）
      ├─ Recall@K / MRR（file level + function level）
      └─ 输出 output/phase_8_evaluation.json
         输出 output/phase_8_navigation_report.md
```

---

## 三、运行时估算

### 数据规模

| 指标 | 数值 |
|---|---|
| KG 概念数 | 3,760 |
| KG 关系数 | 2,562 |
| Phase 1 出现次数 | 21,281 |
| 已有 LLM 定义 | 21,281 (100%) |
| SWE-bench 实例数 | 91 |

### 全新运行（`run_all.bat clean`）

| 阶段 | 耗时 | API Token (输入/输出) | 说明 |
|---|---|---|---|
| Phase 1 概念抽取 | ~3 min | 0 | 本地 AST 解析 |
| Phase 2 LLM 定义 | ~60 min | 6.4M / 0.64M | 21,281 次 DeepSeek API 调用，10 req/s 并发 |
| Phase 3 同义词 | ~10 min | 0.12M / 0.01M | embedding + Track B LLM ~1,000 对判断 |
| Phase 4 一词多义 | ~5 min | 0 | 本地 embedding + DBSCAN |
| Phase 5 共现分析 | ~2 min | 0 | 纯统计 |
| Phase 6 KG 组装 | ~1 min | 0 | JSON 导出 |
| Phase 7 Neo4j 导入 | ~3 min | 0 | 本地数据库 |
| Phase 8 语义导航 | ~2 min | **0** | 本地 sentence-transformers |
| **合计** | **~85 min** | **~7.0M total** | |

### 续跑（`run_all.bat`，利用缓存）

| 阶段 | 耗时 | API Token | 说明 |
|---|---|---|---|
| Phase 1-6 | ~2 min | 0 | 输出文件均存在，全部 skip |
| ground truth | ~2 min | 0 | GitHub API（免费） |
| Phase 8 | ~2 min | 0 | 本地 |
| **合计** | **~6 min** | **0** | |

### API 费用估算

```
DeepSeek-chat 定价:
  Input:  $0.14 / 1M tokens
  Output: $0.28 / 1M tokens

Phase 2: 6.4M input  × $0.14 = $0.90
         0.64M output × $0.28 = $0.18
Phase 3: 0.12M input × $0.14 = $0.02
         0.01M output × $0.28 = $0.00

合计: ~$1.10 USD
```

### Phase 8 零 API 设计

Phase 8 完全不调外部 API：
- **Embedding**：本地 `sentence-transformers` (`all-MiniLM-L6-v2`, ~80MB)
- **图游走**：纯 CPU BFS，无 LLM
- **骨架生成**：模板拼接，无 LLM
- **路径解释**：模板化生成（可选 LLM 增强，默认关闭）

---

## 四、容错设计

| 场景 | 行为 |
|---|---|
| embedding 模型加载失败 | `_semantic_enabled=False`，自动回退 token match |
| 单条 issue 处理抛异常 | `evaluate()` 中 try/except，记录到 error log，继续下一条 |
| ground truth JSON 不存在 | navigate_issue.py 打印提示并 exit 1 |
| KG JSON 格式错误 | `__init__` 时 json.load 抛异常，fast-fail |
| 所有概念无定义 | 用 `split_terms + canonical_name` 代替定义文本做 embedding |
| 图无边（无关系数据） | GraphWalker 仅返回种子概念自身 |
| 已存在输出文件 | `run_all.bat` 的 `:run_phase` 自动 skip |
| Neo4j 不可达 | 打印 WARN，跳过导入阶段，不阻塞后续 |

---

## 五、运行方式

```bash
# === 首次运行 ===
run_all.bat           # 完整 pipeline（Phase 1-6 + GT + eval + Neo4j）

# === 仅跑导航评估 ===
run_all.bat navigate  # 仅 Phase 8（需已有 KG + ground truth）

# === 仅跑评估（旧 compare + 新 navigate） ===
run_all.bat eval      # 跳过 Phase 1-6，跑两种评估方法

# === 单条测试 ===
python scripts\navigate_issue.py --title "User can't log in with email"

# === 全量评估 ===
python scripts\navigate_issue.py --top-k 10 --max-hops 3 --also-bm25

# === demo 模式（快速验证） ===
run_all.bat demo       # ~30 min，分层采样

# === 清空重跑 ===
run_all.bat clean      # 交互确认后删除 output\ + .llm_cache\
```

---

## 六、输出文件清单

| 文件 | 内容 |
|---|---|
| `output/phase_1_concepts.json` | 概念出现（AST 提取） |
| `output/phase_2_definitions.json` | LLM 定义 |
| `output/phase_3_synonyms.json` | 同义词关系 |
| `output/phase_4_polysemy_groups.json` | 一词多义分组 |
| `output/phase_5_cooccurrence.json` | 共现关系 |
| `output/phase_6_knowledge_graph.json` | 最终 KG |
| `output/swebench_ground_truth.json` | SWE-bench 标准答案 |
| `output/phase_8_evaluation.json` | Phase 8 评估结果（Recall@K, MRR, per-issue） |
| `output/phase_8_navigation_report.md` | Phase 8 可读报告 |
| `output/phase_8_bm25_evaluation.json` | BM25 基线评估（`--also-bm25`） |
| `output/compare_summary.json` | BM25 vs KG-walk 对比 |
| `output/kg_queries_report.md` | Cypher 查询结果 |

---

## 七、Demo 数据探索工具（`scripts/demo_data.py`）

全量跑完后，所有数据保存在 `output/` 下。`demo_data.py` 是这些 JSON 的查询前端，无需重跑任何 Phase。

### 支持的查询

| 命令 | 功能 |
|---|---|
| `--cooc` | Top 共现对，可按 `--subdomain` 筛选、`--cross` 只看跨子域 |
| `--syn` | Top 同义词对，`--track naming_variant`/`domain_equivalence` 分轨 |
| `--poly` | 多义词排名，`--min-meanings 10` 只看高度多义的 |
| `--hubs` | 最中心的概念（按度数排名） |
| `--syn-hubs` | 同义词边最多的概念 |
| `--ego <name>` | 某个概念的自我网络（所有邻接边） |
| `--search <pattern>` | 按名称/标识符模糊搜索概念 |
| `--subgraph <a,b,c>` | 导出概念子集 + 其间关系，`--out` 指定路径 |
| 无参数 | 打印全量数据摘要 |

### 使用示例

```bash
# 查看 accounts 子域内最强共现对
python scripts/demo_data.py --cooc --subdomain accounts -n 20

# 查看 LLM 判定的同义词（带理由）
python scripts/demo_data.py --syn --track domain_equivalence -n 30

# 哪些概念含义最丰富（>10 个不同意思）
python scripts/demo_data.py --poly --min-meanings 10

# user 概念关联了哪些其他概念？用什么边？
python scripts/demo_data.py --ego user -n 30

# 搜索借阅相关概念
python scripts/demo_data.py --search borrow

# 图的中心节点是谁？
python scripts/demo_data.py --hubs -n 20

# 导出 user/book/author/isbn 的局部子图
python scripts/demo_data.py --subgraph user,book,author,isbn --out demo_subset.json

# 保存结果到文件供论文使用
python scripts/demo_data.py --syn --track domain_equivalence -n 50 > demo_synonyms.txt
```
