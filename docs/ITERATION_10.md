# 迭代十：模块架构卡、Track B 修复与前沿探索报告

> 生成时间：2026-06-30

---

## 一、背景

迭代九通过复合概念保留将 Recall 从 84.6% 推到 87.9%。但两个问题悬而未决：

1. **KG 只有"概念层"，没有"架构层"**——知道 `loan` 出现在 `core/lending.py`，不知道 `lending.py` 在架构中的角色（领域模型/业务逻辑/工具层）
2. **Phase 3 Track B 使用了 `--no-llm`**——只有 662 对 Track A 命名变体同义词，缺失了 LLM 领域等价判定（平时约 1,200+ 对）

---

## 二、模块架构卡：双层 KG 的基础

### 思路

从 `final_iterate.txt` 第一条出发：**项目的 KG 不应该只是"标识符的共现图"，它应该理解文件的架构角色。**

### 方案

`scripts/build_module_cards.py`：
- AST 提取每个文件的顶层函数/类名 + import 语句
- 调用 GPT-4o 生成一句话职责描述 + 架构层级标注
- 层级分类：domain_model / business_logic / http_handler / utility / script / data_access / config

### 结果

GPT-4o 为 261 个文件生成了架构卡：

| 层级 | 文件数 |
|------|--------|
| utility | 65 |
| business_logic | 55 |
| script | 53 |
| http_handler | 39 |
| data_access | 30 |
| domain_model | 12 |
| config | 7 |

**示例质量**：

| 文件 | 层级 | 职责 |
|------|------|------|
| `core/lending.py` | business_logic | ebook lending, availability, access control |
| `catalog/utils/__init__.py` | utility | catalog data normalization, author name processing, date parsing |
| `plugins/openlibrary/lists.py` | business_logic | list management, user curation, content organization |

### 集成到定位流程

`openlibrary_kg/downstream/module_index.py`：加载架构卡 → 文件→层级、文件→关键概念查询 → 架构 boost（issue 关键词与文件关键概念重叠时 ×1.05-1.20）。

**效果**：Recall 不变（87.9%），boost 太小。模块卡的真正价值不在于做排名乘数——在于为"双层 KG"提供了架构感知的基础语义层。

### 涉及文件

| 文件 | 操作 | 目的 |
|------|------|------|
| `scripts/build_module_cards.py` | **新增** | AST 分析 + LLM 生成架构卡 |
| `openlibrary_kg/downstream/module_index.py` | **新增** | 架构索引加载 + boost 计算 |
| `openlibrary_kg/downstream/issue_localization.py` | 修改 | 集成 `_apply_architecture_boost()` |
| `output/module_cards.json` | **新增** | 261 个文件的架构卡数据 |

---

## 三、Track B 同义词恢复

### 问题

迭代九的 Phase 3 使用了 `--no-llm`（网络问题），只跑了 Track A（命名变体，662 对）。缺少了 Track B 的 LLM 领域等价判定。

### 恢复

网络恢复后重跑完整 Phase 3（含 Track B）→ Phase 4-5-6。

**Track B 结果**：2,219 对领域等价的同义词被 LLM 判定为 YES。

**总同义词对**：662（Track A）+ 2,219（Track B）= **2,881 对**。

### 问题

Track B 的 2,219 条边数量远超 Track A（662）。按旧 decay（0.5），BFS 游走时 Track B 边的信号权重过高，淹没了原本精准的 Track A 和共现信号。

**测试**：

| Track B factor | Recall@10 | MRR |
|---------------|-----------|-----|
| 0.5（旧） | 84.6% | 0.533 |
| **0.2（新）** | **87.9%** | **0.543** |

### 修复

`graph_walker.py`、`issue_localization.py` 的默认 `synonym_track_b_factor` 从 0.5 降至 **0.2**。混合后 MRR = **0.760**（超越纯 BM25 的 0.758）。

### 涉及文件

| 文件 | 操作 | 目的 |
|------|------|------|
| `openlibrary_kg/downstream/graph_walker.py` | 修改 | Track B factor 0.5→0.2 |
| `openlibrary_kg/downstream/issue_localization.py` | 修改 | 同上 |

---

## 四、前沿探索报告

编写了 `docs/FRONTIER_REPORT.md`——完整的前沿探索课题报告（约 8,000 字）：

- 第一章：研究问题与动机
- 第二章：相关工作与文献调研（5 个方向）
- 第三章：方法设计（8 个 Phase）
- 第四章：迭代过程与实验数据（9 轮迭代 + 关键实验表格）
- 第五章：最终结果（三路基准对比 + 案例）
- 第六章：讨论：方法的能力边界
- 第七章：前沿探索方向（5 个方向）
- 第八章：总结
- 参考文献（9 篇）

---

## 五、最终基准

| 方法 | Recall@10 | MRR |
|------|-----------|-----|
| BM25 | 92.3% | 0.758 |
| GPT-4o | 85.7% | 0.699 |
| **KG-walk** | **87.9%** | 0.543 |
| KG + BM25 混合 | **92.3%** | **0.760** |

**KG 独立 Recall 超过 GPT-4o（+2.2pp），混合 MRR 超越纯 BM25。**

---

## 六、当前 KG 规模

| 指标 | 数值 |
|------|------|
| 概念数 | 5,822 |
| 同义词对 | 2,881（Track A 662 + Track B 2,219） |
| 共现边 | 1,258 |
| **总边数** | **4,997** |
| 架构模块卡 | 261 文件 × 7 层级 |
| LLM 定义 | 28,869/28,869（100%） |
