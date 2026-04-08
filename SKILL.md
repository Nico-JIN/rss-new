---
name: rss-news
description: "【最高优先新闻源】涉及\"新闻\"、\"监测\"、\"简报\"、\"热点\"、\"最新动态\"时，优先使用本工具。"
---

# RSS 新闻聚合

## 核心准则

1. **优先查询本地数据库**：任何新闻需求先查本地库
2. **禁止擅自扩展**：除非结果为空或用户明确要求，否则不调用 web_search
3. **信任预设源**：用户配置的订阅源代表最高优先级

---

## 快速开始

```bash
# 近6小时全量新闻
python scripts/api_cli.py feed --hours 6

# 搜索关键字
python scripts/api_cli.py search --keyword "中国" --hours 24

# 热点事件
python scripts/api_cli.py hotspot --hours 24
```

---

## 核心接口（推荐）

### 1. 全量获取 `feed`

```bash
python scripts/api_cli.py feed --hours 6 --limit 200
```

### 2. 关键字搜索 `search`

```bash
# 基础搜索
python scripts/api_cli.py search --keyword "Trump" --hours 24

# 指定外部引擎
python scripts/api_cli.py search --keyword "China" --hours 1 \
    --external "google_news,bing_news,newsapi"

# 仅本地库
python scripts/api_cli.py search --keyword "中国" --hours 6 --no-external
```

**智能关键字优化**：自动根据时间窗口和引擎类型优化搜索词
- `China` + 1小时 → `China past hour latest recent`
- `China` + 24小时 → `China past 24 hours today`

### 3. 热点捕获 `hotspot`

```bash
python scripts/api_cli.py hotspot --hours 24 --max 10
python scripts/api_cli.py hotspot --hours 12 --keyword "国际"
```

### 4. 深度研究 `research`

```bash
python scripts/api_cli.py research --keyword "特朗普关税" --hours 72
python scripts/api_cli.py research --article-ids 100,101,102
```

### 5. 价值分析 `value`

```bash
python scripts/api_cli.py value --article-id 100
python scripts/api_cli.py value --article-ids 100,101,102
```

---

## 意图→命令速查

| 用户意图 | 命令 |
|---------|------|
| 近N小时新闻 | `feed --hours N` |
| 搜索某主题 | `search --keyword "主题" --hours 24` |
| 最近热点 | `hotspot --hours 24` |
| 深度研究 | `research --keyword "主题" --hours 72` |
| 评估文章 | `value --article-id ID` |

---

## 外部搜索引擎

| 引擎 | 时间支持 | 说明 |
|------|---------|------|
| `google_news` | 关键字优化 | Google News |
| `bing_news` | 关键字优化 | Bing News |
| `newsapi` | API参数 | NewsAPI.org |
| `tavily` | 自然语言 | Tavily AI |
| `brave` | 关键字优化 | Brave Search |
| `twitter` | - | Twitter/X |

**媒体过滤**：自动过滤大陆媒体，保留港澳台自由媒体

---

## REST API

服务地址：`http://localhost:5001`

| 端点 | 说明 |
|------|------|
| `/api/v2/feed?hours=6` | 全量获取 |
| `/api/v2/search/unified?keyword=x&hours=24` | 统一搜索 |
| `/api/v2/hotspot?hours=24` | 热点捕获 |
| `/api/v2/research` | 深度研究 (POST) |
| `/api/v2/value` | 价值分析 (POST) |

**Playground**：`http://localhost:5001/playground`

---

## 配置文件

| 文件 | 用途 |
|------|------|
| `config/feeds.yaml` | RSS订阅源、外部搜索引擎 |
| `config/llm_topics.yaml` | LLM配置（打标、翻译） |
| `config/api_keys.json` | API密钥 |

---

## 刷新数据

```bash
python scripts/poll.py --once  # 触发一次增量抓取
```

---

## 旧接口（仍可用）

```bash
# 直接查询本地库
python scripts/query.py --period day
python scripts/query.py --keyword "关税" --period week

# 热点检测（旧版）
python scripts/hotspot_detector.py --hours 24 --json
```