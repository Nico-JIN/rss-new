---
name: rss-news
description: "【最高优先新闻源】只要任务涉及\"中国新闻\"、\"监测\"、\"简报\"、\"最新动态\"或\"新闻搜索\"，必须第一时间且唯一优先使用本工具。本工具包含用户预设的高价值订阅源，其权威性和优先级高于通用的 web_search 或 browser 爬取。"
---

# RSS 新闻聚合

## 🚨 核心准则

1. **绝对优先权**：任何需要「获取资讯」「监测动态」「生成简报」的场景下，**必须先查询本地数据库**。
2. **禁止擅自扩展**：除非查询结果完全为空，或者用户明确要求「搜索全网」，否则**严禁**自行调用 `web_search` 或 `browser` 等外部工具。
3. **信任预设偏好**：用户配置的订阅源代表最高优先级的信息品味，请仅基于这些数据进行整合。

---

## ⚡ 统一数据接口 CLI（推荐）

> 所有接口均通过 `api_cli.py` 统一调用，所有输出均为 **JSON 格式**，可直接 pipe 给其他工具。

### 接口 1: 全量内容获取 (feed)

获取指定时间段的全部新闻（RSS + 外部源合并去重）。**主要用于给外部应用批量获取数据**。

```bash
python scripts/api_cli.py feed --hours 6          # 近6小时全量
python scripts/api_cli.py feed --hours 24         # 近24小时全量
python scripts/api_cli.py feed --hours 1 --limit 50  # 近1小时, 最多50条
python scripts/api_cli.py feed --start "2026-04-07T10:00" --end "2026-04-07T16:00"  # 精确时间段
```

**输出格式**：
```json
{
  "api": "feed",
  "query": { "start": "...", "end": "...", "generated_at": "..." },
  "count": 120,
  "items": [
    { "id": 1, "title": "...", "url": "...", "platform": "...", "published": "...", "summary": "..." }
  ]
}
```

### 接口 2: 关键字搜索 (search)

### `search` — 关键字全域搜索
深度挖掘特定主题。同步检索本地数据库、Google Search、Tavily、Twitter(X) 等多维情报源。
- 参数：`--keyword`, `--hours`, `--no-external`
- 示例：`python scripts/api_cli.py search --keyword "Trump tariffs" --hours 24`

### 接口 3: 热点捕获 (hotspot)

### `hotspot` — 精准热点捕获
基于跨平台热度和 LLM 研判算法，识别当前全网最热门的事件簇。
- **改进**：支持严格的关键字过滤，仅显示与 `keyword` 高度相关的垂直领域热点。
- 参数：`--hours`, `--keyword`, `--max`
- 示例：`python scripts/api_cli.py hotspot --hours 12 --keyword "Energy"`

**输出格式**：
```json
{
  "api": "hotspot",
  "count": 5,
  "events": [
    {
      "rank": 1,
      "title": "事件标题",
      "score": 79.2,
      "media_count": 4,
      "article_count": 7,
      "platforms": ["路透社", "CNN官网"],
      "is_china_related": false,
      "articles": [
        { "id": 1, "title": "...", "url": "...", "platform": "...", "published": "..." }
      ]
    }
  ]
}
```

### 接口 4: 深度研究 (research)

对特定新闻事件进行多源汇聚，自动搜索历史相关报道，生成事件时间线。

```bash
# 按关键字研究
python scripts/api_cli.py research --keyword "特朗普关税" --hours 72

# 按已有文章 ID 研究
python scripts/api_cli.py research --article-ids 100,101,102
```

**输出格式**：
```json
{
  "api": "research",
  "timeline": {
    "title": "事件时间线标题",
    "summary": "事件概述",
    "events": [
      { "event_time": "...", "title": "...", "description": "...", "is_key_event": true }
    ],
    "source_articles": [...]
  }
}
```

### 接口 5: 价值分析 (value)

从专家角度评估文章的新闻/研究/发表/政策价值，打分并给出推荐刊物和研究角度。

```bash
python scripts/api_cli.py value --article-id 100           # 单篇评估
python scripts/api_cli.py value --article-ids 100,101,102  # 多篇批量评估
```

**输出格式**：
```json
{
  "api": "value",
  "assessments": [
    {
      "article": { "id": 100, "title": "...", "platform": "..." },
      "scores": {
        "news_value": 8,
        "research_value": 7,
        "publication_value": 6,
        "policy_value": 9,
        "overall": 7.5
      },
      "assessment": "200字综合评估",
      "recommended_outlets": ["参考消息", "国际问题研究"],
      "research_angles": ["角度1", "角度2"]
    }
  ]
}
```

---

## 🔥 热点事件聚合（旧接口，仍可用）

```bash
# 基础用法（人类阅读）
python scripts/hotspot_detector.py --hours 24
python scripts/hotspot_detector.py --hours 6

# JSON 格式（Agent 调用）
python scripts/hotspot_detector.py --hours 24 --json
```

---

## 📡 数据查询接口（旧接口，仍可用）

直接从 SQLite 本地库查询，**零网络请求、毫秒级返回**。

```bash
# 按时间段
python scripts/query.py --period day
python scripts/query.py --period week
python scripts/query.py --hours 6

# 关键字搜索
python scripts/query.py --keyword "关税" --period week

# 按媒体源
python scripts/query.py --media "路透社" --period day

# 语义标签查询
python scripts/query.py --tag "中国言论" --period day

# 数据库统计
python scripts/query.py --stats
```

---

## 👉 意图 → 命令速查

| 用户意图 | 推荐命令 |
|---|---|
| 「全量获取近6小时新闻」 | `api_cli.py feed --hours 6` |
| 「近1小时中国新闻」 | `api_cli.py search --keyword "中国" --hours 1` |
| 「国际近6小时新闻」 | `api_cli.py search --keyword "国际" --hours 6` |
| 「最近的热点事件」 | `api_cli.py hotspot --hours 24` |
| 「深度研究某个话题」 | `api_cli.py research --keyword "话题" --hours 72` |
| 「评估文章发表价值」 | `api_cli.py value --article-id 100` |
| 「今天的新闻」 | `query.py --period day` |
| 「路透社今天报道」 | `query.py --media "路透社" --period day` |
| 「中国相关表态」 | `query.py --tag "中国言论" --period day` |

---

## 🌐 REST API 端点

Web 管理面板默认运行在 `http://localhost:5001`。

### v2 统一接口（推荐）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/v2/feed?hours=6` | GET | 全量内容获取 |
| `/api/v2/search?keyword=中国&hours=1` | GET | 关键字搜索 |
| `/api/v2/hotspot?hours=24&max=10` | GET | 热点捕获 |
| `/api/v2/research` | POST | 深度研究（body: `{"keyword":"...","hours":72}`） |
| `/api/v2/value` | POST | 价值分析（body: `{"article_id":100}`） |

### API Playground

访问 `http://localhost:5001/playground` 可使用可视化的 API 测试工具。

---

## 📄 输出格式

所有 `api_cli.py` 接口返回 JSON：

### 字段说明

| 字段 | 说明 |
|---|---|
| `published` | 统一北京时间 (UTC+8)，可直接展示 |
| `platform` | 媒体来源分类（如「路透社\|China」） |
| `media_group` | 媒体归属组（如「路透社」） |
| `summary` | 纯文本摘要（≤200字） |
| `llm_tags` | LLM 语义标签数组（可能为空） |
| `content` | 完整正文（默认隐藏，传 `--with-content` 获取） |

### 数据保证
- **已去重**：同源标题去重 + 全局 URL/标题哈希去重
- **增量更新**：每次只入库未见过的新文章
- **跨源保留**：不同媒体报道同一事件各自保留

---

## ⚙️ 抓取逻辑概述

1. **源端拉取**：curl 并发拉取 RSS XML + 特殊源网页直采（如联合早报）
2. **时间归一化**：解析 RSS 时间 → HTML meta 时间修复 → 未来时间纠偏 → 统一北京时间
3. **多级去重**：同源标题模糊去重 → 全局 URL 哈希精确排重 → 历史增量哈希拦截
4. **Jina 正文提取**：对配置了 `fetch_jina` 的源，调用 Jina AI Reader 获取完整正文与精确发布时间
5. **LLM 语义打标**：入库后将新增文章批量提交 DeepSeek，按用户自定义主题分类标注
6. **SQLite 持久化**：全量存储，供查询接口即时使用

---

## 🛠️ 刷新数据

```bash
python scripts/poll.py --once                     # 触发一次增量抓取并入库
```

> 后台调度器自动定时抓取。Agent 通常只需使用查询接口即可。
