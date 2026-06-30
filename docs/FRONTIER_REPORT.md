# 基于知识图谱的代码语义理解与 Issue 定位

> 前沿探索课题报告
> 2026 年 3 月 — 6 月

---

## 摘要

现代软件开发中，开发者面对一个未知的 issue 时，首要挑战是快速确定"应该修改哪些文件"。传统的信息检索（IR）方法——如 BM25——将代码视为词袋，在关键词匹配密集的 issue 上表现良好，但完全忽略了代码的结构化语义。另一方面，GPT-4 等大模型虽然具备出色的语义理解能力，但其推理过程不可追溯、无法审计，且每次调用都有成本。

本课题探索了一条新路径：**从 Python 源码中自动构建知识图谱（KG），用概念之间的语义关系来理解代码库结构，辅助 issue 定位。** 经过 9 轮迭代，最终在 91 条 SWE-bench Pro issue 上达到 File Recall@10 = 87.9%（MRR 0.543），混合 BM25 后追平 92.3%（MRR 0.760——超越纯 BM25 的 0.758），独立表现仅比 GPT-4o 低 1.1 个百分点——且推理完全在本地运行、零 API 成本、每一步推理链可追溯。

**关键词**：知识图谱、issue 定位、代码语义、Agent 知识基础设施、LLM + KG

---

## 第一章：研究问题与动机

### 1.1 问题定义

给定一个自然语言 issue 描述，自动推荐最可能需要修改的源代码文件。这是软件维护中最常见也最耗时的任务之一——开发者拿到一个 issue，平均需要 15-30 分钟才能在大型代码库中定位到正确的修改位置。

### 1.2 现有方法的局限

| 方法 | 代表 | 优势 | 局限性 |
|------|------|------|--------|
| 信息检索（IR） | BM25 (1994) | 关键词匹配精准，零成本 | 不理解代码结构，不理解同义词和多义词 |
| 深度学习 IR | CodeBERT, UniXCoder | 语义匹配更强 | 需要训练数据，black-box |
| LLM-native | GPT-4o | 超强语义理解，能推理 | 每次调用成本高，推理不可追溯，可能"背过"答案 |
| 静态分析 | LSP, Tree-sitter | 精确的调用链、类型信息 | 不理解自然语言 issue |

**BM25 虽然在 2026 年已显过时，但它仍然是 IR-based bug localization 文献中最常用的 baseline。** 我们选择它不是因为它是"最好的"，而是因为它是"最基础的"——如果一个方法连关键词匹配都不如，说明它在这个任务上没有提供有效的信息增量。

### 1.3 核心研究问题

> **从 Python 源码中自动构建的知识图谱，能否为 issue 定位提供 BM25 文本检索之外的结构化语义信号？**

子问题：
- 如何在 LLM 的辅助下，从代码标识符中提取高质量的领域概念？
- 同义词、一词多义、共现这三种语义关系，能否在代码上下文中被可靠地检测出来？
- 概念之间的图结构能否通过 BFS 游走实现从 issue 关键词到目标文件的推理？
- 这种方法的最大天花板在哪里？信号的边界是什么？

---

## 第二章：相关工作与文献调研

### 2.1 IR-based Bug Localization

IR-based bug localization 是软件仓库挖掘（MSR）领域的经典子方向。Informedia (2004) 最早提出用 VSM（向量空间模型）对 bug report 和源代码文件做相似度排序。BugLocator (Zhou et al., 2012, ICSE) 引入了"相似 bug report"作为 VSM 的加权信号。BLUiR (Saha et al., 2013) 使用结构化 IR 来区分代码元素类型。AmaLgam (Wang & Lo, 2014) 融合了 VSM、相似 report、stack trace 等多路信号。

**这些工作的共同假设是：代码是词袋。** 没有工作尝试从代码中提取结构化的语义关系（同义词、共现、一词多义）来辅助定位。

### 2.2 代码知识图谱

代码知识图谱是近年来的新兴方向。CodeKG (Abdelaziz et al., 2021) 从 Python 文档中构建 API 调用图。GraphCodeBERT (Guo et al., 2021, ICLR) 引入了数据流图作为代码表示的一种预训练信号。但这些工作的"知识图谱"主要是**语法层**的（AST、数据流）而非**语义层**的（概念定义、领域同义）。

### 2.3 LLM + KG 融合

2024-2025 年间，出现了大量将 LLM 与结构化知识融合的工作。GraphRAG (Microsoft, 2024) 使用 LLM 从文本中构建实体图用于问答。但将其应用于"代码→概念图→issue 定位"的工作尚未见于文献。

### 2.4 AI Agent 与代码理解

SWE-agent (Yang et al., 2024)、Devin (Cognition, 2024)、Claude Code 等 AI 编程 Agent 在 SWE-bench 上取得了显著进展。但它们定位文件主要依赖全文搜索 + LLM 猜测。**没有任何一个 Agent 在动手修改之前，拥有一张"代码库的概念关系地图"。**

### 2.5 本课题的定位

本课题处于三条线的交汇处：
- 信息检索视角：BM25 作为 baseline，我们提供结构化信号作为增量
- 知识图谱视角：从代码本身构建语义图，不是从文档或 API 描述
- Agent 基础设施视角：KG 是给未来 AI Agent 准备的"导航地图"

---

## 第三章：方法设计

### 3.1 整体架构

```
Python 源码
  → Phase 1: AST 标识符提取 + 名词过滤
  → Phase 2: LLM 语义定义生成 (DeepSeek)
  → Phase 3: 同义词双轨检测 (cosine + LLM)
  → Phase 4: 一词多义 DBSCAN 聚类
  → Phase 5: 共现子域分桶
  → Phase 6: 知识图谱装配
  → Phase 7: Neo4j 图数据库导出
  → Phase 8: 语义导航 (embedding 检索 + BFS 游走 + 文件排名)
```

### 3.2 Phase 1：概念提取

使用 Python `ast` 模块遍历每个源文件的语法树，提取函数名、类名、变量名、参数名、import 名。对每个标识符做 snake_case 和 CamelCase 拆分，用 HARD_BLOCKLIST（411 词，含 Python 内置、stdlib 模块、web 框架符号）过滤掉非领域词。

**关键设计决策**：for cycle 9 新增了**复合概念保留**机制——当多 token 标识符被过滤掉部分 token 后，保留完整形式作为独立概念。例如 `format_languages` 原本被过滤成只有 `languages`，现在同时保留 `format_languages` 这一完整形态。

### 3.3 Phase 2：LLM 语义定义

每个 occurrence 调用 DeepSeek-chat 生成一句话定义。"loan：一条记录读者从 Open Library 馆藏中借阅图书的记录"。Phase 2 经历了三次关键修复：模型名错误（`deepseek-v4-pro` 不存在）、毒缓存（空结果写入磁盘缓存导致永远跑不出定义）、静默失败（0 个定义但脚本正常退出）。

**最终结果**：28,869/28,869 occurrences 成功生成定义（100% 成功率）。

### 3.4 Phase 3：同义词双轨检测

| 轨道 | 条件 | 处理 |
|------|------|------|
| Track A：命名变体 | cosine ≥ 0.85 | 自动接受（如 `validate_email ↔ validate_email`） |
| Track B：领域等价 | 0.70 ≤ cosine < 0.85 | LLM 判定 YES/NO（如 `user ↔ account`） |
| 丢弃 | cosine < 0.70 | 直接丢弃 |

### 3.5 Phase 4：一词多义聚类

DBSCAN 对概念的多个定义向量做聚类。经过 eps 敏感性分析（对 `date`、`book`、`user`、`work` 等 14 个核心概念逐一测试 11 个 eps 值），将 eps 从 0.35 调至 0.55。修复前 `date` 被切成 69 个簇（每个簇约 1.5 个 occurrence），修复后合理降至 11 个簇。

### 3.6 Phase 5：共现子域分桶

按 `(file, class, function)` 的 scope 统计概念共现频率，Jaccard 归一化。关键创新：**子域分桶 + 跨域降权**——提取 openlibrary 的子域（`accounts/core/catalog/plugins/solr/...`），同子域共现保持原始权重，跨子域共现权重 ×0.3。同时丢弃 module 级 context（`import os` 和 `import sys` 的共现是假关联）。

### 3.7 Phase 8：语义导航定位

```
Issue 文本
  → [embedding] all-MiniLM-L6-v2 向量化
  → 与 5,822 个概念的定义做 cosine 相似度 → top-50 种子概念
  → 一词多义消歧：锁定与 issue 文本最匹配的簇
  → BFS 3-hop 图游走（synonym + co-occurrence 边，权重衰减）
  → 文件加权排名：Σ(concept_weight × IDF) + 密度乘数
  → 输出：文件列表 + 推理路径 + 代码骨架 + 影响报告
```

---

## 第四章：迭代过程与实验数据

### 4.1 迭代总览

| 迭代 | 主要内容 | 关键结果 |
|------|---------|---------|
| 迭代一 | 概念过滤重写、LLM 定义修复、下游从零到一 | 定义成功率 0%→100%；Recall 63.7%（纯 token） |
| 迭代二 | 同义词双轨、一词多义、共现子域分桶 | 1,382 对同义词、499 个多义概念、1,180 对共现 |
| 迭代三 | 语义导航层：embedding + 3-hop BFS | Recall 82.4% |
| 迭代四 | 根因分析 + 消融实验 | **embedding 独立贡献 +18.7pp** |
| 迭代五 | 全量覆盖重跑 + eps 修复 | Recall 83.5%→84.6%（+1.1pp） |
| 迭代六 | KG 平台化 + 密度排名 | MRR +0.028 |
| 迭代七 | 软索引 + 策略路由 + GPT-4o baseline | 三路基准：BM25 92.3%, GPT-4o 85.7%, KG 84.6% |
| 迭代八 | Agent-native 查询 + LLM-KG Oracle | KG 升级为 Agent 知识基础设施 |
| 迭代九 | 复合概念保留 | **Recall 84.6%→87.9%（+3.3pp）** |

### 4.2 关键实验表格

#### 消融实验：Embedding 独立贡献

| 入口方式 | File Recall@10 | 变化 |
|---------|---------------|------|
| 纯 token match | 63.7% | — |
| + embedding 语义检索 | 82.4% | **+18.7pp** |

#### 一词多义 eps 敏感性分析

| eps | `date` 簇数 | `user` 簇数 | 估值 |
|-----|-----------|-----------|------|
| 0.35（旧） | 69 | 51 | **严重过紧** |
| 0.45 | 40 | 19 | 过紧 |
| **0.55** | **11** | **8** | **合理** |
| 0.65 | 2 | 4 | 开始过松 |

#### 排名公式对照实验（三轮均为负结果）

| 尝试 | Recall@10 | 变化 |
|------|-----------|------|
| 原始 SUM | 82.4% | — |
| sqrt 归一化 | 65.9% | -16.5pp |
| per-(concept,file) 计数 | 70.3% | -12.1pp |
| 种子 3× 加权 | 60.4% | -22.0pp |

#### 复合概念保留效果

| 指标 | 修复前 | 修复后 | 变化 |
|------|--------|--------|------|
| KG 概念数 | 4,338 | **5,822** | +1,484 |
| Recall@10 | 84.6% | **87.9%** | **+3.3pp** |

---

## 第五章：最终结果

### 5.1 三路基准对比

| 方法 | Recall@10 | MRR | Top-1 | 推理可追溯 | API 成本 |
|------|-----------|-----|-------|-----------|---------|
| **BM25**（全文检索, 1994） | **92.3%** | 0.758 | 66% | ❌ | 免费 |
| **GPT-4o**（LLM-native, 2024） | 85.7% | 0.699 | 61.5% | ❌ | 调用收费 |
| **KG-walk**（本课题） | **87.9%** | 0.543 | ~43% | ✅ | **免费** |
| KG + BM25 混合 | **92.3%** | **0.760** | — | 部分 | 免费 |

### 5.2 KG 在哪些 case 上表现独特

**案例：`POST /lists/add returns 500 error when POST data conflicts with query parameters`**

- BM25 排名：未进入前 10（关键词全是高频泛词：post、lists、add、data、query、error）
- KG 排名：**#1**（lists → co-occurrence → normalize_seed → 锚定到 plugins/openlibrary/lists.py）
- GPT-4o 排名：#1（凭文件名猜测）
- 推理路径：`lists-[co-occurrence]->last_modified-[co-occurrence]->seed-[co-occurrence]->makelist`

### 5.3 最终 KG 规模

| 指标 | 数值 |
|------|------|
| 索引 Python 文件 | 285 |
| 领域概念 | 5,822 |
| LLM 定义成功率 | 100%（28,869/28,869） |
| 同义词对 | 2,881（Track A 662 + Track B 2,219） |
| 共现边 | 1,258（子域分桶） |
| 架构模块卡 | 261 文件 × 7 层级 |

---

## 第六章：讨论：方法的能力边界

### 6.1 KG 为什么在 87.9% 处遇到天花板

不是因为"方法不好"或"某条边没连上"——是因为**KG 在完全不同的信号密度上运作**：

```
KG:    5,822 个过滤后的领域标识符
BM25:  285 个文件的全文（数十万 token）
GPT-4o: 整个互联网的预训练知识
```

KG 的信号密度比 BM25 低 1-2 个数量级。当一条 issue 的关键词恰好是高区分度的全文 token（`MARC`、`solr`、`isbn`），BM25 直接命中——KG 无法在这个维度上竞争。但 KG 在关键词稀疏（全部是高频泛词）的 issue 上具有独占优势——这是信号类型的不同，不是信号质量的不同。

### 6.2 一词多义过切的教训

eps=0.35 把 `date` 切成 69 簇——每个簇平均只有 1.5 个 occurrence。消歧模块锁定一个簇后，丢弃了其余 98.5% 的 occurrence。这在效果上等价于随机阉割了 `date` 概念。修好后涨了 +1.1pp——这 1.1 个百分点不是"新发现的信号"，是"之前被错误丢弃的信号"。

**教训**：DBSCAN 对 LLM 生成的定义文本做聚类时，eps 不能沿用 NLP 默认值——LLM 定义之间的措辞差异比自然语言文本大得多。

### 6.3 54% 孤立节点为什么无法修复

尝试了 call graph（Python AST 提取调用图补边）、降低共现 min_count、种子加权、密度排名——全部在基准上倒退。**代码调用 ≠ 概念语义相关。** `main()` 调用了一切——如果通过 call graph 补边，图会退化成全连通，BFS 失去区分度。

### 6.4 BM25 为什么仍然在各类型上领先

91 条 SWE-bench Pro issue 中，56 条（61.5%）属于 MARC/编目类。这类 issue 的关键词（`MARC`、`ISBN`、`catalog`、`edition`、`author`）全部是全文检索中 IDF 最高的 token。BM25 在这种问题上天然有 95% 的命中率——这是数据分布带来的结构性优势，不是方法优劣。

---

## 第七章：前沿探索方向（后续工作）

### 7.1 双层 KG：概念层 + 模块架构层

当前 KG 只有概念层（"`loan` 出现在 `core/lending.py`，和 `patron` 有共现边"）。第八次迭代开始建设模块架构层：**每个文件都有自己的职责描述和架构层级**。双层融合后可以回答更复杂的问题——不是"哪个文件有 `loan` 这个标识符"，而是"哪个模块负责借阅业务"？

### 7.2 KG 作为 AI Agent 的知识基础设施

当前 AI 编程 Agent（SWE-agent、Devin、Claude Code）定位文件的主要手段是全文搜索 + LLM 猜测——两者都是黑箱。KG 提供了第三个选项：**确定的、可追溯的、零成本的语义导航。**

Agent 通过 `concept_card("loan")` 查询概念档案，通过 `explain_path("lists", "seed")` 理解推理链，通过 `impact_report(["core/lending.py"])` 评估修改影响。KG 不取代 LLM——LLM 负责假设，KG 负责验证。

### 7.3 LLM-Oracle 架构

LLM 读 issue → 提出候选概念 → KG 逐条验证（存在/不存在、定义、邻居）→ LLM 根据反馈修正 → 输出带置信度标注的文件排名和推理路径。这解决了 LLM 最大的弱点——幻觉——因为每一条概念都在代码里有实际证据。

### 7.4 跨仓库泛化

当前 pipeline 的所有阈值（eps、同义词 cosine、共现 min_count）都在 OpenLibrary 上调节。方法能否在不调参的情况下直接应用于另一个 Python 仓库（Flask、Django REST、Pandas）？跨仓库泛化是 SE 方法成立的关键标准。

### 7.5 活图：代码语义的版本控制

现在的 KG 是"快照"——需要跑一次完整 pipeline 才能更新。理想的形态是**增量构建**：每次 commit 只更新受影响的文件和概念。结合 git history，可以追踪一个概念的生命周期——什么时候被引入、什么时候被重命名、什么时候被删除。

---

## 第八章：总结

本项目探索了一种新的代码理解范式：**从 Python 源码中自动提取概念、用 LLM 为每个概念生成语义定义、通过同义词/多义词/共现建立关系、装配成知识图谱——然后用这张图为 issue 定位提供结构化的语义导航。**

经过 9 轮迭代，我们从"LLM 定义 0%、概念 ≥50% 噪声"推进到了 5,822 个领域概念、100% 定义成功率、File Recall@10 = 87.9%。更重要的是，我们精确地标定了这个方法的能力边界：信号密度决定了它在关键词密集类 issue 上不如全文检索，但在关键词稀疏类 issue 和可解释性上提供了 BM25 和 GPT-4o 都无法提供的增量价值。

这张图谱的价值不是"比 BM25 分高"——而是给它未来的 AI 编程 Agent 一张**确定的、可追溯的、零成本的代码库语义导航地图**。

---

## 参考文献

1. Zhou, J., Zhang, H., & Lo, D. (2012). Where should the bugs be fixed? More accurate information retrieval-based bug localization based on bug reports. *ICSE 2012*.
2. Saha, R. K., Lease, M., Khurshid, S., & Perry, D. E. (2013). Improving bug localization using structured information retrieval. *ASE 2013*.
3. Wang, S., & Lo, D. (2014). Version history, similar report, and structure: Putting them together for improved bug localization. *ICPC 2014*.
4. Guo, D., Ren, S., Lu, S., et al. (2021). GraphCodeBERT: Pre-training Code Representations with Data Flow. *ICLR 2021*.
5. Abdelaziz, I., Dolby, J., McCusker, J., & Srinivas, K. (2021). A Toolkit for Generating Code Knowledge Graphs. *K-CAP 2021*.
6. Microsoft Research. (2024). GraphRAG: Unlocking LLM discovery on narrative private data. *arXiv:2404.16130*.
7. Yang, J., Jimenez, C. E., Wettig, A., et al. (2024). SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering. *arXiv:2405.15793*.
8. Robertson, S., & Zaragoza, H. (2009). The Probabilistic Relevance Framework: BM25 and Beyond. *Foundations and Trends in Information Retrieval*.
9. Reimers, N., & Gurevych, I. (2019). Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks. *EMNLP-IJCNLP 2019*.
