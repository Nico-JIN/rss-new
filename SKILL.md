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

获取指定时间段的全部新闻（RSS + 外部源合并去重）。

```bash
# 完整格式
python scripts/api_cli.py feed --hours 6 --limit 200

# Agent 精简模式（推荐）
python scripts/api_cli.py feed --hours 2 --simple
python scripts/api_cli.py feed --hours 6 --limit 50 --simple
```

**精简输出格式**（`--simple`）：
```json
{
  "count": 53,
  "articles": [
    {
      "title": "伊朗伊斯兰堡会谈首席谈判代表...",
      "url": "https://x.com/AP/status/...",
      "platform": "美联社|X.COM",
      "published": "2026-04-12T17:26:05+08:00"
    }
  ]
}
```

**字段说明**：
| 字段 | 说明 |
|------|------|
| `count` | 文章总数 |
| `articles[].title` | 文章标题 |
| `articles[].url` | 原文链接 |
| `articles[].platform` | 平台/媒体名称 |
| `articles[].published` | 发布时间（ISO格式） |

**Agent 使用示例**：
```bash
# 获取近2小时增量新闻
python scripts/api_cli.py feed --hours 2 --simple

# 获取近6小时新闻（限制50条）
python scripts/api_cli.py feed --hours 6 --limit 50 --simple
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

### 4. 定时热点检测 `scheduled-hotspot`（Agent推荐）

按类别和时间窗口获取热点，**返回纯净JSON格式**。

```bash
# 按类别获取热点（24小时）
python scripts/api_cli.py scheduled-hotspot --category us_news --hours 24
python scripts/api_cli.py scheduled-hotspot --category china_related --hours 24
python scripts/api_cli.py scheduled-hotspot --category japan_news --hours 24
python scripts/api_cli.py scheduled-hotspot --category middle_east --hours 24
python scripts/api_cli.py scheduled-hotspot --category hk_tw_macau --hours 24
python scripts/api_cli.py scheduled-hotspot --category asia_neighbors --hours 24

# 自定义时间窗口
python scripts/api_cli.py scheduled-hotspot --category us_news --hours 6
python scripts/api_cli.py scheduled-hotspot --category china_related --hours 12

# 获取所有类别热点
python scripts/api_cli.py scheduled-hotspot --all --hours 48

# 查看历史执行记录
python scripts/api_cli.py scheduled-hotspot --history --days 7
```

**类别ID对照表**：
| ID | 名称 | 说明 |
|----|------|------|
| `international` | 国际热点 | 重大国际事件、多国参与的热点 |
| `foreign_china` | 外媒报道中国 | 排除中国媒体，仅看外媒视角 |
| `us_news` | 美国新闻 | 美国内政、外交、军事、经济 |
| `japan_news` | 日本新闻 | 日本政治、经济、军事、外交 |
| `middle_east` | 中东新闻 | 中东冲突、石油、外交 |
| `greater_china` | 港澳台新闻 | 港台政治、两岸关系 |
| `asia_other` | 亚洲周边国家 | 韩国、朝鲜、东南亚、南亚、中亚 |

> **多归属机制**：同一热点可能同时出现在多个分类。例如"美伊谈判"会同时出现在 `international`、`us_news`、`middle_east`。

### 5. 深度研究 `research`

```bash
python scripts/api_cli.py research --keyword "特朗普关税" --hours 72
python scripts/api_cli.py research --article-ids 100,101,102
```

### 6. 价值分析 `value`

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
| 按类别获取热点 | `scheduled-hotspot --category us_news --hours 24` |
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

---

## 定时热点检测（7大分类）

自动检测7类新闻热点，支持定时执行和手动触发。**所有接口返回 JSON 格式**。

### 分类列表

| ID | 名称 | 说明 |
|----|------|------|
| `international` | 国际热点 | 重大国际事件、多国参与的热点 |
| `foreign_china` | 外媒报道中国 | 排除中国媒体，仅看外媒视角 |
| `us_news` | 美国新闻 | 美国内政、外交、军事、经济 |
| `japan_news` | 日本新闻 | 日本政治、经济、军事、外交 |
| `middle_east` | 中东新闻 | 中东冲突、石油、外交 |
| `greater_china` | 港澳台新闻 | 港台政治、两岸关系 |
| `asia_other` | 亚洲周边国家 | 韩国、朝鲜、东南亚、南亚、中亚 |

> **多归属机制**：同一热点可能同时出现在多个分类。例如"美伊谈判"会同时出现在 `international`、`us_news`、`middle_east`。

### CLI 命令（Agent 推荐）

```bash
# 获取指定分类热点（返回 JSON）
python scripts/scheduled_hotspot.py --type international --hours 24 --json
python scripts/scheduled_hotspot.py --type foreign_china --hours 24 --json
python scripts/scheduled_hotspot.py --type us_news --hours 24 --json
python scripts/scheduled_hotspot.py --type japan_news --hours 24 --json
python scripts/scheduled_hotspot.py --type middle_east --hours 24 --json
python scripts/scheduled_hotspot.py --type greater_china --hours 24 --json
python scripts/scheduled_hotspot.py --type asia_other --hours 24 --json

# 自定义时间窗口
python scripts/scheduled_hotspot.py --type us_news --hours 6 --json
python scripts/scheduled_hotspot.py --type foreign_china --hours 12 --json
```

### Agent 精简模式（推荐）

使用 `--simple` 参数获取精简格式，仅包含热点标题和来源文章，适合 Agent 处理。

```bash
# 精简模式示例
python scripts/scheduled_hotspot.py --type international --hours 12 --simple
python scripts/scheduled_hotspot.py --type foreign_china --hours 24 --simple
python scripts/scheduled_hotspot.py --type us_news --hours 6 --simple
```

**精简输出格式**：
```json
{
  "category": "国际热点",
  "count": 2,
  "hotspots": [
    {
      "title": "美伊谈判未达成协议 特朗普称双方立场差距巨大",
      "score": 192,
      "media_count": 15,
      "articles": [
        {
          "title": "美伊谈判未达成协议 特朗普称双方立场差距巨大",
          "url": "https://news.rthk.hk/rthk/ch/component/k2/1850778.htm",
          "platform": "香港电台|RTHK",
          "published": "2026-04-12T12:33:00+08:00",
          "summary": "美国与伊朗在阿曼举行的间接谈判..."
        },
        {
          "title": "特朗普称美伊谈判未达成协议",
          "url": "https://www.cnn.com/2026/04/12/world/iran-us-talks",
          "platform": "CNN|World",
          "published": "2026-04-12T12:10:26+08:00"
        }
      ]
    }
  ]
}
```

**字段说明**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `category` | string | 分类名称 |
| `count` | int | 热点数量 |
| `hotspots[].title` | string | 热点标题 |
| `hotspots[].score` | int | 热度分数（越高越重要） |
| `hotspots[].media_count` | int | 报道媒体数量 |
| `hotspots[].articles[]` | array | 来源文章列表 |
| `articles[].title` | string | 文章标题 |
| `articles[].url` | string | 原文链接 |
| `articles[].platform` | string | 平台/媒体名称 |
| `articles[].published` | string | 发布时间（ISO格式） |
| `articles[].summary` | string | 摘要（可选，最多300字） |

### Agent 使用示例

**示例 1：获取国际热点（精简格式）**
```bash
python scripts/scheduled_hotspot.py --type international --hours 12 --simple
```

**示例 2：获取美国新闻热点**
```bash
python scripts/scheduled_hotspot.py --type us_news --hours 24 --simple
```

**示例 3：获取外媒报道中国（6小时窗口）**
```bash
python scripts/scheduled_hotspot.py --type foreign_china --hours 6 --simple
```

**示例 4：获取中东新闻热点**
```bash
python scripts/scheduled_hotspot.py --type middle_east --hours 12 --simple
```

### 完整格式（--json）

不使用 `--simple` 时返回完整格式，包含更多元数据：

```json
{
  "category_id": "us_news",
  "category_name": "美国新闻",
  "executed_at": "2026-04-10T15:11:13+08:00",
  "time_window_hours": 24,
  "events": [
    {
      "title": "特朗普签署新关税令",
      "score": 37.0,
      "count": 4,
      "media_count": 3,
      "s_tier_count": 2,
      "importance": "high",
      "latest_published": "2026-04-10T14:30:00+08:00",
      "platforms": ["路透社", "纽约时报", "BBC"],
      "sources": ["路透社", "纽约时报", "BBC"],
      "tags": ["特朗普", "关税", "中国"],
      "entities": ["特朗普", "关税", "中国"],
      "is_china_related": true,
      "items": [
        {
          "id": 18562,
          "title": "特朗普签署新关税令 对中国商品加征25%",
          "url": "https://www.reuters.com/article/...",
          "platform": "路透社",
          "published": "2026-04-10T14:30:00+08:00",
          "summary": "..."
        }
      ]
    }
  ],
  "event_count": 6,
  "article_count": 25
}
```

### 其他 CLI 命令

```bash
# 查看帮助
python scripts/scheduled_hotspot.py --help

# 列出所有分类
python scripts/scheduled_hotspot.py --list

# 执行所有启用的检测
python scripts/scheduled_hotspot.py --run-all

# 执行指定分类（旧格式，仍可用）
python scripts/scheduled_hotspot.py --run china_related --json

# 查看历史记录
python scripts/scheduled_hotspot.py --history
python scripts/scheduled_hotspot.py --history --category china_related --days 7

# 启动定时服务
python scripts/scheduled_hotspot.py --daemon

# 配置管理
python scripts/scheduled_hotspot.py --enable china_related
python scripts/scheduled_hotspot.py --disable japan_news
```

### API 接口

```bash
# 获取分类配置
GET /api/hotspot/categories

# 获取最新热点
GET /api/hotspot/scheduled
GET /api/hotspot/scheduled?category=china_related

# 获取历史记录
GET /api/hotspot/history?category=china_related&days=7

# 手动执行
POST /api/hotspot/execute
{"category": "china_related", "hours": 24}

# 修改配置
PUT /api/hotspot/config/china_related
{"hours": 12, "max_results": 20}
```

### 配置文件

`config/hotspot_schedule.yaml`

```yaml
settings:
  default_hours: 24
  retention_days: 30

categories:
  china_related:
    enabled: true
    name: "外媒报道中国"
    keywords: ["中国", "北京", "习近平", "外交部", "中美", "南海"]
    hours: 24
    max_results: 15
    exclude_china_media: true
    schedule: "0 */4 * * *"
    
  us_news:
    enabled: true
    name: "美国新闻"
    keywords: ["美国", "特朗普", "拜登", "白宫", "美联储"]
    hours: 24
    max_results: 20
    schedule: "0 */6 * * *"
```

### 定时计划（cron格式）

```
┌───────── 分钟
│ ┌───────── 小时
│ │ ┌───────── 日
│ │ │ ┌───────── 月
│ │ │ │ ┌───────── 星期
│ │ │ │ │
* * * * *

示例：
0 */4 * * *     每4小时
0 */6 * * *     每6小时
0 9,15,21 * * * 每天9点、15点、21点
```