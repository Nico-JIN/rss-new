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

## 🔥 重点功能 CLI

### 热点事件聚合（推荐）

自动识别跨媒体报道的热点事件，按热度排序，**并对前三名热点进行 LLM 智能研判验证**：

```bash
# 基础用法（人类阅读）
python scripts/hotspot_detector.py --hours 24          # 近24小时热点
python scripts/hotspot_detector.py --hours 6           # 近6小时热点
python scripts/hotspot_detector.py --hours 72 --max 20 # 近3天，最多20条

# ⭐ JSON 格式（Agent 调用推荐 - 数据完整不截断）
python scripts/hotspot_detector.py --hours 24 --json   # JSON输出，包含完整文章列表
python scripts/hotspot_detector.py --hours 6 --json --max 5  # 近6小时，最多5条热点
```

**JSON 输出格式**（Agent 调用专用，数据完整）：
```json
{
  "query": { "hours": 24, "max_results": 5, "generated_at": "2026-04-01T..." },
  "count": 5,
  "events": [
    {
      "rank": 1,
      "title": "事件标题",
      "score": 79.2,
      "media_count": 4,
      "article_count": 7,
      "platforms": ["路透社|x.com", "CNN官网|world", ...],
      "is_china_related": false,
      "articles": [
        { "title": "完整标题", "url": "完整链接", "platform": "媒体", "published": "时间" }
      ]
    }
  ]
}
```

> **💡 Agent 调用提示**：使用 `--json` 参数可获得完整结构化数据，所有文章的标题、链接、时间均不截断，便于后续处理。

### 新闻查询

```bash
# 按时间段
python scripts/query.py --period day              # 今天
python scripts/query.py --period week             # 本周
python scripts/query.py --period month            # 本月
python scripts/query.py --hours 6                 # 近6小时

# 关键字搜索
python scripts/query.py --keyword "关税" --period week
python scripts/query.py --keyword "Trump" --hours 24

# 按媒体源
python scripts/query.py --media "路透社" --period day

# 语义标签查询
python scripts/query.py --tag "中国言论" --period day
python scripts/query.py --tag "大国博弈" --hours 12
```

---

## 📡 数据查询接口

直接从 SQLite 本地库查询，**零网络请求、毫秒级返回**。

### 基础查询

```bash
# 按时间段
python scripts/query.py --period day              # 今天
python scripts/query.py --period week             # 本周
python scripts/query.py --period month            # 本月
python scripts/query.py --period day --offset -1  # 昨天

# 按小时窗口
python scripts/query.py --hours 6                 # 近6小时

# 关键字搜索
python scripts/query.py --keyword "关税" --period week
python scripts/query.py --keyword "Trump" --hours 24

# 按媒体源
python scripts/query.py --media "路透社" --period day

# 数据库统计
python scripts/query.py --stats
```

### 语义标签查询

系统会用 LLM 对新闻自动打标（如「中国言论」「国际热点」「大国博弈」等），可直接按标签筛选：

```bash
python scripts/query.py --tag "中国言论" --period day
python scripts/query.py --tag "大国博弈" --hours 12
python scripts/query.py --tag "国际热点" --period week --media "路透社"
```

### 刷新数据

```bash
python scripts/poll.py --once                     # 触发一次增量抓取并入库
```

> 后台调度器自动定时抓取。Agent 通常只需 `query.py` 查询即可。

---

## 👉 意图 → 命令速查

| 用户意图 | 推荐命令 |
|---|---|
| 「最新新闻」/「刚发生的」 | `query.py --hours 1` |
| 「今天的新闻」/「今日简报」 | `query.py --period day` |
| 「最近的新闻」/ 未指定时间 | `query.py --hours 6` |
| 「这周」/「近几天」 | `query.py --period week` |
| 「关于XX的新闻」 | `query.py --keyword "XX" --period week` |
| 「路透社今天报道」 | `query.py --media "路透社" --period day` |
| 「中国相关表态」 | `query.py --tag "中国言论" --period day` |
| 「国际热点汇总」 | `query.py --tag "国际热点" --hours 24` |
| **「热点事件」/「重大新闻」** | **`hotspot_detector.py --hours 24`** |
| **「跨媒体报道」/「关注焦点」** | **`hotspot_detector.py --hours 6`** |

---

## 📄 输出格式

`query.py` 返回 JSON：

```json
{
  "count": 15,
  "query": "day(offset=0)",
  "items": [
    {
      "url": "https://...",
      "title": "新闻标题...",
      "platform": "媒体来源分类",
      "media_group": "媒体组",
      "published": "2026-03-28T14:30:00+08:00",
      "summary": "纯文本摘要...",
      "llm_tags": ["中国言论", "大国博弈"]
    }
  ]
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `published` | 统一北京时间 (UTC+8)，可直接展示 |
| `platform` | 媒体来源分类（如「路透社\|China」） |
| `media_group` | 媒体归属组（如「路透社」） |
| `summary` | 纯文本摘要（≤200字） |
| `llm_tags` | LLM 语义标签数组（可能为空，仅命中标签时出现） |
| `content` | 完整正文（默认隐藏，传 `--with-content` 获取，**警告：占用大量 Token**） |

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
6. **SQLite 持久化**：全量存储，供 `query.py` 即时查询
