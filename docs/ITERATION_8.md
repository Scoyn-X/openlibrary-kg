# 迭代八：KG 作为 AI Agent 的知识基础设施

> 生成时间：2026-06-29

---

## 一、为什么需要这次迭代

迭代七结束时，我们有了一个完整的 issue 定位系统（KG 84.6%、GPT-4o 85.7%、BM25 92.3%）和完整的实验记录。但项目的本质问题还没解决：

**KG 不应该只是一个"issue 定位工具"。** 它是一个从代码库中提取的结构化语义模型。真正的价值不在于和 BM25 比谁的 Recall 高，而在于为 AI 编程 Agent 提供一张**确定的、可追溯的、零成本的代码库语义导航地图**。

本次迭代将 KG 从"搜索工具"升级为"Agent 知识基础设施"。

---

## 二、线1：Agent-native 查询接口（`kg_query.py` 扩展）

### 设计目标

一个 AI Agent（SWE-agent、Claude Code、Devin）在操作代码库时，需要的不是"文件排名"，而是：

- "loan 这个概念是什么？在哪些文件里？和谁有关？"
- "如果我要改 core/lending.py，会影响哪些文件？"
- "agent 从 issue 关键词走到目标文件，中间经过了哪些概念和边？"

### 新增方法

| 方法 | 输入 | 输出 | Agent 使用场景 |
|------|------|------|---------------|
| `concept_card(name)` | 概念名 | 结构化卡片（定义、频率、文件、邻居、IDF、多义簇数） | "让我查一下这个概念的 KG 档案" |
| `explain_path(seed, target, max_hops)` | 两个概念名 | 完整推理路径（步数、边类型、权重、每步定义预览） | "为什么系统推荐了这个文件？走给我看" |
| `reverse_impact(file_path)` | 文件路径 | 直接影响文件（共享概念）+ 间接影响文件（邻居可达） | "改这个文件前，让我先看看会影响什么" |
| `impact_report(file_paths)` | 多个文件 | 合并影响报告 + 去重 + Agent 可读的建议文本 | "我要改这些文件，请给我完整的影响评估" |

### 验证

以 `/lists/add` issue 为例：

```
concept_card("lists")
  → 定义: "Lists are curated collections of books..."
  → 10个文件，10个邻居概念
  → IDF 3.28

explain_path("lists", "seed")
  → lists-[co-occurrence]->last_modified-[co-occurrence]->seed
  → 2 hops, fully traceable

reverse_impact("core/lending.py")
  → 189个本地概念, 15个直接受影响文件, 15个间接受影响文件

impact_report(["core/lending.py", "plugins/upstream/borrow.py"])
  → 合并39个受影响文件, 涉及878个概念
```

### 涉及文件

| 文件 | 操作 | 目的 |
|------|------|------|
| `openlibrary_kg/kg_query.py` | 修改 | 新增 concept_card / explain_path / reverse_impact / impact_report |
| `scripts/demo_kg_agent.py` | **新增** | 演示脚本（agent-query / llm-oracle / full-pipeline 三种模式） |

---

## 三、线2：LLM + KG Oracle（`llm_oracle.py`）

### 架构

```
LLM                    KG (Oracle)
 │                        │
 │ "issue 提到了 lists,    │
 │  add, seed, error..."   │
 │──────────────────────→ │
 │                        │ 查 lists: ✅, card返回
 │                        │ 查 add: ❌ (在HARD_BLOCKLIST)
 │                        │ 查 seed: ✅, card返回
 │                        │ 建议: user_lists, normalize_seed
 │ ←────────────────────── │
 │                        │
 │ "KG确认了 lists + seed, │
 │  这两个概念有边;        │
 │  最终推荐文件为..."      │
 │──────────────────────→ │
 │                        │ BFS从lists→seed→到达文件
 │                        │ explain_path返回推理链
 │ ←─ files + paths + ─── │
 │    impact report        │
```

LLM 可以猜、可以假设。**KG 来判断哪些假设在代码里有依据。**

### 核心组件

| 组件 | 作用 |
|------|------|
| `LLMOracleClient` | 通用 LLM 客户端（OpenAI 兼容 API） |
| `LLMOracle` | LLM-KG 对话管理：发送 issue → 接收 LLM 术语 → KG 验证 → 返回验证结果 → LLM 修正 → 最终输出 |
| `OracleRound` | 单轮对话记录：LLM 提议 / KG 确认 / KG 拒绝 / KG 建议 |
| `OracleResult` | 最终输出：确认的概念、带推理链的文件排名、影响报告、置信度 |

### 验证逻辑

| 步骤 | 做什么 |
|------|--------|
| Round 1 | LLM 读取 issue → 输出 5-10 个搜索术语 → KG 逐条验证 → KG 拒绝不在的概念、返回存在的概念的完整档案 + 邻居推荐 |
| 文件排名 | 确认的概念 + BFS 邻居 → 加权排名 → 每个文件附带 explain_path 推理链 |
| 影响报告 | 对 top-3 文件跑 `impact_report` → Agent 可读的修改前影响评估 |
| 置信度 | 按 LLM 提出的术语中被 KG 确认的比例计算 high/medium/low |

### 涉及文件

| 文件 | 操作 | 目的 |
|------|------|------|
| `openlibrary_kg/downstream/llm_oracle.py` | **新增** | LLM + KG Oracle 对话框架 |

---

## 四、改动文件清单

| 文件 | 操作 | 目的 |
|------|------|------|
| `openlibrary_kg/kg_query.py` | 修改 | 新增 4 个 Agent-native 查询方法 |
| `openlibrary_kg/downstream/llm_oracle.py` | **新增** | LLM + KG Oracle 框架 |
| `scripts/demo_kg_agent.py` | **新增** | 三合一演示脚本 |

### 未改文件

| 文件 | 原因 |
|------|------|
| 其余全部模块 | 新增功能完全独立，不修改现有管道 |

---

## 五、这个项目的终局定位

回看 87 天前：

```
概念抽取 ≥50% 噪声、LLM 定义 0%、下游定位不存在、同义词/多义词/共现全为空
```

现在：

```
4,338 个领域概念的语义图谱
完整的 8 步 pipeline（一键可复现）
issue 定位：KG 84.6%、GPT-4o 85.7%（仅差 1.1pp）
LLM + KG Oracle：LLM 猜测 + KG 验证的对话架构
Agent-native 查询：concept_card / explain_path / reverse_impact / impact_report
BM25 / KG / GPT-4o 三路基准全量对比
```

**我们做的不只是一个"定位工具"。** 我们验证了一种新的代码理解范式：**从代码本身的结构、标识符、调用关系中自动构建语义图谱，让这张图成为 AI Agent 理解代码库的"导航地图"。**

这张图的特点是：
- **确定的**——每条边从代码里提取，不是 LLM 猜的
- **可追溯的**——每步推理能走给你看
- **零成本的**——定位一条 issue 不花 API 钱
- **可审计的**——同义词有没有错、多义词有没有误判，全可检查

当未来的 AI 编程 Agent 需要的不只是"grep 找文件"时，它们需要的正是这种**结构化的、可查询的、语义层面的代码知识**。
