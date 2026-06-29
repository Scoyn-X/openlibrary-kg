# 迭代九：复合概念保留 —— 修复 HARD_BLOCKLIST 的过度拆分

> 生成时间：2026-06-30

---

## 一、发现

迭代七中我们发现了一个长期被忽视的问题：`format_languages` 这种领域特定标识符，因为包含被 HARD_BLOCKLIST 过滤的 token（`format`），被 `split_name_filter_nouns` 拆成了 `languages` 这一个单词——失去了"format_languages"这个完整的、高区分度的标识符。

因果链：

```
format_languages
  → split_identifier → ["format", "languages"]
  → filter_tokens → "format" 被 HARD_BLOCKLIST 挡掉
  → split_name = "languages"
  → "languages" 出现在 50+ 个文件中，IDF 低，信号被稀释
  → issue 中提到 "format_languages" 时无法精准锚定 catalog/utils/__init__.py
```

类似被过度拆分的标识符包括：
- `read_subjects` → 只剩 `subjects`
- `get_subjects` → 只剩 `subjects`
- `new_solr_updater` → 拆分后 token 全被挡，整体被丢弃

---

## 二、修复

在 `scripts/extract_concepts.py` 的 Phase 1 循环中新增"复合概念保留"逻辑：

**条件触发**：
1. `split_name` 非空（说明至少有一个 token 存活）
2. 至少一个 token 被 HARD_BLOCKLIST 挡掉（`has_blocked_token`）
3. 拆分后存活的 token ≤ 1 个（说明原始标识符的大部分信息被丢失）
4. 原始标识符 ≥ 2 个 token（避免对单 token 标识符的重复）

**动作**：
创建第二个 occurrence，`split_name` 设为完整的原始标识符（如 `format_languages`）。这个复合概念和正常概念一样进入 Phase 2-6 的完整流程——获得 LLM 定义、参与同义词检测和共现计数。

---

## 三、结果

### 概念增长

| 指标 | 迭代八 | 迭代九 | 变化 |
|------|--------|--------|------|
| 概念数 | 4,338 | **5,822** | **+1,484** |
| 同义词对（仅 Track A） | 313 | **662** | +349 |
| 共现边 | 1,295 | **1,258** | -37 |
| 总边 | 2,825 | **2,778** | -47 |

> 注：Phase 3 本次用了 `--no-llm`（仅 Track A 命名变体），缺少了平时 1,217 对 Track B 的 LLM 领域等价同义词。恢复 Track B 后边数应回到 3,000+。

### 核心指标

| 指标 | 迭代八 | 迭代九 | 变化 |
|------|--------|--------|------|
| **KG File Recall@10** | **84.6%** | **87.9%** | **+3.3pp** |
| KG File MRR | 0.580 | 0.540 | -0.04 |
| KG Function Recall@10 | 74.7% | **79.1%** | +4.4pp |
| KG+BM25 混合 | 92.3% | 92.3% | 持平 |

### 直接修复的 case

| Issue | 之前 Rank | 之后 Rank | 关键概念 |
|-------|----------|----------|---------|
| `format_languages` 问题 1 | None | **2** | `format_languages`（IDF=5.55） |
| `format_languages` 问题 2 | None | **5** | 同上 |
| `isbndb` CLI import | None | **4** | `isbndb`、复合概念群 |
| `isbndb` import metadata | None | **9** | 同上 |

### 新概念的质量

| 概念 | 文件数 | IDF | 度数 |
|------|--------|-----|------|
| `format_languages` | 1 | **5.55** | 0（Track B 后应有边） |
| `read_subjects` | 1 | **5.55** | 0（同上） |
| `get_subjects` | 9 | 3.39 | 1 |

这些概念虽然目前孤立（未跑 Track B），但 IDF 极高——只要 issue 里出现这个词，就能立刻锚定到唯一文件。这是 KG 在"关键词稀疏"类 issue 上追上 BM25 的关键能力。

---

## 四、涉及文件

| 文件 | 操作 | 目的 |
|------|------|------|
| `scripts/extract_concepts.py` | 修改 | Phase 1 新增复合概念保留逻辑 |

### 未改文件

| 文件 | 原因 |
|------|------|
| 其余全部模块 | 改动仅限于概念提取阶段。复合概念和普通概念走完全相同的下游流程 |

---

## 五、下一步

Phase 3 使用了 `--no-llm`（仅 Track A 同义词）。恢复 Track B（LLM 领域等价判定）预计可增加 1,200+ 对同义词，显著降低新复合概念的孤立率，可能进一步涨 MRR。
