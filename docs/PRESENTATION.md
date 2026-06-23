---
marp: true
theme: default
paginate: true
size: 16:9
backgroundColor: #ffffff
style: |
  section {
    font-size: 22px;
    font-family: 'Microsoft YaHei', 'SimHei', sans-serif;
  }
  section.title {
    text-align: center;
  }
  section.title h1 {
    font-size: 40px;
  }
  section h1 {
    font-size: 32px;
    color: #2c3e50;
  }
  section h2 {
    font-size: 28px;
    color: #34495e;
  }
  table {
    font-size: 18px;
    margin: 0 auto;
  }
  th {
    background-color: #3498db;
    color: white;
  }
  code {
    background-color: #f4f4f4;
    padding: 2px 6px;
    border-radius: 3px;
  }
  pre {
    background-color: #f8f8f8;
    padding: 12px;
    border-radius: 6px;
    font-size: 16px;
  }
---

<!-- _class: title -->

# 项目进展汇报


---

## 课题概览

**课题**：从 OpenLibrary Python 代码仓库中提取概念、梳理关系、构建知识图谱（KG），支撑 downstream issue 定位

**核心 Pipeline**：
```
Python 源码 → AST 标识符提取 → LLM 生成概念定义 →
同义词/一词多义/共现关系 → KG 装配 → Neo4j 图数据库 → Issue 定位
```

**汇报起点**：

| 问题                         | 严重度     |
| ---------------------------- | ---------- |
| LLM 定义产出为 0             | P0 —— 致命 |
| 概念 ≥50% 是 stdlib/框架噪声 | P1 —— 严重 |
| 下游 issue 定位完全空白      | P1 —— 严重 |

---

## 起点诊断

**跑出来的"KG"长什么样：**

| 指标     | 数值   | 实质问题                     |
| -------- | ------ | ---------------------------- |
| 概念数   | 5,369  | ≥50% 是 Python/框架噪声      |
| 关系数   | 3,447  | 全部 co-occurrence，噪声主导 |
| LLM 定义 | **0**  | 模型名错误 + 毒缓存          |
| 同义词   | **0**  | Phase 3 文件不存在           |
| 多义词   | **0**  | Phase 4 文件不存在           |
| 下游定位 | **无** | 完全未实现                   |

**Top 概念**：`get`(1765次) `append` `split` `strip` `startswith` `join` `datetime` `json` `os` `logging`

**Top 中心节点**：`literal` `cast` `re` `os` `logging` `functools` —— 全是框架符号

> 结论：这是"标识符共现图"，不是"openlibrary 概念图"

---

## 迭代一：Phase 1 概念过滤重写

**三类改动：**

| 改动             | 内容                                                |
| ---------------- | --------------------------------------------------- |
| 五类硬黑名单     | 350 词（stdlib + builtins + framework + stopwords） |
| 文件覆盖率过滤   | 出现在 >50% 文件的词直接砍掉                        |
| 跳过 import 类型 | `import os, re, sys` 不入图                         |

**效果：**

|               | 改造前                 | 改造后                                    |
| ------------- | ---------------------- | ----------------------------------------- |
| 总 occurrence | 31,646                 | **21,281**                                |
| 唯一概念      | 5,369                  | **3,760**                                 |
| Top 概念      | `get/append/split/...` | **`page/edition/username/work/user/...`** |

---

## 迭代一：Phase 2 LLM 定义修复

**三个致命 bug：**

| Bug      | 原因                     | 修复                 |
| -------- | ------------------------ | -------------------- |
| 定义全空 | `deepseek-v4-pro` 不存在 | → `deepseek-chat`    |
| 毒缓存   | 失败时把 `""` 写入缓存   | → 空结果不写缓存     |
| 静默失败 | 0 个定义但脚本正常退出   | → 失败率 ≥50% 抛异常 |

**效果**：21,281 个 occurrence **全部**成功生成定义，0 失败。

每个概念出现都有了一句 LLM 生成的语义定义，后续的关系检测才有了语义基础。

---

## 迭代一：下游定位从零到一

**全新 `downstream/` 子包：**

| 模块                    | 功能                                               |
| ----------------------- | -------------------------------------------------- |
| `ground_truth.py`       | GitHub API 抓取 closed issues + 修复 PR 的文件列表 |
| `issue_localization.py` | KG 上的概念匹配 + 文件排序算法                     |
| `locate_issue.py`       | CLI：单条定位 / 批量评估                           |

**IssueLocalizer 算法**：

```
Issue 文本 → tokenize → 匹配 KG 概念 →
同义词扩展 → 共现扩展 → 多义消歧 → 文件/函数加权排名
```

- Token match：精确名 1.0 / 子词 0.6 / 词干 0.3
- 停用词过滤：~100 词英文虚词 + issue 模板噪声
- Light stemming：`logging ↔ login` 能通

**首次评估（naive token match）**：Recall@10 ≈ 0% —— 几乎所有 issue 都命中不了
issue 的自然语言和 KG 里的标识符名称之间存在巨大的语义鸿沟

---

## 迭代二：同义词双轨检测

### Phase 3 设计

```
cosine ≥ 0.85  →  Track A "命名变体"（自动接受）
0.70 ≤ cos < 0.85  →  Track B "领域等价"（LLM 判定 YES/NO）
cosine < 0.70  →  丢弃
```

| 要点         | 说明                                       |
| ------------ | ------------------------------------------ |
| Track A 抓   | `validate_email ↔ valid_email`（字面变体） |
| Track B 抓   | `user ↔ account`（语义等价）               |
| Track B 特例 | `work ↔ book` 不是同义（FRBR 区分）        |
| 缓存         | LLM 判定结果落盘，断点续跑                 |

**效果**：1,382 对同义词（289 命名变体 + **1,093 领域等价**）

---

## 迭代二：一词多义 & 共现改造

### Phase 4：一词多义双闸门

|                 | 旧   | 新                            |
| --------------- | ---- | ----------------------------- |
| min_occurrences | 3    | **5**                         |
| min_files       | 无   | **3**（必须在 ≥3 个文件出现） |
| DBSCAN eps      | 0.30 | 0.35                          |

**效果**：499 个多义概念。`path` 过度分簇到 216 个含义（eps 偏紧），真实多义 ~4-5 种。

### Phase 5：共现子域分桶

1. 丢弃 module 级 context（`import os, import re` 不算共现）
2. 提取 openlibrary 子域（`accounts/core/catalog/plugins/solr/...`）
3. 跨子域降权 ×0.3

**效果**：3,447 → **1,180** 对（-66%），噪声大幅消除

---

## 遇到的关键 Bug

| #   | Bug                      | 耗时 | 修复                                |
| --- | ------------------------ | ---- | ----------------------------------- |
| 1   | 毒缓存：LLM 失败写入空串 | 半天 | 跳过空结果 cache.set                |
| 2   | Track B 候选爆炸 25k 对  | 2h   | 阈值 0.55→0.70                      |
| 3   | Neo4j 0 条边             | 半天 | concept_id = UUID 改 canonical_name |
| 4   | 评估卡死几小时           | 半天 | 多义簇定义预计算                    |
| 5   | 评估用了 2 周前的旧 KG   | 1h   | 跳过逻辑修正                        |

---

## 迭代三：语义导航层

### 旧 KG-walk 的致命弱点

Issue 文本 → tokenize → 精确字符串匹配 → KG 概念
         ↑
    90% 的关键信息在此丢失

Issue 说 "crashes when borrowing limit exceeded"，KG 里没有 `crashes` 这个词 → 入口匹配不到 → 全链断裂

### 新增 4 个模块

| 模块                    | 功能                                                       |
| ----------------------- | ---------------------------------------------------------- |
| `query_rewriter.py`     | embedding 语义检索：issue 文本 → cosine → 概念定义 top-50  |
| `graph_walker.py`       | 多跳 BFS：synonym + co-occurrence 边 3-hop 探索 + 高频剪枝 |
| `skeleton_generator.py` | 代码骨架生成：只输出匹配概念相关的函数代码                 |
| `path_explainer.py`     | 路径解释：为什么推荐这个文件？沿途经过了哪些概念           |

---

## 迭代三：数据流 & 优化

### 新数据流

```
Issue 文本
  → [embedding] 语义匹配 top-50 概念 + 多义簇锁定
  → [BFS] 3-hop 图游走 → 200+ 到达概念
  → [加权] weight × IDF → 文件排名
  → [输出] 文件 + 函数 + 骨架 + 导航路径解释
```

### 性能优化

| 优化                          | 效果                     |
| ----------------------------- | ------------------------ |
| 概念 embedding 落盘缓存       | 重复跑 0 秒加载          |
| 多义簇定义预计算              | 91 issue 从几小时 → 1 秒 |
| SWE-bench Pro 91 条           | ground truth 评估基准    |
| BM25 基线（272 文件全文索引） | 公平对比                 |

---

## 数据流示例：一条 Issue 的完整旅程

**Issue**: *"Borrowing limit incorrectly blocks patron with 0 active loans"*

**Step 1 — Embedding 语义匹配**
```
Issue 向量 → cosine → 3,760 个概念定义，取 top-50：
  borrow  (0.91)    loan     (0.89)    patron    (0.87)
  limit   (0.84)    checkout (0.82)    waitinglist (0.79)
  block   (0.78)    account  (0.76)    ...
```

**Step 2 — 多义簇消歧**
```
block 有 3 个含义簇：
  C0 "preventing user action"     → cos 0.72 ✅ 保留
  C1 "code block / indent"        → cos 0.31 ✗ 丢弃
  C2 "building block / component" → cos 0.18 ✗ 丢弃
→ 只用 C0 的 occurrence 参与后续
```

**Step 3 — 3-hop BFS 图游走**
```
Hop 0: [borrow, loan, patron, limit, checkout, ...]              ← 50 种子
Hop 1: loan → borrow_record (同义)  patron → user, borrower (同义)
Hop 2: patron → waitinglist, hold (共现)  limit → threshold (共现)
Hop 3: hold → availability, book → edition, work, isbn
→ 最终到达 ~220 个概念，各有权重 + 路径
```
---

**Step 4 — 文件加权排名**
```
文件得分 = Σ(concept_weight × IDF)：
  #1 core/loan.py      12.4  (loan,borrow,limit,checkout,patron)
  #2 accounts/model.py  8.7  (patron,user,borrow)
  #3 core/borrow.py     6.2  (borrow,loan)
  #4 plugins/waitinglist.py  4.1  (waitinglist,hold,patron)
```

**Step 5 — 输出**
```
top-10 文件 + 匹配函数 + 代码骨架 + 路径解释
  → "推荐 loan.py #1: issue 'borrowing' → embed → borrow
     → 共现边 → loan (loan.py 出现 47 次)"
```

---

## 最终评估结果

### 91 条 SWE-bench Pro Issue 定位

| 指标               | BM25（基线） | KG-walk v2 | 差距  |
| ------------------ | ------------ | ---------- | ----- |
| **File Recall@10** | **92.3%**    | 82.4%      | -9.9  |
| File MRR           | 0.758        | 0.547      | -0.21 |
| Function Recall@10 | 84.6%        | 73.6%      | -11.0 |
| 第 1 名命中        | 66%          | 41%        | —     |

### KG 独家优势（11 个 issue KG 排名优于 BM25）

| Issue                             | KG     | BM25   |
| --------------------------------- | ------ | ------ |
| "Edition.from_isbn() 不识别 ASIN" | **#1** | #3     |
| "移除 legacy XML 解析 solr 输出"  | **#1** | #4     |
| "给 list seeds 添加 public notes" | **#1** | #3     |
| "POST /lists/add 返回 500 错误"   | **#2** | 未命中 |

共同特征：OpenLibrary 核心领域逻辑（编目/搜索/列表），概念之间有丰富的同义词+共现边

---

## 数据全貌 & 项目规模

### 当前 KG 规模

| 指标       | 改造前             | 改造后                     |
| ---------- | ------------------ | -------------------------- |
| 概念数     | 5,369（≥50% 噪声） | **3,760**（领域概念）      |
| LLM 定义   | 0                  | **21,281**（100% 成功）    |
| 同义词关系 | 0                  | **1,382**                  |
| 多义概念   | 0                  | **499**                    |
| 共现关系   | 3,447              | **1,180**（子域分桶）      |
| Neo4j      | 0 条边             | **2,562 条边**             |
| Issue 定位 | 不存在             | **File Recall@10 = 82.4%** |

### 项目规模

- 4 个新下游模块 + 3 个评估脚本 + 1 个一键 Pipeline
- embedding 缓存 + LLM 缓存 + 各阶段 JSON 产物全部落盘

---

## 待改进 & 下一步

| 问题                             | 状态                                   |
| -------------------------------- | -------------------------------------- |
| Phase 4 eps 偏紧 → `path` 216 簇 | 调到 0.55-0.65 即可                    |
| KG 不如 BM25（差 ~10 个点）      | 方法论限制，非调参能解决               |
| KG 信号密度远低于 BM25           | KG 只有过滤后的标识符，BM25 有全部源码 |
| 孤立节点 54%                     | 放宽共现门槛 + call graph 一跳         |
| 混合方法（BM25+KG）未尝试        | 预计能涨 1-3 个点                      |

### 下一步方向

1. **混合评分**：`α·BM25 + (1-α)·KG`，取双方长处
2. **Issue 语义入口优化**：LLM 翻译 issue 自然语言 → KG 术语集
3. **叙事重心调整**：不强求超过 BM25，强调 KG 的概念质量、关系清晰度、Neo4j 可查询性

---

<!-- _class: title -->

## 总结

**从 0 到 1**：修复了 LLM 零产出的致命 bug，建立了从概念抽取到图数据库到 issue 定位的完整链路。

**从 1 到 80%**：用 embedding 语义导航替代 token match，将 issue 定位从 <1% 提升到 82.4%。

**诚实结论**：KG 在「领域逻辑」类 issue 上有独到价值（11 个 case 超过 BM25），但在 SWE-bench 的主流 issue 类型上，BM25 的直接关键词匹配仍是更强的基线。

**后续工作**：混合方法 + 叙事调整 + Phase 4 eps 调优。
