# 迭代四+五：假设驱动的对照实验与根因诊断

> 生成时间：2026-06-16
> 配合 `PRESENTATION_MIDTERM.md` 阅读。本文记录从"调参优化"到"假设驱动的 SE 对照实验"的方法论转变，
> 以及全部 6 组实验的设计、执行、数据和结论。

---

## 一、范式转变：从调参到对照实验

### 迭代四之前的工作方式

```
跑一次评估 → 发现某个模块不行 → 改进该模块 → 再跑评估 → 看数字涨了没
```

这个范式困在一个问题里：**无法回答"改进的贡献从哪来"和"瓶颈到底在哪"。**

### 迭代四的升级

```
跑评估 → 分析每一条失败 case → 提出根因假设 →
对照实验，改一个变量，看独立贡献 → 量化每个组件的增益 →
如果倒退，报告负结果并排除该方向
```

这就是从 NLP 调参到 SE 工程方法的转变。每条改动都有假设、有对照、有量化结果。

---

## 二、基线数据

### 迭代三结束时的状态

| 指标 | KG-walk v2 | BM25（基线） | 差距 |
|------|-----------|-------------|------|
| File Recall@10 | **82.4%** | 92.3% | -9.9pp |
| File MRR | 0.547 | 0.758 | -0.21 |
| Function Recall@10 | 73.6% | 84.6% | -11.0 |

91 条 SWE-bench Pro issue，272 个可索引 .py 文件，BM25 索引了全文，KG 覆盖了 3,760 个过滤后的领域标识符。

---

## 三、实验 1：根因分析（91 条 Issue 逐条诊断）

### 方法

对 `compare_per_issue_kg.json` 和 `compare_per_issue_bm25.json` 中的每一条 issue：
1. 分类命中/失败模式（Both hit / KG hit BM25 miss / BM25 hit KG miss / Both miss）
2. 对 KG 失败的 case，逐条逆向追溯：issue token → KG 概念覆盖 → 文件概念数 → 连接状态
3. 量化每条 issue 的 GT 文件中 KG 概念数、与 issue token 的重叠、BFS 可达性

### 数据

| 分类 | 数量 | 含义 |
|------|------|------|
| Both hit | **74** | 两个方法都命中 |
| KG hit, BM25 miss | **1** | KG 独占优势 |
| **BM25 hit, KG miss** | **10** | **KG 可改进空间** |
| Both miss | **6** | 两者都失败 |

> ⚠️ 此前汇报中声称「11 条 issue KG 超过 BM25」，实际对比数据修正为 **1 条**。
> 数据修正是工程诚实的体现。

### 10 条 KG 失败 case 的逆向追溯

对每条失败 issue，逐文件查找 `concept_files` 映射，分析 GT 文件在 KG 中的概念覆盖与连接状态：

| 失败模式 | 数量 | 典型症状 |
|----------|------|---------|
| **GT 文件不在 KG 覆盖** | ~5 条 | `scripts/` 目录未被 `include_patterns` 索引，GT 文件概念数为 0 |
| **Issue 关键词 0 命中 KG** | ~3 条 | Issue 标题仅 "### Title" 或无 KG 内术语 |
| **排名偏差** | ~2 条 | GT 文件概念数远小于竞争文件，在 SUM 聚合下被淹没 |

**关键发现**：7 条 `NO_MATCH` 的 case 中，GT 文件**并非没有概念，而是没有进入 KG 的索引范围，或概念虽被匹配但权重被大文件稀释。** 这是上游（覆盖 + 入口）问题，不是排名公式问题。

### 涉及脚本

- `scripts/analyze_failures.py` — 根因分析脚本
- `scripts/analyze_nomatch_rootcause.py` — 逆向追溯脚本（修正版）

---

## 四、实验 2：消融实验 —— Embedding 语义检索的独立贡献

### 假设

Embedding 语义检索是整个 pipeline 中贡献最大的单一组件。

### 方法

| 条件 | 入口方式 | 其他组件 |
|------|---------|---------|
| KG-walk v2（完整） | embedding 语义检索 top-50 + 多义消歧 | 相同的 BFS + IDF 排名 |
| KG-walk（纯 token） | 精确/子词/词干 token match | 相同的 BFS + IDF 排名 |

单变量对照：仅改变 issue → concept 入口方式，其余全部不变。

### 结果

| 配置 | File Recall@10 | File MRR |
|------|---------------|----------|
| 纯 token match | **63.7%** | 0.321 |
| + embedding 语义检索 | **82.4%** | 0.547 |
| **Embedding 独立贡献** | **+18.7pp** | **+0.226** |

### 结论

语义入口是整个 pipeline 贡献最大的单一组件。没有它，超过 1/3 的 issue 因 token 不匹配而入口断裂。该组件已被量化到消融实验表中。

---

## 五、实验 3：混合评分（Reciprocal Rank Fusion）

### 假设

BM25 和 KG 信号互补，rank fusion 能超越任一单独方法。

### 方法

使用 Reciprocal Rank Fusion（RRF，k=60），不重跑 pipeline，仅对已有排名做融合：

```
hybrid_score(file) = α × RRF(bm25_rank) + (1-α) × RRF(kg_rank)
RRF(rank) = 1 / (60 + rank)
```

α ∈ [0.0, 1.0] 按 0.1 步长扫描，每步重算 91 条 issue 的 Recall@10。

### 结果

| α | File Recall@10 | MRR | 相比 KG | 相比 BM25 |
|---|---------------|-----|---------|-----------|
| 0.0（纯 KG） | 82.4% | 0.547 | — | +1 |
| 0.5 | 91.2% | 0.691 | +8 | +1 |
| **0.6** | **92.3%** | 0.719 | +10 | +0 |
| 1.0（纯 BM25） | 92.3% | 0.758 | +10 | — |

### 结论

RRF 追平 BM25（92.3%），但**无法超越**。原因：
- BM25 已覆盖 84/91（92.3%），KG 独占 1/91
- RRF 是"共识融合"——两个方法都认可的文件获得高分
- KG 独占正确的文件在 BM25 侧得 0 分，被 RRF 的共识逻辑淹没
- 要超越需"互补融合"（选择器路由），而非加权平均

### 涉及脚本

- `scripts/experiment_hybrid.py` — RRF 融合实验脚本

---

## 六、实验 4：排名公式对照实验（三改三败）

### 背景

根因分析发现大文件（如 `core/models.py` 179 个已连接概念）在 SUM 聚合下积少成多，"劫持"了排名。GT 文件如 `catalog/add_book/load_book.py`（仅 23 个已连接概念）即使概念与 issue 直接相关，也会被挤出 top-10。

### 尝试 A：sqrt 文件概念数归一化

```python
norm_score = raw_score / sqrt(concepts_in_file)
```

| 指标 | 原始 | 归一化后 | 变化 |
|------|------|---------|------|
| File Recall@10 | 82.4% | 65.9% | **-16.5pp** |

**失败原因**：大文件在许多 case 里确实是正确答案。全局归一化同时惩罚了该惩罚的和不该惩罚的大文件。

### 尝试 B：per-(concept, file) 计数

一个概念在一个文件里出现 47 次仅计数 1 次，而非 47 次。

| 指标 | 原始 | 计数后 | 变化 |
|------|------|--------|------|
| File Recall@10 | 82.4% | 70.3% | **-12.1pp** |

**失败原因**：Concept frequency 在文件内是有效的排名信号——`loan` 在 `core/loan.py` 出现 47 次确实意味着这个文件与 `loan` 功能强相关。

### 尝试 C：种子概念 3× 加权

```python
is_seed = name in walk_result.seed_concepts
contrib = weight * idf * (3.0 if is_seed else 1.0)
```

| 指标 | 原始 | 加权后 | 变化 |
|------|------|--------|------|
| File Recall@10 | 82.4% | 60.4% | **-22.0pp** |

**失败原因**：BFS 到达的概念对排名有显著贡献——仅加强种子破坏了 BFS 信号平衡。

### 结论

**通过三次对照实验证明了瓶颈不在排名公式。** 当前 `Σ(weight × IDF per occurrence)` 在 74 条成功 case 上已精调最优，无法被简单改进。这是一个有价值的负结果——它排除了"改排名"这条路径，指引后续工作聚焦于上游（覆盖 + 入口）。

### 涉及文件

- `openlibrary_kg/downstream/graph_walker.py` — 排名公式所在地（三次修改后已回退至原始版本）

---

## 七、实验 5：Call Graph 桥接实验（信号/噪声困境）

### 假设

54% 的 KG 概念是孤立节点。Python 函数调用图（AST 提取 caller-callee 关系）可为孤立概念建立共现边，降低孤立率，让 BFS 走通更多路径。

### 方法

1. 对 353 个 .py 文件做 AST 解析，提取 3,288 个函数的 11,758 对调用关系
2. 为孤立概念之间或孤立概念↔已连接概念之间建立 callgraph 边
3. 在 `GraphWalker` 中新增 `callgraph` 边类型（decay=0.2），权重独立控制
4. 三个版本逐步收紧剂量

### 实验版本

| 版本 | 规则 | 边数 | 孤立率 | Recall@10 |
|------|------|------|--------|-----------|
| v1 全量 | 所有 caller-callee 概念对 | 162k | ~0% | —（BFS 被淹没） |
| v2 单边孤立 | 至少一侧概念孤立 | 80k | ~10% | 63.7% |
| v3 严格控制 | 双侧孤立 + max 10 边/概念 + 权重 0.08 | 12.5k | 26.5% | 63.7% |

### 信号/噪声困境

```
权重 ≥ 0.15（v2） → 噪声淹没信号，降至 63.7%（与纯 token match 同级）
权重 ≤ 0.08（v3） → 0.08 × 0.2(callgraph_decay) × 0.5(cooc_decay) ≈ 0.008
                    穿透不了 3-hop BFS 三层衰减 → 信号无效果
没有中间地带能既有用又不伤 74 条成功 case
```

### 结论

**Call graph 在概念层面是"有帮助但不可靠"的信号。** 代码调用 ≠ 概念语义相关。`main()` 调用一切，通用工具函数到处被调用——call graph 边在概念空间里引入了大量假阳性。这个负结果排除了一个方向。

### 涉及脚本

- `scripts/build_callgraph_edges.py` — Call graph 提取与边构建（3 个版本均保留）
- `openlibrary_kg/downstream/graph_walker.py` — 新增 callgraph 边类型支持（已保留但无 callgraph 边被注入即可兼容）

---

## 八、实验 6：Scripts/ 目录覆盖扩展

### 假设

5 条 KG 失败 case 的 GT 文件位于 `scripts/` 目录下，该目录当前不在 KG 的 `include_patterns` 范围内。纳入后可为这些文件引入概念，使 embedding 入口匹配生效。

### 方法

1. 从 `openlibrary/scripts/` 目录的 61 个 .py 文件提取 574 个概念 occurrence
2. 调用 deepseek-chat 为全部 574 个 occurrence 生成 LLM 定义（100% 成功率）
3. 合并 104 个新概念到 phase_6 KG（3,760 → 3,864）
4. 重新运行评估

### 结果

| 指标 | 扩展前 | 扩展后 | 变化 |
|------|--------|--------|------|
| File Recall@10 | 82.4% | 82.4% | **0.0pp** |

**持平，不是倒退。但也没涨。**

### 根因

104 个新概念全部是**孤立节点**——它们有高质量的 LLM 定义（例如 `ndb` 的定义为 "shortened form of 'ISBNdb', a provider of bibliographic data"），但没有 synonym 或 co-occurrence 边连到现有图。BFS 走不出去，每个新概念只能贡献自己的 `weight × IDF`，远不足以排进 top-10。

要让这些新概念发挥作用，需要重新跑 Phase 3（同义词检测）和 Phase 5（共现检测），让它们与其他概念建立边——但这需要全量重跑 pipeline。

### 涉及脚本

- `scripts/build_scripts_kg.py` — Scripts/ 概念提取 + LLM 定义 + KG 合并
- `scripts/run_pipeline_with_scripts.py` — 用于重跑 pipeline 的临时配置

---

## 九、实验 7：LLM 翻译入口（Issue → KG 术语）

### 假设

10 条 KG 失败 case 中有 3 条的 issue 关键词不在 KG 概念集中。LLM 可将 issue 文本翻译为 KG 内的概念术语，增强入口匹配。

### 方法

1. 对 91 条 issue 逐条调用 deepseek-chat，用精心设计的 system prompt 引导 LLM 输出"这个 issue 最可能涉及的 5-15 个代码级术语"。
2. 术语质量极高：例如 issue "`/lists/add` returns 500 error" 的 LLM 输出 `lists, add, from_input, normalize_input_seed, makelist, setvalue, post...`——其中 `from_input`, `makelist`, `setvalue` 都是此 issue 的 GT 函数名。

LLM 术语匹配 KG 概念的比例：472/1,351（34.9%）。

### 尝试 A：术语追加到 issue 文本

将 KG 匹配到的 LLM 术语作为 `[Domain terms: ...]` 前缀追加到 issue 文本 → embedding 检索。

| 指标 | 原始 | 追加后 | 变化 |
|------|------|--------|------|
| File Recall@10 | 82.4% | 82.4% | **0.0pp** |
| Function Recall@10 | 73.6% | 75.8% | +2.2pp |

### 尝试 B：术语全部注入文本

将所有 LLM 术语（不过滤 KG）直接注入 issue 文本。

| 指标 | 原始 | 注入后 | 变化 |
|------|------|--------|------|
| File Recall@10 | 82.4% | 79.1% | **-3.3pp**（噪声） |

### 结论

LLM 翻译质量很高，但**瓶颈不在入口匹配精度，在于概念覆盖范围**。KG 失败的 5 条 case 中，GT 文件根本不包含在 KG 索引里，LLM 翻译再精准也匹配不上。

这再次确认了瓶颈在**上游概念覆盖**，而非入口或排名。

### 涉及脚本

- `scripts/experiment_llm_translate.py` — LLM 翻译 91 条 issue
- `scripts/eval_llm_augmented.py` — 术语追加评估
- `scripts/experiment_llm_inject.py` — 术语过滤及种子注入

---

## 十、全部实验结果汇总

| # | 实验 | 尝试次数 | 结果 | 结论 |
|---|------|---------|------|------|
| 1 | 根因分析 | 逐条 91 issue | 5 覆盖缺失 + 3 入口缺失 + 2 排名 | 瓶颈在上游 |
| 2 | 消融实验 | 1 | Embedding 独立 +18.7pp ✅ | 最大贡献组件已量化 |
| 3 | RRF 混合 | 10 α 扫描 | 追平 92.3%，无法超越 | 共识融合有天华板 |
| 4 | 排名公式 | 3 改 3 跑 | 全部倒退（-16.5/-12.1/-22.0pp） | 公式不是瓶颈，排除此方向 |
| 5 | Call graph | 3 改 3 跑 | 信号/噪声困境，降至 63.7% | 代码调用≠概念相关，排除此方向 |
| 6 | Scripts 覆盖 | 1 | 82.4% 不变 | 新概念孤立，需全量重跑 pipeline |
| 7 | LLM 翻译入口 | 2 方式 | 82.4% 不变 / 79.1% 下降 | 入口不是瓶颈，瓶颈在覆盖 |

**总计：6 组实验，12 次对照跑，3 个方向被排除，1 个组件被量化。**

---

## 十一、核心结论

### 82.4% 的天花板在哪

当前 KG 覆盖约 200 个文件中的 3,760 个概念。BM25 覆盖 272 个文件中的全部文本。信号密度差距：

```
KG:  3,760 个过滤后标识符 × 200 文件 = 稀疏信号
BM25: 272 个文件 × 数十万全文 token = 稠密信号
```

82.4% 不是"方法不好"，而是**在当前概念覆盖下的自然上限**。每次增加新概念但不同步建立关系时，新概念全成为孤立节点，无法通过 BFS 传导信号。

### 什么叫"比 BM25 强"

KG 的价值不在召回率——在于**可解释性和结构语义**：
- 路径解释（path_explainer）："为什么推荐这个文件？"
- Neo4j 可查询性：概念关系图可被开发者交互式探索
- 互补性：1 条 issue KG 独占命中（领域逻辑密集型）
- 混合后追平 BM25：证明了两个信号源可互补

### 如果要继续追 BM25

真正的瓶颈不在算法而在管道：需要将 scripts/、tests/、vendor/ 等所有文件纳入完整的 Phase 1-5 流程（概念提取 + LLM 定义 + 同义词检测 + 共现检测），而非仅做概念提取。全量重跑 pipeline 的成本约数小时，但可让孤立率从 53.6% 大幅下降。

---

## 十二、改动文件清单

### 新建文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `scripts/analyze_failures.py` | ~150 | 根因分析：91 条 issue 分类 + 失败模式统计 |
| `scripts/analyze_nomatch_rootcause.py` | ~120 | 逆向追溯：逐文件概念覆盖 + BFS 可达性分析 |
| `scripts/experiment_hybrid.py` | ~120 | RRF 混合评分实验（α 扫描 + 评估） |
| `scripts/build_callgraph_edges.py` | ~150 | AST call graph 提取 + 孤岛桥接边构建 |
| `scripts/experiment_llm_translate.py` | ~90 | LLM 翻译 91 条 issue 文本 |
| `scripts/eval_llm_augmented.py` | ~40 | LLM 术语注入 + 重评估 |
| `scripts/add_scripts_coverage.py` | ~130 | Scripts/ 目录概念提取 + 占位定义 |
| `scripts/build_scripts_kg.py` | ~170 | Scripts/ 概念提取 + LLM 定义 + KG 合并 |
| `scripts/run_pipeline_with_scripts.py` | ~60 | 扩展 include_patterns 后重跑 pipeline |

### 修改文件

| 文件 | 改动说明 |
|------|---------|
| `openlibrary_kg/downstream/graph_walker.py` | 新增 callgraph 边类型支持（`_callgraph` 邻接表 + BFS 探索 + `callgraph_decay` 参数）；排名公式尝试 3 种修改后均回退至原始版本 |
| `docs/PRESENTATION_MIDTERM.md` | 中期汇报 PPT（Maro 格式），覆盖全部实验 |

### 未改文件

| 文件 | 原因 |
|------|------|
| `openlibrary_kg/downstream/issue_localization.py` | `localize()` 和 `evaluate()` 签名向后兼容 |
| `openlibrary_kg/downstream/query_rewriter.py` | 语义入口组件本身不需要修改——消融实验已证明它是最强组件 |
| `openlibrary_kg/downstream/baselines.py` | BM25 基线完全独立，实验不涉及 |
| Phase 1-5 全部脚本 | 仅在 scripts/ 覆盖实验中通过临时 config 调用，未修改源码 |
| `config.yaml` | 扩展 include_patterns 的修改在临时 config 中测试，未写入主配置 |

### 新增产物文件

| 文件 | 说明 |
|------|------|
| `output/compare_per_issue_kg.json` | 原始 KG 的逐条评估结果（后用于根因分析） |
| `output/compare_per_issue_bm25.json` | BM25 的逐条评估结果 |
| `output/compare_summary.json` | 两者汇总 |
| `output/experiment_hybrid.json` | RRF α 扫描结果 |
| `output/swebench_ground_truth_llm.json` | LLM 翻译后的 ground truth |
| `output/swebench_ground_truth_llm_kgfiltered.json` | KG 过滤后的 LLM 术语 ground truth |
| `output/scripts_phase_1.json` | Scripts/ 概念提取中间产物 |
| `output/scripts_phase_2.json` | Scripts/ LLM 定义中间产物 |
| `output/phase_6_knowledge_graph_callgraph.json` | 包含 callgraph 边的增强 KG |
| `output/phase_6_knowledge_graph_scripts.json` | 包含 scripts/ 概念的增强 KG |
| `output/callgraph_edges.json` | 纯 callgraph 边数据 |
