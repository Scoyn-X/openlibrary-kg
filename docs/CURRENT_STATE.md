# Openlibrary-KG 现状总结（最终迭代前）

> 写于：迭代五完成后，最后一次迭代前。
> 目的：记录从基线到当前的全部演进，作为最终阶段的出发点。

---

## 0. 演进概览

| 迭代 | 主要工作 | 核心指标变化 |
|------|---------|-------------|
| 基线 | LLM定义0%、≥50%噪声、下游不存在 | File Recall≈0% |
| 迭代一 | 概念过滤重写、LLM定义修复、下游从零到一 | Recall 63.7%（纯token） |
| 迭代二 | 同义词双轨检测、一词多义聚类、共现子域分桶 | — |
| 迭代三 | 语义导航层：embedding检索 + 3-hop BFS | Recall 82.4% |
| 迭代四 | 根因分析 + 消融实验 + 混合评分 | 消融：embedding +18.7pp |
| 迭代五 | 全量覆盖重跑 + eps修复 + 共现调试 + LLM翻译 | **Recall 84.6%** |
| **迭代六** | KG平台化 + 短标题enrichment + 密度排名 | Recall 84.6%, MRR 0.580 |
| **迭代七** | 软索引 + 策略路由 + LLM-native baseline | Recall 84.6%, GPT-4o=85.7%, BM25=92.3% |
| **迭代八** | Agent-native查询接口 + LLM-KG Oracle | KG 升级为 Agent 知识基础设施 |
| **迭代九** | 复合概念保留 — 修复 HARD_BLOCKLIST 过度拆分 | **Recall 84.6%→87.9%（+3.3pp）** |
| **迭代十** | 模块架构卡 + Track B 恢复 + 前沿探索报告 | Recall 87.9%, 混合 MRR 0.760 超 BM25 |

---。

## 1. 当前最终产物

| 指标 | 数值 |
|------|------|
| 索引 Python 文件 | 285 |
| 领域概念 | **5,822**（含复合概念保留） |
| LLM 定义成功率 | **100%**（28,869/28,869） |
| 同义词对 | 2,881（Track A 662 + Track B 2,219） |
| 一词多义检测 | 410 个概念，eps=0.55 |
| 共现边 | 1,258（经子域分桶，min_count=3） |
| 总边数 | 2,778 |
| **File Recall@10** | **87.9%** |
| **混合（KG+BM25）** | **92.3%** |
| BM25 基线 | 92.3% |

---

## 2. 当前 config.yaml 关键值（迭代五后）

```yaml
codebase.root: D:/Secret/Sem4/SE/frontier/Openlibrary/openlibrary
codebase.include_patterns: ["openlibrary/**/*.py", "scripts/**/*.py", "*.py"]
llm.model: deepseek-chat
llm.max_tokens: 300
relationships.synonyms.similarity_threshold: 0.70
relationships.polysemy.embedding_distance_threshold: 0.55  # 从0.35调至0.55
relationships.cooccurrence.min_count: 3
pipeline: Phase 1-8 完整
```

---

## 3. 本次迭代待解决的问题

1. **没有整体意识**：KG 只为 issue 定位一个下游任务服务，缺少通用查询接口。需要把 KG 抽象成平台，支持多个下游任务。
2. **10 条未命中 case**：
   a. 5 条覆盖缺失——已全量覆盖但新概念孤立，需验证同义词/共现是否真正接上了
   b. 3 条标题仅 "### Title"——ground truth 的 body 可能包含关键信息，需从 body 提取
   c. 排名被大文件挤出——当前 SUM 聚合偏向概念多的大文件，需多维信号综合排名
3. **超越 BM25**：两种方法同时未命中的 6 条 case 中，有部分 BFS 已走到 GT 文件但排名不够，有机会抓住
4. **现代工具融合**：BM25 已过时，现代代码理解工具（LLM、LSP）可融入 KG 方法

---

## 4. 当前 KG 已知弱点（迭代五诊断结果）

- **一词多义已修**：eps 0.35→0.55，总簇数 7,533→2,826（-62%），贡献 +1.1pp
- **共现阈值已达最优**：min_count=2 边翻倍引入噪声，已回退
- **55% 概念是孤立节点**，巨型分量仅 26.1%
- **91 条 issue 失败分布**：15条KG未命中 = 5无入口 + 2无覆盖 + 7排名未进 + 1其他
- **6 条 both-miss**：可救 2-3 条（body提取 + 排名优化）
