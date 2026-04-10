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

---

## 定时热点检测（6大分类）

自动检测6类新闻热点，支持定时执行和手动触发。**所有接口返回 JSON 格式**。

### 分类列表

| ID | 名称 | 说明 |
|----|------|------|
| `china_related` | 外媒报道中国 | 排除中国媒体，仅看外媒视角 |
| `us_news` | 美国新闻 | 美国内政、外交、军事、经济 |
| `japan_news` | 日本新闻 | 日本政治、经济、军事、外交 |
| `middle_east` | 中东新闻 | 中东冲突、石油、外交 |
| `hk_tw_macau` | 港澳台新闻 | 港台政治、两岸关系 |
| `asia_neighbors` | 亚洲周边国家 | 韩国、朝鲜、东南亚、南亚、中亚 |

### CLI 命令（Agent 推荐）

```bash
# 获取指定分类热点（返回 JSON）
python scripts/scheduled_hotspot.py --type china_related --hours 24 --json
python scripts/scheduled_hotspot.py --type us_news --hours 24 --json
python scripts/scheduled_hotspot.py --type japan_news --hours 24 --json
python scripts/scheduled_hotspot.py --type middle_east --hours 24 --json
python scripts/scheduled_hotspot.py --type hk_tw_macau --hours 24 --json
python scripts/scheduled_hotspot.py --type asia_neighbors --hours 24 --json

# 自定义时间窗口
python scripts/scheduled_hotspot.py --type us_news --hours 6 --json
python scripts/scheduled_hotspot.py --type china_related --hours 12 --json
```

### 返回 JSON 格式

```json
{
  "category": "us_news",
  "category_name": "美国新闻",
  "executed_at": "2026-04-10T15:11:13+08:00",
  "time_window_hours": 24,
  "events": [
    {
      "title": "特朗普签署新关税令",
      "score": 37.0,
      "count": 4,
      "media_count": 3,
      "importance": "high",
      "latest_published": "2026-04-10T14:30:00+08:00",
      "platforms": ["路透社", "纽约时报", "BBC"],
      "tags": ["特朗普", "关税", "中国"],
      "summary": "特朗普宣布对中国商品加征关税...",
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

### Agent 使用示例

**示例 1：获取美国新闻热点**
```bash
python scripts/scheduled_hotspot.py --type us_news --hours 24 --json
```

**示例 2：获取外媒报道中国（6小时窗口）**
```bash
python scripts/scheduled_hotspot.py --type china_related --hours 6 --json
```

**示例 3：获取亚洲周边国家热点**
```bash
python scripts/scheduled_hotspot.py --type asia_neighbors --hours 24 --json
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