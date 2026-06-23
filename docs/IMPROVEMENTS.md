# IMPROVEMENTS.md — 完整改造记录

> 这份文档汇总从"原始 5369-concept 的旧 KG"到"3760-concept 新 KG + BM25 对照评估"全过程的所有改动、踩过的坑、最终数据。配合 `CURRENT_STATE.md` 阅读 —— 前者讲改造前长什么样，本文讲改造做了什么、为什么、得到了什么。

---

## 目录

1. [改造前的痛点](#1-改造前的痛点)
2. [Phase 1 — 概念抽取](#2-phase-1--概念抽取)
3. [Phase 2 — LLM 定义生成](#3-phase-2--llm-定义生成)
4. [Phase 3 — 同义词检测（两路）](#4-phase-3--同义词检测两路)
5. [Phase 4 — 一词多义](#5-phase-4--一词多义)
6. [Phase 5 — 共现](#6-phase-5--共现)
7. [Phase 6 — KG 装配](#7-phase-6--kg-装配)
8. [Phase 7 — Neo4j 导入与查询](#8-phase-7--neo4j-导入与查询)
9. [下游 issue 定位](#9-下游-issue-定位)
10. [SWE-bench Pro 接入](#10-swe-bench-pro-接入)
11. [BM25 baseline 与对照评估](#11-bm25-baseline-与对照评估)
12. [Pipeline 编排（run_all.bat）](#12-pipeline-编排run_allbat)
13. [中途遇到的 bug 与修复](#13-中途遇到的-bug-与修复)
14. [最终评估数字](#14-最终评估数字)
15. [仍未做、留待决策的事](#15-仍未做留待决策的事)

---

## 1. 改造前的痛点

源自 `CURRENT_STATE.md` 的诊断：

| 问题 | 旧产物 |
|---|---|
| LLM 定义全失败 | `definitions_generated: 0` |
| 同义词为 0 | `phase_3_synonyms.json` 文件不存在 |
| 多义词为 0 | 同上 |
| 共现噪声主导 | 3447 条边里 top 中心节点是 literal/cast/re/os/logging |
| 抽取概念 ≥50% 是 stdlib | top 概念是 `get/append/split/strip/...` |
| 下游 issue 定位 | 完全未实现 |

---

## 2. Phase 1 — 概念抽取

### 改造目标
把 5369 个含 stdlib 噪声的"概念"砍到只保留 openlibrary 领域词。

### 改动文件
- `openlibrary_kg/extraction/noun_filter.py` — **重写**
- `openlibrary_kg/extraction/name_splitter.py` — 改默认走 HARD_BLOCKLIST
- `scripts/extract_concepts.py` — 加二次过滤

### 核心改动

**A. 五类硬黑名单（noun_filter.py）**
```
DEFAULT_STOP_WORDS       ~50 词（self/cls/tmp + Python 关键字）
PYTHON_BUILTINS          ~60 词（abs/list/dict/print/...）
PYTHON_BUILTIN_METHODS   ~80 词（append/split/strip/get/keys/items/dumps/...）
PYTHON_STDLIB_MODULES    ~70 词（os/re/sys/json/datetime/logging/typing/...）
FRAMEWORK_SYMBOLS        ~50 词（web/delegate/render_template/ctx/...）
                                  ─────
                          HARD_BLOCKLIST ≈ 350 词
```

**B. 文件覆盖率二次过滤（corpus 级）**
- 扫完所有 occurrence 后统计每个概念的文件分布
- 出现在 >50% 文件里的概念 = 框架水印，删除
- 体现在 `output/phase_1_concepts.json` 的 `metadata.concepts_dropped_by_coverage` 字段

**C. 跳过 import 类型 occurrence**
- `import os, re, sys` 不是领域概念
- `scripts/extract_concepts.py` 里 `if occ.identifier_type == "import": continue`

### 实际效果

| | 改造前 | 改造后 |
|---|---|---|
| 总 occurrence | 31,646 | 21,281 |
| 唯一概念数 | 5,369 | **3,760** |
| Top 概念是什么 | `get/page/site/path/append/split/...` | `page/path/site/query/edition/username/work/user/URL/limit/error/db/author` |

`get/append/split/strip/json/datetime/init/replace` 等 stdlib 噪声**全部消失**。

---

## 3. Phase 2 — LLM 定义生成

### 改造目标
解决"21k 个定义全是空字符串"的灾难性 bug。

### 改动文件
- `config.yaml` — 模型名
- `openlibrary_kg/llm/definition_generator.py` — **重写**
- `scripts/generate_definitions.py` — 重写 + 加分层抽样

### 关键修复

| 项 | 改造前 | 改造后 |
|---|---|---|
| 模型名 | `deepseek-v4-pro`（不存在） | `deepseek-chat` |
| max_tokens | 1000 | 300（单句定义够用） |
| 失败时缓存策略 | 写入 `""` 到 `.llm_cache/` → **永久毒缓存** | 跳过 cache.set，下次自动重试 |
| 缓存命中判定 | `if cached is not None` → 命中空串 | `if cached and cached.strip()` |
| 失败率 ≥50% | 静默继续，写空 JSON | 抛 `DefinitionGenerationError`，提示检查 model name |
| 进度可见性 | 仅"x/N batches" | 每 batch 失败计数 + 总失败率 |

### 新增分层抽样模式
对于"演示 demo"场景，新增 `--strategy stratified`：
- 对每个频次 ≥ `--min-freq` 的概念取 `--per-concept` 个 occurrence
- 保证高频概念在 Phase 4 多义聚类时有足够样本
- 默认 `min_freq=8, per_concept=5`

### 实际效果
- definitions_generated: 0 → **21,281**（全部成功，0 失败）
- LLM model 字段确认 `deepseek-chat`

---

## 4. Phase 3 — 同义词检测（两路）

### 改造目标
旧版 cosine ≥ 0.75 单一路径只抓得到命名变体（`valid_email/validate_email`），抓不到领域同义（`user/account`），还会误判 `work/book`。

### 改动文件
- `openlibrary_kg/relationships/synonyms.py` — **重写**
- `openlibrary_kg/llm/prompt_templates.py` — 加同义词判定 prompt
- `scripts/detect_synonyms.py` — 接入 LLM client + 缓存

### 双轨设计

```
cosine ≥ 0.85  →  Track A "naming_variant"  → 自动接受
0.70 ≤ cosine < 0.85  →  Track B "domain_equivalence"  → LLM 判定 YES/NO
cosine < 0.70  →  丢弃
```

**Track B 的 LLM prompt** 特别提示：
- "Answer YES only if A and B are interchangeable in this codebase"
- "Be careful: work vs book are RELATED but NOT synonymous (FRBR distinction)"

### Track B 缓存
中途加上的 fix —— 否则 5000 次 LLM 调用一旦中断就要全重跑。
- 缓存 key：`SYNJUDGE|<system_prompt>|||<user_prompt>` 的 MD5
- 缓存 value：`{is_synonym, reason}`
- LLM 失败响应**不缓存**，下次自动重试
- 改 prompt 模板自动失效（MD5 变）

### 阈值调整历史
- 第一次设 `llm_judge_low: 0.55` → Track B 候选**爆炸到 25,088**，预估 1.5-2 小时
- 中途收到错误信号后调整为 `0.70` → Track B 候选降到 ~4,000，约 12 分钟
- 原因：`all-MiniLM-L6-v2` 任意文本对的 baseline cosine 都在 0.4-0.6，0.55 等于没筛

### 进度日志
原来跑 4029 个 candidates 时一句日志都不打，看起来像卡住。修改后每个 batch 一行：
```
Track B batch 202/202 done: +2 accepted (running total 1093 / 4029 evaluated, 0 cached hits at start)
```

### 实际效果
- 总 1,382 对同义词
- Track A (cosine ≥ 0.85): **289** 对（命名变体）
- Track B (LLM 判定): **1,093** 对（接受率 1093/4029 = **27%**，符合预期）

---

## 5. Phase 4 — 一词多义

### 改造目标
旧版门槛过松（仅 freq ≥ 3 一个条件），且无文件分布约束。

### 改动文件
- `openlibrary_kg/relationships/polysemy.py` — 修改
- `scripts/analyze_polysemy.py` — 传新参数

### 关键改动

| 项 | 改造前 | 改造后 |
|---|---|---|
| min_occurrences | 3 | **5** |
| min_files | （没有） | **3**（新增） |
| DBSCAN eps | 0.30 | 0.35 |

### 实际效果（不完美）

- **499 个多义概念**被识别
- **但过度分簇**：`path` 居然有 216 个"含义"
- 看实际定义全是 *"path 是访问 X 页面的 URL 路由"* 的变体 —— LLM 把"login 路由"和"logout 路由"判为不同含义
- 真实多义应是 4-5 种（URL 路由 / 文件系统路径 / 模板位置 / API 端点）
- **eps 应调到 0.55-0.65 才合理**，留待你确认

### 下游影响
- 对 issue 定位仍然可用：消歧时挑出"URL route to login"那一簇，命中相关文件没问题
- 但报告里说"499 个多义"会误导，需谨慎措辞

---

## 6. Phase 5 — 共现

### 改造目标
旧版 top 中心节点 `literal/cast/re/os/logging/...` 全是 stdlib，因为 module-level 共现把 `import os, import re` 算成"强共现"。

### 改动文件
- `openlibrary_kg/relationships/cooccurrence.py` — **重写**
- `scripts/analyze_cooccurrence.py` — 传新参数

### 三处改动

**A. 丢弃 module 级 context**
- `drop_module_level_context: true`
- context 里 class_name + function_name 都空 → 当成 import 簇，丢弃
- 这一项直接干掉了 `os↔re↔sys` 这类伪相关

**B. openlibrary 子域分桶**
- 用正则 `openlibrary/openlibrary/<X>` 提子域：accounts / core / coverstore / catalog / plugins / solr / admin / api / olbase / ...
- 每对概念记录共现发生在哪些子域里、各几次

**C. 跨子域降权**
- 若主导子域占比 ≥ 50% → 保持原始 Jaccard 权重
- 否则视为跨子域 → weight × 0.3
- 不直接丢，因为有些跨子域共现是真实的（accounts 和 plugins/upstream 都用 user）

每条边的 metadata 增加：`dominant_subdomain` / `same_subdomain_ratio` / `cross_subdomain_penalized` / `raw_score`。

### 实际效果
- 共现边数 3,447 → **1,180**（-66%，主要是 stdlib 噪声被消除）
- top 共现现在能看到真正有意义的对（待 query 报告确认）

---

## 7. Phase 6 — KG 装配

### 改造目标
旧版 `concept_id = uuid4()`（随机 UUID），但关系数据用 canonical_name 作 source/target ID，**这个不匹配直接导致 Neo4j 边导入失败**。

### 改动文件
- `openlibrary_kg/graph/builder.py`

### 关键改动
```python
concept_map[name] = Concept(
    concept_id=name,            # ← 之前是 str(uuid4())
    canonical_name=name,
    ...
)
```

### 实际效果
- 新 KG: 3,760 nodes, 2,562 edges（1,382 synonym + 1,180 cooccurrence）
- 注意：499 个多义信息**作为节点属性 `definition_clusters` 存储**，不作为边类型
- **孤立节点 2,014 / 3,760 = 54%** —— 这是个潜在问题（见 §15）

---

## 8. Phase 7 — Neo4j 导入与查询

### 改造目标
- 之前导入只写 `weight` 一个属性，查询毫无信息量
- 旧 KG 的 UUID concept_id 导致边全部导入失败

### 改动文件
- `openlibrary_kg/graph/neo4j_exporter.py` — 边属性扩充
- `scripts/run_kg_queries.py` — **新增**

### 边属性扩充
每条边现在保留：

| 属性 | 适用类型 | 说明 |
|---|---|---|
| `weight` | 全部 | Jaccard / cosine |
| `track` | SYNONYM | naming_variant / domain_equivalence |
| `method` | SYNONYM | embedding_cosine / llm_judged |
| `llm_reason` | SYNONYM | LLM 给出的 YES/NO 理由（截 240 字符） |
| `cooccurrence_count` | CO_OCCURRENCE | 原始共现次数 |
| `dominant_subdomain` | CO_OCCURRENCE | 主导子域 |
| `same_subdomain_ratio` | CO_OCCURRENCE | 同子域比例 |
| `cross_subdomain_penalized` | CO_OCCURRENCE | 是否被跨子域降权 |

### 8 条精选 Cypher 查询（scripts/run_kg_queries.py）

1. 总节点/边数（按类型）
2. Top 15 高频概念
3. Top 15 中心节点（degree）
4. Track A 同义词样例
5. Track B 同义词样例 + LLM reason
6. 多义概念
7. 同子域强共现
8. 跨子域共现（已降权）

外加 `--concept <name>` 跑 ego 子图。

**输出双形态**：控制台 + markdown 文件 `output/kg_queries_report.md`。

---

## 9. 下游 issue 定位

### 改造目标
从无到有实现 issue 文本 → 候选文件/函数排序。

### 改动文件
- `openlibrary_kg/downstream/__init__.py` — **新增**
- `openlibrary_kg/downstream/issue_localization.py` — **新增 + 多次重写**
- `scripts/locate_issue.py` — **新增**

### IssueLocalizer 算法

```
issue 文本
  ↓ tokenize (drop English stopwords, light stem)
  ↓ 三档匹配概念: exact name 1.0 / exact token 0.6 / stem 0.3
seed concepts
  ↓ 一跳同义词扩展 (Track A 全权, Track B × 0.5)
  ↓ 一跳共现扩展 (× 0.5 decay)
expanded concepts
  ↓ 对每个概念:
  ↓   若有 ≤30 个多义簇 → embedding 选最相关簇 (issue 文本只 embed 一次)
  ↓   若 >30 个多义簇 → 跳过消歧 (数据噪声不可信)
  ↓ 累加 weight × IDF 到每个 occurrence 所在文件
file scores
  ↓ 排序，每文件取 top-5 函数
top-K results
```

### 关键改进（与初版相比）

**A. 英文停用词 + light stemming**
- 加 ~100 词英文虚词表（the/a/can/in/with/...）
- `_light_stem()` 处理 `-ing/-ed/-es/-s/-ies` 后缀（logging↔login 能命中）

**B. Top-N 函数列表**
- 旧版只返回 `top_function`（一个）
- 新版返回 `top_functions: [{name, score}, ...]`（5 个）
- 评估时**文件命中且 gold 函数在 top-5 函数列表里**就算对，减少假阴性

**C. 多义簇预计算 embedding（关键性能修复）**
- 旧逻辑：每个 issue × 每个匹配的多义概念 × 现场 embed 所有 cluster 定义
- 91 issue × `path`(216 簇) × 现场 embed = **几小时卡死**
- 新逻辑：构图时一次性 embed 所有合理多义概念的 cluster 定义并缓存
- 每个 issue 只 embed 自己的文本一次，后续是 numpy dot product
- 同时 over-fragmented (>30 簇) 的概念跳过消歧

**D. 每 issue 失败分析 dump**
- `evaluate()` 接受 `per_issue_out` 参数
- 输出每条 issue 的 gt_files / gt_functions / predicted_files / file_rank / func_rank
- 用于"哪些 issue 我们错了"的失败分析

---

## 10. SWE-bench Pro 接入

### 改动文件
- `openlibrary_kg/downstream/swebench_loader.py` — **新增**
- `scripts/build_swebench_ground_truth.py` — **新增**

### 自带的 unified-diff mini-parser
不引入 `unidiff` 依赖，自己写 ~30 行解析：
- `+++ b/<path>` 提 changed file（仅 `.py`）
- `@@ ... @@ def name(...)` 提 hunk 所在函数
- hunk body 里的 `+def/+class` 提新增/移动的函数

### Dataset 名字踩坑
我猜的 4 个候选全错（`SWE-bench-Pro` / `SWE-Bench-Pro`），实际是 **`ScaleAI/SWE-bench_Pro`**（Pro 前面是下划线）。
- 已修正为默认候选

### Schema 兼容
代码同时支持：
- 字段 `patch` / `gold_patch` / `golden_patch`
- 字段 `problem_statement` / `issue_text` / 标题+正文拼接

### 实际产物
- 731 行 SWE-bench Pro `test` split → 过滤到 `internetarchive/openlibrary` → **91 条有 Python patch 改动的 instance**

---

## 11. BM25 baseline 与对照评估

### 改动文件
- `openlibrary_kg/downstream/baselines.py` — **新增**
- `scripts/compare_methods.py` — **新增**
- `openlibrary_kg/downstream/issue_localization.py::evaluate()` — 升级

### BM25Baseline 设计
- 索引所有 .py 文件的**完整源代码**（不只是标识符）
- snake_case 识别符同时拆解为子 token（`get_user_email` → 也索引 `get/user/email`）—— 跟 KG token 形态对齐，**公平对比**
- 用同一份 issue tokenizer（含同样的英文停用词）→ **公平对比**
- 函数级用 query token 覆盖 + 函数名子 token 的简单交集打分

### 同一份 evaluate() 跑两个方法
保证：
- 同一份 91 条 ground truth
- 同一份路径归一化
- 同一份命中判定（file: suffix match；function: 文件匹配 + 函数名在 top-5 函数列表里）
- 同一份 metric 公式（Recall@K + MRR）

### 输出
- `output/compare_summary.json` —— 双方指标表
- `output/compare_per_issue_bm25.json` —— BM25 每条预测
- `output/compare_per_issue_kg.json` —— KG 每条预测

---

## 12. Pipeline 编排（run_all.bat）

### 模式

| 命令 | 作用 |
|---|---|
| `run_all.bat` | 默认（run）：完整流水线，跳过已存在输出 |
| `run_all.bat demo` | 30 min 演示模式：Phase 2 用分层抽样 |
| `run_all.bat clean` | 清掉 output/ 和 .llm_cache/（交互式确认） |
| `run_all.bat eval-only` | 跳过 Phase 1-6，只重跑 ground truth + 评估 |
| `run_all.bat neo4j` | 跳过 Phase 1-6，只导 Neo4j + 查询 |

### 关键设计
- 每个 phase 检查输出文件存在性 → 自动跳过、可断点续跑
- Phase 失败立即停止 + 提示如何恢复
- Neo4j 不可达时**优雅跳过**（不让一个未启动的 DB 让 30 分钟工作白费）
- `clean` 模式需要 `YES` 确认 → 防止误删 LLM 缓存

### Bug 修复历史（见 §13）
- 括号在 DESC 里炸 if 块 → 改用延迟展开 `!DESC!`
- LF 行尾符被 cmd 误读 → 用 PowerShell 转 CRLF

---

## 13. 中途遇到的 bug 与修复

按时间顺序：

### Bug 1: Track B 同义词候选爆炸
- **现象**：Phase 3 输出 `Track B [0.55, 0.85) 25088`，估计 1.5-2 小时
- **根因**：`llm_judge_low` 设成 0.55 太低，MiniLM 任意文本对 baseline cosine 0.4-0.6
- **修复**：调到 0.70，候选降到 ~4000

### Bug 2: Track B 没缓存
- **现象**：跑到一半 Ctrl+C 就要全重跑
- **修复**：加 `DiskCache` 到 `.llm_cache/`，前缀 `SYNJUDGE|`，断点续跑

### Bug 3: 单 batch 跑完一句日志没有
- **现象**：250 个 batch 跑 12 分钟，全程黑盒
- **修复**：每 batch 打 `+K accepted (running total X / Y evaluated)`

### Bug 4: bat 括号炸 if 块
- **现象**：Phase 3 结束后报 `此时不应有 returned。`
- **根因**：`echo [FAIL] %DESC% returned ...` 里 `%DESC%` 含 `(...)`，提前关闭 if 块
- **修复**：改用延迟展开 `!DESC!`

### Bug 5: bat 行尾符问题
- **现象**：所有 `'M' 不是内部或外部命令` 错误，`setlocal` 被读成 `etlocal`
- **根因**：Write 工具写出的 bat 是 LF 行尾，cmd 误读吃掉每行首字符
- **修复**：用 PowerShell 转 CRLF + 加 fix-line-endings 命令到 IMPROVEMENTS

### Bug 6: 评估跑的是旧 KG
- **现象**：跑完 phase 1-4 但评估指标和旧 KG 一模一样
- **根因**：phase_5/phase_6 是 5/6 的旧文件（2 周前），bat 的 skip 逻辑跳过了它们
- **修复**：删除 stale phase 5/6/compare 文件 + 加诊断说明

### Bug 7: Neo4j 0 条边
- **现象**：所有边相关 Cypher 返回 (no results)
- **根因**：旧 KG 的 `concept_id` 是 UUID，但 relationship 用 canonical_name 作 source_id，MATCH 全部失败
- **修复**：builder.py 改 `concept_id = canonical_name`

### Bug 8: KG 评估多小时卡死
- **现象**：BM25 跑完，KG 评估 `Loading sentence-transformers model: all-MiniLM-L6-v2` 后挂起
- **根因**：每个 issue × 每个多义概念 × 现场 embed 所有 cluster 定义。`path` 216 簇 × 91 issue = 几小时
- **修复**：构图时一次性 embed 所有合理多义概念的 cluster 向量并缓存；超过 30 簇的概念跳过消歧

---

## 14. 最终评估数字

### 总览（91 条 SWE-bench Pro openlibrary instance）

| 指标 | BM25 | KG-walk | 差距 |
|---|---|---|---|
| File Recall@10 | **92.3%** | 80.2% | KG -12.1 |
| File MRR | **0.758** | 0.449 | KG -0.31 |
| Function Recall@10 | **84.6%** | 65.9% | KG -18.7 |
| Function MRR | **0.668** | 0.342 | KG -0.33 |

### 交叉分析（91 条 issue 各自击中情况）

| | File-level | Function-level |
|---|---|---|
| 两方都命中 | 72 | 55 |
| 只有 BM25 命中 | **12** | **22** |
| 只有 KG 命中 | **1** | **5** |
| 都没命中 | 6 | 9 |

### 诚实结论

**KG 在 issue 定位这个任务上不如 BM25。**

原因不是调参问题，是**信号密度问题**：

| 信号 | BM25 看得到 | KG 看得到 |
|---|---|---|
| 完整文件源码 | ✓ | ✗ |
| 标识符 | ✓ 全部 | ✓ 过滤后子集 |
| 注释 / docstring | ✓ | ✗ |
| 字符串字面量（URL/error msg 等） | ✓ | ✗ |

SWE-bench Pro 的 issue 通常**直接含有代码标识符**（函数名、类名），BM25 字面命中就是赢家。KG 的"概念扩展"反而稀释了原本应该排第一的命中。

KG 唯一独家命中的 1 个 issue：
> "/lists/add returns 500 error when POST data conflicts with query parameters"

—— 这种"语义化提问、不含具体标识符"的 issue 才是 KG 主场，但在 SWE-bench Pro 里只占 ~1%。

### 数据规模对比

| | 旧 KG | 新 KG |
|---|---|---|
| Concepts | 5,369 | **3,760** |
| Relationships | 3,447（全 co-occurrence） | **2,562** (1,382 syn + 1,180 cooc) |
| 多义概念 | 0 | **499**（含过度分簇问题） |
| LLM 定义 | 0（毒缓存） | **21,281**（全部成功） |
| Top 概念是什么 | stdlib 噪声 | openlibrary 领域词 |
| 孤立节点 | 4,411 (82%) | **2,014 (54%)** |

---

## 15. 仍未做、留待决策的事

| 项 | 说明 | 触发条件 |
|---|---|---|
| **Phase 4 eps 调优** | `path` 216 簇明显过度分簇，eps 从 0.35 调到 0.55-0.65 应能压到 ~30-80 真多义 | 跑一次确认效果 |
| **降低孤立节点比例** | 54% 概念无任何边。可能改善：放宽共现 min_count、加 call graph 一跳 | 你想让 KG 更密 |
| **BM25 + KG 混合方法** | hybrid score = α·BM25 + (1-α)·KG，预计能涨 1-3 个点 Recall@10，且报告里能讲故事 | 你选 §3 末尾的选项 1 |
| **改换 thesis 叙事** | 不追求超过 BM25，重点说"KG 抽取质量、三种关系清晰度、Neo4j 可查询性" | 你选选项 2 |
| **call graph 一跳作为共现** | 把 module-level 共现替换成"被同一个调用链触达" | 大改，需要静态调用图分析 |
| **issue→KG 概念的更智能映射** | 当前是 token 字面匹配，可以加 issue 文本 embedding ↔ 概念定义 embedding | 提高 KG-walk 的下游表现 |
| **Neo4j 边按 track 拆类型** | 把 :SYNONYM 拆成 :SYNONYM_VARIANT 和 :SYNONYM_DOMAIN，Cypher 查询更精准 | 视实际查询需要 |
| **CI/CD 化测试** | 现在每次重跑都靠人盯进度日志 | 长期工程化方向 |

---

## 16. 改动文件全清单

### 修改的文件（13 个）
```
config.yaml                                                  阈值/模型名
openlibrary_kg/config.py                                     dataclass 新字段
openlibrary_kg/extraction/noun_filter.py                     重写
openlibrary_kg/extraction/name_splitter.py                   默认走 HARD_BLOCKLIST
openlibrary_kg/llm/prompt_templates.py                       加同义词判定 prompt
openlibrary_kg/llm/definition_generator.py                   重写
openlibrary_kg/relationships/synonyms.py                     重写（两路 + 缓存）
openlibrary_kg/relationships/polysemy.py                     双闸门
openlibrary_kg/relationships/cooccurrence.py                 重写（子域）
openlibrary_kg/graph/builder.py                              concept_id = name
openlibrary_kg/graph/neo4j_exporter.py                       边属性扩充
scripts/extract_concepts.py                                  二次覆盖过滤 + 跳 import
scripts/generate_definitions.py                              重写 + 分层抽样
scripts/detect_synonyms.py                                   接入 LLM client
scripts/analyze_polysemy.py                                  传新参数
scripts/analyze_cooccurrence.py                              传新参数
```

### 新增的文件（8 个）
```
openlibrary_kg/downstream/__init__.py
openlibrary_kg/downstream/ground_truth.py                    旧版（GitHub API 抓取）
openlibrary_kg/downstream/swebench_loader.py                 SWE-bench Pro 接入
openlibrary_kg/downstream/issue_localization.py              定位算法 + 评估
openlibrary_kg/downstream/baselines.py                       BM25
scripts/build_issue_ground_truth.py
scripts/build_swebench_ground_truth.py
scripts/compare_methods.py                                   BM25 vs KG 对照
scripts/locate_issue.py                                      单 issue / 评估
scripts/run_kg_queries.py                                    Cypher 查询脚本
run_all.bat                                                  全流程编排
CURRENT_STATE.md
CHANGES.md（早期版本）
IMPROVEMENTS.md（本文）
```

---

## 17. 怎么跑（速查）

```cmd
REM 30 分钟演示 + Neo4j 导入 + 查询
run_all.bat demo

REM 续跑（跳过已有输出）
run_all.bat

REM 单 issue 试试看
python scripts\locate_issue.py --title "User can't log in with email"

REM 评估某个 ground truth
python scripts\locate_issue.py --eval --ground-truth output\swebench_ground_truth.json --top-k 10

REM BM25 vs KG 对比
python scripts\compare_methods.py --top-k 10

REM Neo4j 单独跑
python scripts\export_to_neo4j.py --clear
python scripts\run_kg_queries.py --out output\kg_queries_report.md
python scripts\run_kg_queries.py --concept user

REM 行尾符 fix（如果再出 'M' 不是命令错误）
powershell -Command "$p='run_all.bat'; $t=[IO.File]::ReadAllText($p); [IO.File]::WriteAllText($p, ($t -replace \"`r`n\", \"`n\" -replace \"`n\", \"`r`n\"), [Text.UTF8Encoding]::new($false))"
```
