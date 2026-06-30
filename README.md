# openlibrary-kg

从 Python 源代码自动构建知识图谱，实现 issue 语义导航与文件定位。

> 前沿探索课题报告：[docs/FRONTIER_REPORT.md](docs/FRONTIER_REPORT.md)

## 概述

本课题从 [OpenLibrary](https://github.com/internetarchive/openlibrary) Python 代码仓库中提取领域概念、梳理语义关系，构建知识图谱（KG），并在此基础上实现 issue 定位——给定一条 bug report，自动推荐最可能需要修改的源代码文件。

### 核心 Pipeline

```
AST 标识符提取 → LLM 语义定义 → 同义词双轨检测 → 一词多义聚类 → 
共现子域分桶 → KG 装配 → Neo4j 图数据库 → 语义导航定位
```

### 最终结果

| 方法 | File Recall@10 | File MRR |
|------|---------------|----------|
| KG-walk | **87.9%** | 0.543 |
| BM25（基线） | 92.3% | 0.758 |
| GPT-4o | 85.7% | 0.699 |
| KG + BM25 混合 | **92.3%** | **0.760**（超越纯BM25） |

## 快速开始

```bash
# 1. 安装
pip install -e .

# 2. 配置
cp config.example.yaml config.yaml
# 编辑 config.yaml，填写 Deepseek API Key 和 Neo4j 密码

# 3. 一键运行完整 pipeline
python scripts/run_pipeline.py --export-neo4j --navigate

# 4. 单条 issue 定位
python scripts/locate_issue.py --title "用户描述的 issue 标题"

# 5. 与 BM25 对比评估
python scripts/compare_methods.py
```

## 依赖

- Python 3.12+
- Deepseek API（Phase 2 定义生成 + Phase 3 同义词判定）
- Neo4j（Phase 7 图数据库导入，可选）
- sentence-transformers（Phase 8 语义导航）

## 项目结构

```
openlibrary_kg/
├── extraction/      # Phase 1: AST 解析、标识符提取、名词过滤
├── llm/             # Phase 2: LLM 定义生成
├── embeddings/      # 嵌入向量计算
├── relationships/   # Phase 3/4/5: 同义词、多义词、共现
├── graph/           # Phase 6/7: KG 装配、Neo4j 导出
├── downstream/      # Phase 8: 语义导航、issue 定位、评估
└── utils/           # IO、缓存、日志

scripts/             # 各阶段独立脚本 + 管道编排
output/              # 生成产物（KG、评估结果）
docs/                # 迭代记录与变更文档
```

## 迭代记录

- [docs/CHANGES.md](docs/CHANGES.md) — 迭代一至三的完整改造记录
- [docs/PHASE8_CHANGES.md](docs/PHASE8_CHANGES.md) — 迭代三：Phase 8 语义导航层实现
- [docs/ITERATION_4_5.md](docs/ITERATION_4_5.md) — 迭代四+五：根因诊断与对照实验
- [docs/ITERATION_6.md](docs/ITERATION_6.md) — 迭代六：KG 平台化 + 密度排名 + 短标题enrichment
- [docs/ITERATION_7.md](docs/ITERATION_7.md) — 迭代七：软索引 + 策略路由 + GPT-4o baseline
- [docs/ITERATION_8.md](docs/ITERATION_8.md) — 迭代八：Agent-native查询 + LLM-KG Oracle
- [docs/ITERATION_9.md](docs/ITERATION_9.md) — 迭代九：复合概念保留 — Recall 84.6%→87.9%
- [docs/ITERATION_10.md](docs/ITERATION_10.md) — 迭代十：模块架构卡 + Track B 恢复 — 混合 MRR 0.760 超 BM25
- [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) — 当前状态
- [docs/PROJECT_ANALYSIS.md](docs/PROJECT_ANALYSIS.md) — 项目分析

## 数据规模

| 指标 | 数值 |
|------|------|
| 索引 Python 文件 | 285 |
| 领域概念 | 4,338 |
| 同义词对 | 1,530 |
| 共现边 | 1,295 |
| 总边数 | 2,825 |
| 评估基准 | SWE-bench Pro 91 条 issue |
