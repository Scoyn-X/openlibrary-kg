# 迭代七：软索引、策略路由与 LLM-native baseline

> 生成时间：2026-06-27

---

## 一、背景

迭代六结束时，KG 独立 File Recall@10 = 84.6%，MRR = 0.580，混合追平 BM25（92.3%）。
但存在三个未解决的结构性问题：

1. **BM25 作为唯一 baseline 已经过时**——1994 年的方法不能代表 2026 年的前沿水平
2. **Issue 文本与 KG 概念之间存在语义交集之外的盲区**——被 HARD_BLOCKLIST 挡掉的词（`requests`/`urllib`）在 issue 定位中反而是关键信号
3. **项目缺少"整体意识"**——KG 只是机械地做 BFS + SUM 排名，没有区分 issue 类型、没有架构感知能力

本次迭代围绕这三个问题展开。

---

## 二、方向 A：软索引 —— 为被过滤的词建立辅助信号

### 问题

Phase 1 的 `HARD_BLOCKLIST`（411 词）会过滤掉 `requests`、`urllib`、`http`、`import` 等标准库/框架词。这些词在 KG 概念图中是噪声——但如果一个 issue 说 "refactor to use requests instead of urllib"，这些词恰好是最关键的定位信号。

### 方案

在 Phase 1 概念提取时，将被 block 的 token 和被过滤掉的完整标识符记录到 `output/soft_index.json`：
- `{token: [file_path, ...]}` 映射
- import 类型 occurrence 的 token 也纳入（之前被直接跳过）
- 被完全 block 的复合标识符（如 `read_subjects`）的完整 raw identifier 也纳入

### 结果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 软索引 token 数 | 0 | 3,996 |
| 含 import 的 token | 无 | 881→1,771→3,996 |

`get_ia.py` case：`requests` 软索引连接到了 48 个文件，其中包括 GT 文件 `get_ia.py`。

但 **Recall@10 不变（84.6%）**。根因：

- 没有"零 KG 命中"的 issue——每条 issue 至少匹配 15 个 KG 概念，软索引 fallback 从未触发
- 被挡词的 IDF 太低——`requests` 分布在 48 个文件中，每个文件 boost 仅 +0.462，无法改变 top-10

**结论**：软索引填补的是"KG 完全没有信号"的场景——但这个场景不存在。每条 issue 都能在 KG 里找到至少 15 个概念。瓶颈不在"信号太少"，而在"信号不够精准"。

### 涉及文件

| 文件 | 操作 | 目的 |
|------|------|------|
| `scripts/extract_concepts.py` | 修改 | Pass 1 新增软索引构建（import tokens + blocked tokens + raw identifiers） |
| `openlibrary_kg/downstream/issue_localization.py` | 修改 | 新增 `soft_index_path` 参数、`_apply_soft_index_boost()`、`_soft_index_only_ranking()` |
| `output/soft_index.json` | 新增 | 3,996 个 token → 文件映射 |

---

## 三、方向 B：策略路由 —— Issue 分类 + 架构感知

### 问题

当前的 BFS + SUM 排名对所有 issue 用同一个公式。但不同类型的 issue 在代码库里对应不同的搜索空间和推理路径：
- `POST /lists/add` 的 API 路由类 → 应锁定 `plugins/` + `fastapi/` 子目录
- `solr reindex` 的搜索类 → 应锁定 `solr/` + `plugins/worksearch/`
- `MARC parse` 的编目类 → 应锁定 `catalog/` + `core/`

### 方案

新增 `openlibrary_kg/downstream/strategy_router.py`：
- **Issue 分类器**：8 种类型（API_ROUTE / SOLR_SEARCH / MARC_CATALOG / SCRIPT_TOOL / REFACTOR / UI_FRONTEND / DOMAIN_LOGIC / GENERAL），基于正则规则
- **架构感知子域聚焦**：每种类型绑定了"应该看哪些子目录"
- **分类感知 α 加权**：BM25-primary 类型（MARC/Solr/脚本）用高 BM25 权重，KG-primary 类型（API/UI/领域逻辑）用高 KG 权重

### 91 条 SWE-bench 分类分布

| 类型 | 数量 | KG 命中 | BM25 命中 |
|------|------|---------|-----------|
| MARC/编目 | 56 | 52% | **95%** |
| Solr/搜索 | 30 | 47% | **87%** |
| API/路由 | 23 | 61% | **87%** |
| 修复/重构 | 22 | 59% | **95%** |
| 脚本/工具 | 18 | 61% | **89%** |
| UI/前端 | 13 | 69% | **77%** |
| 通用/其他 | 7 | **86%** | 100% |

### 结果

分类策略路由 = **91.2%**，低于统一 RRF 的 92.3%（-1.1pp）。

**结论**：分类感知的融合权重调整不足以提升指标——SWE-bench 的 issue 分布偏向 MARC 类（56/91），而这类恰好是 BM25 最强、KG 最弱的领域。策略路由的价值不在于"提升分数"，而在于**架构感知能力——知道不同 issue 应该看代码库的哪些部分**。这是后续"输出架构层次标注"和"影响范围分析"的基础。

### 涉及文件

| 文件 | 操作 | 目的 |
|------|------|------|
| `openlibrary_kg/downstream/strategy_router.py` | **新增** | Issue 分类器 + 策略路由 + 子域提取 |
| `scripts/analyze_issue_types.py` | **新增** | 91 条 issue 分类 + 分类型 BM25/KG 对比分析 |
| `scripts/analyze_subdomain_perf.py` | **新增** | 按 GT 子域名分析 KG vs BM25 命中率 |
| `scripts/eval_strategy_router.py` | **新增** | 策略路由器评估脚本 |

---

## 四、LLM-native baseline —— 现代前沿对比

### 问题

BM25 诞生于 1994 年。在 2026 年，LLM（GPT-4o / Claude）可以直接阅读 issue 描述 + 文件列表，猜测哪些文件需要修改。如果把 BM25 作为唯一的 baseline，我们实际上是在和一个 30 年前的方法较劲。

### 方案

新增 `scripts/eval_llm_baseline.py`：
- 对每条 SWE-bench issue，将 issue 文本 + 283 个源文件列表发送给 LLM
- 要求 LLM 输出 top-10 最可能需要修改的文件（JSON 格式）
- 支持 OpenAI 兼容 API（OpenAI / DeepSeek / OpenRouter）

### 实验结果

**使用 GPT-4o（via OpenRouter），temperature=0.0：**

| 方法 | Recall@10 | MRR | Top-1 | 成本 |
|------|-----------|-----|-------|------|
| BM25（1994） | **92.3%** | 0.758 | 66% | 免费，毫秒 |
| **KG-walk（2026）** | 84.6% | 0.580 | 41% | **免费，秒级，可解释** |
| GPT-4o（2024） | 85.7% | 0.699 | 61.5% | ~$1 |
| KG + BM25 混合 | **92.3%** | 0.726 | — | 免费 |

### 核心发现

- **GPT-4o（85.7%）仅比 KG（84.6%）高 1.1 个百分点**——且 GPT-4o 用了全部 283 个文件的文件名、用了预训练时的"世界知识"
- **BM25 在关键词密集类 issue（MARC/Solr/脚本）上碾压一切**——这是方法本质决定的：这些 issue 的关键词（`MARC`、`solr`、`isbn`）恰好是最高区分度的全文检索 token
- **KG 的差异化价值不在召回率，在可追溯性和零成本推理**——GPT-4o 和 BM25 都无法解释"为什么推荐这个文件"，也无法被审计

### 涉及文件

| 文件 | 操作 | 目的 |
|------|------|------|
| `scripts/eval_llm_baseline.py` | **新增** | LLM-native baseline 评估脚本 |
| `llm_baseline_config.json` | **新增** | LLM 配置（含 API Key） |
| `output/llm_baseline_eval.json` | **新增** | 评估结果 |

---

## 五、KG 独有的优势

| 能力 | BM25 | GPT-4o | KG |
|------|------|--------|-----|
| 定位精度 | 最高 | 高 | 高 |
| 推理可追溯 | ❌ | ❌ 黑箱 | ✅ "loan→borrow_record→patron→lending.py" |
| API 成本 | 免费 | 每次 ~$0.01-0.05 | **免费** |
| 可审计性 | ❌ | ❌ | ✅ 能检查每条边对不对 |
| 交互查询 | ❌ | ❌ | ✅ Neo4j 可查询概念关系 |
| 路径解释 | ❌ | ❌ | ✅ PathExplainer |
| 部署依赖 | 无 | API 网络 | 纯本地 |

---

## 六、改动文件清单

| 文件 | 操作 | 目的 |
|------|------|------|
| `openlibrary_kg/downstream/strategy_router.py` | **新增** | Issue 分类 + 策略路由 + 架构感知 |
| `openlibrary_kg/downstream/issue_localization.py` | 修改 | 软索引集成（参数 + boost + fallback） |
| `scripts/extract_concepts.py` | 修改 | Phase 1 软索引构建 |
| `scripts/eval_llm_baseline.py` | **新增** | LLM-native baseline 评估 |
| `scripts/eval_strategy_router.py` | **新增** | 策略路由评估 |
| `scripts/analyze_issue_types.py` | **新增** | Issue 分类 + 分类型分析 |
| `scripts/analyze_subdomain_perf.py` | **新增** | 子域名命中率分析 |
| `scripts/debug_soft_index.py` | **新增** | 软索引调试 |
| `scripts/test_soft_index.py` | **新增** | 软索引离线测试 |
| `llm_baseline_config.json` | **新增** | LLM 配置（已 gitignore） |
| `output/soft_index.json` | **新增** | 软索引数据 |
| `output/llm_baseline_eval.json` | **新增** | LLM baseline 评估结果 |
| `.gitignore` | 修改 | 新增 `llm_baseline_config.json` |

### 未改文件

| 文件 | 原因 |
|------|------|
| `openlibrary_kg/kg_query.py` | 迭代六已完成平台化，本次无需修改 |
| `openlibrary_kg/downstream/graph_walker.py` | 密度乘数在迭代六已添加 |
| 其余 Phase 1-6 全部脚本 | 软索引改动仅涉及 `extract_concepts.py`，不影响后续 phase |

---

## 七、最终结论

### 基准全貌

| 方法 | Recall@10 | MRR | Top-1 | 成本 |
|------|-----------|-----|-------|------|
| BM25（全文检索，1994） | **92.3%** | 0.758 | **66%** | 免费 |
| KG-walk（本课题，2026） | 84.6% | 0.580 | 41% | 免费 |
| KG + BM25 混合 | **92.3%** | 0.726 | — | 免费 |
| GPT-4o（LLM-native，2024） | 85.7% | 0.699 | 61.5% | API 调用 |

### 我们做的是什么

**不是"另一个搜索引擎"。** 87 天前我们只有 0 条 LLM 定义和"标识符共现图"。现在我们有 4,338 个领域概念的语义图谱，在 issue 定位上和 GPT-4o 的差距仅 1.1 个百分点，且不需要任何 API 调用。

**这张图的价值不在追 BM25 的分。** 它的价值是给未来的 AI 编程 Agent 准备一张**确定的、可追溯的、零成本的代码库语义导航地图**——Agent 可以通过 ACI（Agent-Computer Interface）查询 KG 理解代码结构，找到影响范围，并解释推理路径。这是 BM25（词袋）和 GPT-4o（黑箱）都做不到的。
