# CHANGES.md — 改造记录

> 配合 `CURRENT_STATE.md` 阅读。本文按"你问了什么 → 我改了什么 → 为什么这样改 → 如何验证"组织。

---

## 0. 你确认的四个决定

| # | 决定 | 落实在 |
|---|---|---|
| 1 | **先把 Phase 2 LLM 定义补全后再讨论关系梳理** | §2 LLM 修复 |
| 2 | **概念过滤强度 = b（同时过滤 stdlib + framework 符号）** | §1 概念抽取改造 |
| 3 | **同义词检测引入 LLM 判定路径** | §3 同义词两路检测 |
| 4 | **下游用 GitHub closed issues + 修复 PR 改动文件做 ground truth** | §5 下游 issue 定位 |

---

## 1. 概念抽取改造（决定 2）

### 1.1 `openlibrary_kg/extraction/noun_filter.py` — 完全重写
**之前**：只有一个 ~30 词的 `DEFAULT_STOP_WORDS`（挡 `self/cls/tmp/...`），以及一个**死代码**的 `is_noun_like`（声明了但 `name_splitter` 不调用）。

**之后**：
- 拆成五个分类清单：
  - `DEFAULT_STOP_WORDS`（短变量名 + Python 关键字）
  - `PYTHON_BUILTINS`（`abs/all/list/dict/print/...`）
  - `PYTHON_BUILTIN_METHODS`（`append/split/strip/get/keys/items/dumps/loads/...`）
  - `PYTHON_STDLIB_MODULES`（`os/re/sys/json/datetime/logging/typing/...`）
  - `FRAMEWORK_SYMBOLS`（web.py：`web/delegate/render_template/ctx/...`；infogami：`infogami/public/template/...`；pytest：`fixture/monkeypatch/...`；typing 内部符号 `any/literal/cast/iterable/...`）
- 合并出 `HARD_BLOCKLIST` 作为默认 stop list。
- 新增 `filter_by_coverage()`：返回"出现在 >50% 文件"的概念集合，让上层在抽取完成后再扫一遍砍掉。
- 保留 `COMMON_VERB_TOKENS / is_noun_like`，但说明它是"标识符级保留、概念级丢弃"的策略。

**为什么**：现状 stats 里 top concepts 全是 `get/append/split/strip/json/datetime/logger/literal/cast/...`，这些不是 openlibrary 的领域概念。光把它们从 stop_words 里加上还不够，因为像 `logger` 这种**领域里也有**的词不能硬黑名单（domain 里可能真的有 `Logger` 类，但满 codebase 80% 文件都引它就说明是 framework 用法）—— 所以引入**文件覆盖率**这第二层。

### 1.2 `openlibrary_kg/extraction/name_splitter.py` — 改造
**之前**：`split_name_filter_nouns` 用一份硬编码的小 stop_words，不挂 HARD_BLOCKLIST。

**之后**：默认走 `HARD_BLOCKLIST`；调用方传 `stop_words=None` 即可。

### 1.3 `scripts/extract_concepts.py` — 二次过滤
**新增两个东西**：
1. **跳过 `identifier_type=='import'` 的 occurrence**：`import os, re, sys` 完全不是领域概念，但旧代码把它们也录进了图。
2. **Pass 2 文件覆盖率过滤**：抽完所有 occurrence 后，统计每个 split_name 的文件分布；超过 50% 的全砍。输出 metadata 多了 `concepts_dropped_by_coverage` 字段，方便检查砍了哪些。

**预期效果**：5369 → 估计 1000-2000 概念，3447 共现关系里大量 stdlib-导致的边消失。

---

## 2. LLM 流水线修复（决定 1）

### 2.1 `config.yaml` — 改模型名 + 缩 max_tokens
| 字段 | 之前 | 之后 |
|---|---|---|
| `llm.model` | `deepseek-v4-pro` ❌ 不存在 | `deepseek-chat` ✅ |
| `llm.max_tokens` | 1000 | 300 |

> Deepseek 官方只有 `deepseek-chat` 和 `deepseek-reasoner` 两个模型 ID。之前的名字 API 直接返 4xx，被 client 静默吞成空串。

### 2.2 `openlibrary_kg/llm/definition_generator.py` — 重写
**三个关键修复**：

1. **不再缓存空字符串**：旧代码 `cache.set(key, "")`，毒缓存 → 永远重跑也是空。新代码空结果跳过 `cache.set`。
2. **空缓存视为未命中**：旧代码"缓存里有 ''" 也命中。新代码 `if cached and isinstance(cached, str) and cached.strip()`。
3. **新增 `strict` 模式**：失败率 ≥50% 直接抛 `DefinitionGenerationError`，带可读错误消息（提示检查模型名/key/限速），不再悄无声息出一份"0 个定义"的 JSON。

### 2.3 `scripts/generate_definitions.py` — 加 `--no-strict` 开关
默认严格模式。`--no-strict` 可让脚本即便大量失败也继续，以便 debug。

---

## 3. 同义词两路检测（决定 3）

### 3.1 `openlibrary_kg/llm/prompt_templates.py` — 新增同义词判定 prompt
- `SYNONYM_JUDGE_SYSTEM`：明确告诉 LLM "YES = 两个概念在此 codebase 内可互换"，并**特别提醒** `work ↔ book` 在图书馆领域不是同义。
- `SYNONYM_JUDGE_USER_TEMPLATE`：拼接两个概念的 name / identifiers / definition。
- `build_synonym_judge_prompts()` + `parse_synonym_judgment()`：构造和解析"YES/NO + 一句理由"。

### 3.2 `openlibrary_kg/relationships/synonyms.py` — 重写为两路
| 路径 | 触发条件 | 处理 |
|---|---|---|
| Track A "naming_variant" | cosine ≥ 0.85 | **自动接受** —— 通常是 `validate_email/valid_email/account/accounts` 这种命名变体 |
| Track B "domain_equivalence" | 0.55 ≤ cosine < 0.85 | **LLM 判定** —— YES 才接受。捕获 `user ↔ account` 这类字面差大但语义同的对 |
| 丢弃 | cosine < 0.55 | 直接丢 |

每条 Relationship 的 metadata 多了 `track`（`naming_variant` / `domain_equivalence`）和 `llm_reason`（仅 Track B），下游可以差别对待两类同义。

### 3.3 `scripts/detect_synonyms.py` — 接入 LLM client
- 复用 `definition_generator._make_client()` 创建 deepseek 客户端。
- 新增 `--no-llm` 标志（emergency switch）。
- 输出 metadata 多了 `naming_variant_threshold` / `llm_judge_range` / `llm_validation` 字段。

---

## 4. 一词多义收紧（衍生 from 决定 1+3）

### 4.1 `openlibrary_kg/relationships/polysemy.py` — 双闸门
**新增 `min_files_for_polysemy=3` 参数**：必须在 ≥3 个文件出现才算多义候选；只在一个文件里反复出现的概念不是多义，是同一个本地用法的重复。

**`min_occurrences_for_polysemy` 3 → 5**：3 太低，3 次出现就聚类经常聚出噪声簇。

**`distance_threshold` 0.3 → 0.35**：略放宽 DBSCAN eps，让"明显不同义"才会被切开，避免把"同义但措辞不同的定义"误切。

### 4.2 `config.py` + `config.yaml` + `analyze_polysemy.py` — 同步配置
- `PolysemyConfig` dataclass 多了 `min_files_for_polysemy: int = 3`。
- YAML 注释里写明门槛改动的原因。
- 脚本把新字段传给 `analyze_polysemy()`。

---

## 5. 共现按子域分桶（衍生 from 决定 2）

### 5.1 `openlibrary_kg/relationships/cooccurrence.py` — 重写
**三处改动**：

1. **新增 `drop_module_level_context`（默认 True）**：context 里 class_name 和 function_name 都是空（即文件顶层 import / 全局变量）的共现直接丢。这一项干掉的就是 "os↔re↔sys 因为同在文件顶部 import 而被算成共现"。

2. **新增 `_subdomain_of(file_path)`**：用正则提取 `openlibrary/openlibrary/<X>` 里的 X 作为子域（accounts / core / coverstore / catalog / plugins / solr / admin / api / olbase / i18n / data / views / utils / templates / macros / fastapi / schemata / components）。

3. **新增 `use_subdomain_partition` + `cross_subdomain_factor=0.3`**：
   - 每对概念记录它的共现发生在哪些子域里、各发生多少次；
   - 若主导子域占比 ≥ 50%，保持原始 Jaccard 权重；
   - 否则视为"跨子域共现"，权重 × 0.3 降权。
   - 这样不直接丢，是因为有些跨子域共现是真实的（accounts 和 plugins/upstream 都用 user），只是要降权。

每条 Relationship 的 metadata 多了 `dominant_subdomain` / `same_subdomain_ratio` / `cross_subdomain_penalized` / `raw_score`，方便后续 debug 和 issue 定位时的解释。

### 5.2 `config.py` + `config.yaml` + `analyze_cooccurrence.py` — 同步配置

---

## 6. 下游 issue 定位（决定 4）

### 6.1 新增 `openlibrary_kg/downstream/` 子包

#### `ground_truth.py` — GitHub 数据抓取
- `fetch_closed_issues(repo)`：分页拉 closed issues（排除 PR）。
- `find_fixing_pr(repo, issue_number)`：用 issue timeline 找"cross-referenced" 事件 + 验证 PR `merged=true`。
- `list_pr_files(pr_number)`：拉 PR 文件清单，按 `openlibrary/` 前缀 + `.py` 后缀过滤。
- `build_ground_truth()`：组合上述步骤，导出 `output/issue_ground_truth.json`：
  ```json
  [{"issue_number": N, "title": "...", "body": "...",
    "pr_number": M, "changed_files": ["openlibrary/accounts/model.py", ...],
    "url": "..."}, ...]
  ```
- 支持 `GITHUB_TOKEN` 环境变量（5000 req/hour，否则 60）；内置 rate-limit 等待。

#### `issue_localization.py` — KG 上的定位算法
`IssueLocalizer` 类做这几步：
1. **加载 KG** + 建索引：`token → concepts`、`concept → occurrences`、`concept → files`、`concept IDF`、synonym 邻接表（按 track 分）、co-occurrence 邻接表。
2. **`_seed_concepts(tokens)`**：issue 文本 tokenize → 在 token 索引里找命中。完全等于 canonical name 给 +1.0，token-only 命中给 +0.5。
3. **`_expand_synonyms`**：一跳 synonym 扩展。Track A naming-variant 全权传播，Track B 按 `synonym_track_b_factor=0.5` 衰减。
4. **`_expand_cooccurrence`**：一跳共现扩展，按 `cooccurrence_decay=0.5` 衰减。
5. **`_select_polysemy_cluster`**：**关键的消歧步骤** —— 若一个概念有多个 `definition_clusters`，用 sentence-transformer 算 issue 文本与每个簇 canonical_definition 的相似度，**只保留**最相似簇里的 occurrence。例如 issue 说 "user can't login" 时，user 概念只用"会话主体"簇的出现位置，而不是"数据库角色"簇的位置。
6. **打分**：每个被保留的 occurrence 给所在文件加 `concept_weight × concept_IDF`，同时记下函数级分数。
7. **返回 top-K** `{file_path, score, top_function, top_function_score}`。

`evaluate()` 函数：对 ground truth 跑 Recall@K 和 MRR；用 basename 后缀比对来吸收 KG 路径前缀和 GitHub 路径前缀不一致的问题。

### 6.2 新增脚本
- `scripts/build_issue_ground_truth.py`：CLI 包装，参数 `--repo / --max-pages / --max-issues`。
- `scripts/locate_issue.py`：单次定位 (`--title "..."`) 或批量评估 (`--eval --ground-truth ...`)；支持 `--no-embeddings` 跳过多义消歧。

---

## 7. 完整改动文件清单

| 文件 | 操作 | 目的 |
|---|---|---|
| `config.yaml` | 修改 | 模型名修复 + 新关系阈值 |
| `openlibrary_kg/config.py` | 修改 | dataclass 新字段 |
| `openlibrary_kg/extraction/noun_filter.py` | **重写** | 五类 blocklist + 覆盖率过滤 |
| `openlibrary_kg/extraction/name_splitter.py` | 修改 | 默认走 HARD_BLOCKLIST |
| `openlibrary_kg/llm/prompt_templates.py` | 扩展 | 同义词判定 prompt |
| `openlibrary_kg/llm/definition_generator.py` | **重写** | 不毒缓存 + strict 失败检测 |
| `openlibrary_kg/relationships/synonyms.py` | **重写** | 两路检测（cosine + LLM） |
| `openlibrary_kg/relationships/polysemy.py` | 修改 | 双闸门（频次 + 文件分布） |
| `openlibrary_kg/relationships/cooccurrence.py` | **重写** | 子域分桶 + 丢 module 级 |
| `scripts/extract_concepts.py` | 修改 | 丢 import + 二次覆盖过滤 |
| `scripts/generate_definitions.py` | 修改 | `--no-strict` 支持 |
| `scripts/detect_synonyms.py` | 修改 | 接入 LLM client |
| `scripts/analyze_polysemy.py` | 修改 | 传新参数 |
| `scripts/analyze_cooccurrence.py` | 修改 | 传新参数 |
| `openlibrary_kg/downstream/__init__.py` | **新增** | 包占位 |
| `openlibrary_kg/downstream/ground_truth.py` | **新增** | GitHub issue+PR 抓取 |
| `openlibrary_kg/downstream/issue_localization.py` | **新增** | KG 定位算法 + 评估 |
| `scripts/build_issue_ground_truth.py` | **新增** | CLI |
| `scripts/locate_issue.py` | **新增** | CLI（单查 / 评估） |
| `CURRENT_STATE.md` | **新增** | 现状文档 |
| `CHANGES.md` | **新增** | 改动文档（本文） |

---

## 8. 推荐的重跑顺序

```bash
cd D:/Secret/Sem4/SE/frontier/openlibrary-kg

# 0. 清掉旧的 LLM 毒缓存（重要）
rm -rf .llm_cache
# 同时清掉旧的 output 让脚本重新生成
rm -rf output/phase_*.json

# 1. 概念抽取（不要 API，验证 noun_filter 是否生效）
python scripts/extract_concepts.py
#    重点看 metadata.concepts_dropped_by_coverage —— 应该出现 datetime/json/web/...
#    以及 occurrences 总数比 31646 显著降低

# 2. 先 sample 50 个验证 LLM 真的通了
python scripts/generate_definitions.py --sample 50
#    检查输出里 definitions_generated 应该接近 50；definition 字段都是单句
#    确认无误后跑全量
python scripts/generate_definitions.py

# 3. 同义词（含 LLM 判定 Track B —— 会再调用一批 LLM）
python scripts/detect_synonyms.py
#    metadata.num_synonym_pairs 应该 > 0
#    抽几个 Track B 记录看看 llm_reason 是否合理

# 4. 一词多义
python scripts/analyze_polysemy.py
#    重点看 user / work / edition / account 这些词是否被识别为多义

# 5. 共现
python scripts/analyze_cooccurrence.py
#    top pair 应该是 username↔password / loan↔user / edition↔work 这类
#    而不是 os↔re / cc↔to 这种

# 6. 装配 KG
python scripts/build_kg.py

# 7. 下游 ground truth（建议先设 GITHUB_TOKEN）
set GITHUB_TOKEN=ghp_xxx
python scripts/build_issue_ground_truth.py --max-pages 3 --max-issues 100

# 8. 下游评估
python scripts/locate_issue.py --eval --ground-truth output/issue_ground_truth.json --top-k 10
#    输出 {n, recall_at_k, mrr, top_k}

# 8'. 单 issue 试一下
python scripts/locate_issue.py --title "User cannot log in with email"
```

---

## 9. 仍未做、留待你判断后再动的事

| 项 | 说明 | 触发条件 |
|---|---|---|
| 同义词 / 共现的 ground truth 标注 | 现在没有人工标的 100 对来调阈值 | 等 §8 跑完看产物质量 |
| 多义消歧的 eps 调优 | 0.35 是猜的；要 user/work/edition/account 4 个词手工标真值后再调 | 等 Phase 4 出第一版产物 |
| call graph 一跳邻居作为共现扩展 | README 里我提过；现在没做 | 等 §8 跑出 Recall@10 数据后判断是否值得加 |
| Neo4j export 同步更新 | `graph/neo4j_exporter.py` 没动；Track A/B 都进 `:SYNONYM` 边但 metadata 不同；视实际查询需要再决定要不要拆边类型 | 你说要不要在 Neo4j 里区分 |

跑完 §8 的"完整重跑"流程后，把 phase 1–5 的 metadata 数字和 §6 的评估指标贴给我，我再看下一步要不要继续动这四项。
