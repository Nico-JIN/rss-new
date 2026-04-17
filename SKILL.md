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

## Agent 推荐接口

### 1. 获取增量新闻 `feed`

```bash
# 精简模式（推荐）
python scripts/api_cli.py feed --hours 2 --simple

# PowerShell 隐藏日志
python scripts/api_cli.py feed --hours 2 --simple 2>$null
```

**输出格式**：
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

### 2. 获取分类热点 `scheduled_hotspot`

```bash
# 国际热点
python scripts/scheduled_hotspot.py --type international --hours 12 --simple

# 外媒报道中国
python scripts/scheduled_hotspot.py --type foreign_china --hours 24 --simple

# 美国新闻
python scripts/scheduled_hotspot.py --type us_news --hours 6 --simple

# 中东新闻
python scripts/scheduled_hotspot.py --type middle_east --hours 12 --simple

# 日本新闻
python scripts/scheduled_hotspot.py --type japan_news --hours 24 --simple

# 港澳台新闻
python scripts/scheduled_hotspot.py --type greater_china --hours 24 --simple

# 亚洲周边国家
python scripts/scheduled_hotspot.py --type asia_other --hours 24 --simple
```

**输出格式**：
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
          "title": "美伊谈判未达成协议...",
          "url": "https://news.rthk.hk/...",
          "platform": "香港电台|RTHK",
          "published": "2026-04-12T12:33:00+08:00",
          "summary": "美国与伊朗在阿曼举行的间接谈判..."
        }
      ]
    }
  ]
}
```

### 3. 关键字搜索 `search`

```bash
# 基础搜索
python scripts/api_cli.py search --keyword "Trump" --hours 24

# 智能搜索（自动提取国家、人物等实体）
python scripts/api_cli.py search --keyword "匈牙利x" --smart --hours 24

# 仅本地库
python scripts/api_cli.py search --keyword "中国" --hours 6 --no-external
```

**输出格式**：
```json
{
  "api": "search",
  "query": {
    "keyword": "匈牙利x",
    "keywords_extracted": ["匈牙利", "Hungary"],
    "smart_search": true,
    "hours": 24,
    "generated_at": "2026-04-13T..."
  },
  "count": 25,
  "items": [
    {
      "title": "匈牙利总理欧尔班...",
      "url": "https://...",
      "platform": "路透社|x.com",
      "published": "2026-04-13T...",
      "summary": "匈牙利总理欧尔班的选举失败...",
      "image": "https://..."
    }
  ]
}
```

**智能搜索说明**：
- 自动去除噪声字符（如 "x"、"空格"）
- 提取国家中英文（"匈牙利" → ["匈牙利", "Hungary"]）
- 提取人物名（"特朗普" → ["特朗普", "Trump"]）
- 多关键词搜索并去重，返回所有相关新闻

---

## 分类说明

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

---

## 意图→命令速查

| 用户意图 | 命令 |
|---------|------|
| 近N小时新闻 | `python scripts/api_cli.py feed --hours N --simple` |
| 国际热点 | `python scripts/scheduled_hotspot.py --type international --hours 12 --simple` |
| 外媒报道中国 | `python scripts/scheduled_hotspot.py --type foreign_china --hours 24 --simple` |
| 美国新闻热点 | `python scripts/scheduled_hotspot.py --type us_news --hours 6 --simple` |
| 中东新闻热点 | `python scripts/scheduled_hotspot.py --type middle_east --hours 12 --simple` |
| 搜索某主题 | `python scripts/api_cli.py search --keyword "主题" --hours 24` |
| 智能搜索（提取国家/人物） | `python scripts/api_cli.py search --keyword "匈牙利x" --smart --hours 24` |

---

## 其他接口

### 热点捕获 `hotspot`

```bash
python scripts/api_cli.py hotspot --hours 24 --max 10
```

### 深度研究 `research`

```bash
python scripts/api_cli.py research --keyword "特朗普关税" --hours 72
```

### 价值分析 `value`

```bash
python scripts/api_cli.py value --article-id 100
```

---

## 管理命令

```bash
# 刷新数据
python scripts/poll.py --once

# 查看分类配置
python scripts/scheduled_hotspot.py --list

# 执行所有检测
python scripts/scheduled_hotspot.py --run-all

# 查看历史记录
python scripts/scheduled_hotspot.py --history --days 7
```

---

## 配置文件

| 文件 | 用途 |
|------|------|
| `config/feeds.yaml` | RSS订阅源、外部搜索引擎 |
| `config/llm_topics.yaml` | LLM配置（打标、翻译） |
| `config/api_keys.json` | API密钥 |
| `config/hotspot_schedule.yaml` | 热点检测配置 |

---

## REST API

服务地址：`http://localhost:5001`

| 端点 | 说明 |
|------|------|
| `/api/v2/feed?hours=6` | 全量获取 |
| `/api/v2/search/unified?keyword=x&hours=24` | 统一搜索 |
| `/api/v2/hotspot?hours=24` | 热点捕获 |
| `/api/hotspot/scheduled?category=us_news` | 分类热点 |

**Playground**：`http://localhost:5001/playground`
