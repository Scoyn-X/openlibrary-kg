好的，让我为你详细讲解整个工具的使用流程。

  整体架构

  Phase 1            Phase 2            Phase 3-5               Phase 6           Phase 7
  概念抽取     →     LLM生成定义   →   关系发现            →   KG组装       →   Neo4j导入
  (静态AST)         (Deepseek)        (同义词/多义/共现)      (JSON输出)        (图数据库)

  快速开始

  一键运行全流程

  cd D:/Secret/Sem4/SE/frontier/openlibrary-kg

  # 先测试：跳过 LLM，只跑不需要 API 的阶段
  python scripts/run_pipeline.py --skip-llm

  # 完整运行：包含 LLM 定义生成
  python scripts/run_pipeline.py

  # 完整运行 + 自动导入 Neo4j
  python scripts/run_pipeline.py --export-neo4j

  分步运行（推荐初次使用）

  每个 Phase 独立运行，失败了可以单独重跑：

  ---
  Phase 1 — 概念抽取（不需要 API）
  python scripts/extract_concepts.py
  # 输入: Openlibrary 源码（config.yaml 中指定路径）
  # 输出: output/phase_1_concepts.json
  # 内容: 31,646 个概念出现，从标识符拆分出 5,369 个唯一概念

  Phase 2 — LLM 生成定义（需要 Deepseek API）
  # 先跑 50 条测试 prompt 质量
  python scripts/generate_definitions.py --sample 50

  # 确认效果后全量跑
  python scripts/generate_definitions.py
  # 输出: output/phase_2_definitions.json
  # 内容: 每个概念出现 + LLM 生成的"它是什么"定义

  Phase 3 — 同义词检测（需要 sentence-transformers）
  python scripts/detect_synonyms.py
  # 输出: output/phase_3_synonyms.json
  # 原理: 对所有概念做 embedding → 余弦相似度 → 找相似概念对

  Phase 4 — 一词多义分析（依赖 Phase 2 的定义）
  python scripts/analyze_polysemy.py
  # 输出: output/phase_4_polysemy_groups.json
  # 原理: 同一概念的不同定义 → DBSCAN 聚类 → 发现多重含义

  Phase 5 — 共现分析（不需要 API，只需要 Phase 1）
  python scripts/analyze_cooccurrence.py
  # 输出: output/phase_5_cooccurrence.json
  # 原理: 统计同一函数内共同出现的概念对，Jaccard 归一化

  Phase 6 — KG 组装
  python scripts/build_kg.py
  # 输出: output/phase_6_knowledge_graph.json (30MB+)
  #        output/phase_6_knowledge_graph_stats.json
  # 内容: 概念节点 + 关系边 + 统计摘要

  Phase 7 — 导入 Neo4j
  # 先确保 Neo4j 已启动 + 安装 neo4j 驱动
  pip install neo4j

  # 导入
  python scripts/export_to_neo4j.py

  # 或者清空旧数据重新导入
  python scripts/export_to_neo4j.py --clear

  ---
  输出文件说明

  ┌──────────────────────────────┬─────────────────────────────────────────────────────────────────────┐
  │             文件             │                                内容                                 │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ phase_1_concepts.json        │ 所有概念出现：原始标识符、拆分名词、文件位置、代码上下文            │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ phase_2_definitions.json     │ Phase 1 + LLM 定义（"user 是...的人"）                              │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ phase_3_synonyms.json        │ 同义词关系对（如 user ↔ member，相似度 0.89）                       │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ phase_4_polysemy_groups.json │ 一词多义分组（如 work 在 core 中是"著作"，在 scripts 中是"工作流"） │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ phase_5_cooccurrence.json    │ 共现关系对（如 username ↔ password 常出现于同一函数）               │
  ├──────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
  │ phase_6_knowledge_graph.json │ 最终 KG：合并所有节点和关系                                         │
  └──────────────────────────────┴─────────────────────────────────────────────────────────────────────┘

  在 Neo4j 中查询 KG

  导入后可以用 Cypher 查询：

  -- 查看图概要
  MATCH (n:Concept) RETURN count(n) AS nodes;
  MATCH ()-[r]->() RETURN count(r) AS edges;

  -- 查看最核心的概念
  MATCH (c:Concept)
  WHERE c.frequency > 100
  RETURN c.canonical_name, c.frequency
  ORDER BY c.frequency DESC LIMIT 20;

  -- 查看某个概念的共现关系
  MATCH (c:Concept {canonical_name: 'user'})-[r:CO_OCCURRENCE]-(other:Concept)
  RETURN other.canonical_name, r.weight
  ORDER BY r.weight DESC LIMIT 10;

  -- 查看同义词
  MATCH (a:Concept)-[r:SYNONYM]-(b:Concept)
  RETURN a.canonical_name, b.canonical_name, r.weight
  ORDER BY r.weight DESC LIMIT 20;