# 新闻热点提炼与国家分类系统 —— 技术方案文档

> **目标**：将 1000+ 条/天的原始新闻，稳定、准确地提炼为按国家分组的热点列表（每国 Top 20）。
> **核心原则**：LLM 只做结构化提取，聚合/分类全部用确定性逻辑完成。

---

## 一、整体架构

```
┌─────────────────────────────────────────────────┐
│                 原始新闻入库                      │
│          1000+ 条（标题 + 摘要 + 来源）           │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│          阶段 1：结构化字段提取（LLM）            │
│  并发批处理，每批 50 条，提取：                   │
│  · primary_country  主体国家（ISO 代码）          │
│  · event_key        事件规范化标签                │
│  · entities         关键实体列表                  │
│  · media_tier       媒体级别 S / A / B            │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│          阶段 2：事件聚合 + 热点判定（纯逻辑）    │
│  按 event_key 分组 → 热点评分 → 保留 Top 200     │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│          阶段 3：按国家分组输出（纯逻辑）          │
│  直接用 primary_country 字段分组                  │
│  每国 Top 20，小国热点不足时回补                  │
└─────────────────────────────────────────────────┘
```

---

## 二、数据模型

### 2.1 原始新闻条目（输入）

```python
class RawNews:
    id: str              # 唯一 ID
    title: str           # 标题
    summary: str         # 摘要（可为空）
    source: str          # 来源媒体名称
    published_at: str    # 发布时间（ISO 8601）
    url: str             # 原文链接
    raw_country: str     # 抓取时粗标注的国家（可能不准，仅参考）
```

### 2.2 结构化条目（LLM 提取后）

```python
class StructuredNews:
    id: str
    title: str
    source: str
    published_at: str
    url: str

    # --- LLM 提取的核心字段 ---
    primary_country: str    # ISO 3166-1 alpha-2，如 "US" "CN" "RU"
    event_key: str          # 规范化事件标签，如 "特朗普关税政策2025"
    entities: list[str]     # 关键实体，如 ["特朗普", "欧盟", "钢铁关税"]
    media_tier: str         # "S" | "A" | "B"
    confidence: float       # LLM 对 primary_country 的置信度 0~1
```

### 2.3 热点条目（聚合后）

```python
class HotspotEvent:
    event_key: str
    primary_country: str
    article_count: int          # 报道该事件的文章数量
    s_tier_count: int           # S 级媒体报道数
    a_tier_count: int           # A 级媒体报道数
    score: float                # 热点得分（见评分公式）
    representative_title: str   # 代表性标题（S 级优先）
    articles: list[str]         # 文章 ID 列表
    sources: list[str]          # 去重后的来源列表
```

---

## 三、媒体分级体系（S / A / B）

> **这是热点判定的基础，必须提前维护好媒体列表。**

### 配置文件：`media_tiers.json`

```json
{
  "S": [
    "Reuters", "AP", "AFP", "Bloomberg", "BBC",
    "新华社", "人民日报", "央视新闻", "华尔街日报",
    "纽约时报", "金融时报", "经济学人", "卫报",
    "CNN", "NBC", "ABC News", "CBS News",
    "参考消息", "环球时报", "中国日报"
  ],
  "A": [
    "Axios", "Politico", "The Hill", "Foreign Policy",
    "南华早报", "联合早报", "凤凰网", "澎湃新闻",
    "第一财经", "财新", "36氪",
    "The Atlantic", "Vox", "NPR", "Der Spiegel",
    "Le Monde", "朝日新闻", "读卖新闻"
  ]
}
```

> **规则**：不在 S/A 列表中的，默认归为 B 级。

---

## 四、阶段 1：结构化提取

### 4.1 LLM Prompt 设计

```
你是一个新闻分析专家。请对以下新闻条目进行结构化信息提取，返回严格的 JSON 数组，不要输出任何其他内容。

## 提取规则

### primary_country（最关键）
- 定义：该新闻事件的"行为主体国家"或"事件发生地国家"
- 不是"文章中出现频率最高的国家"
- 判断逻辑：
  · "美国对中国加征关税" → US（美国是行为主体）
  · "中国火箭发射成功" → CN（事件发生地）
  · "俄乌战场最新进展" → UA（事件发生地）
  · "G7峰会讨论对华政策" → INTL（多国/国际）
  · "以色列轰炸加沙" → IL（行为主体）
- 使用 ISO 3166-1 alpha-2 代码；多国/国际事件用 "INTL"

### event_key（热点聚合的核心）
- 用 10 字以内的中文短语描述这件事的核心
- 同一件事，不同媒体报道，event_key 必须相同
- 格式：[主体][动作/事件]（不含年份）
- 示例：
  · "特朗普宣布关税政策" "美国加征进口关税" "白宫关税令" → 统一为 "特朗普关税政策"
  · "以色列空袭加沙" "加沙平民伤亡" "IDF军事行动" → 统一为 "以色列空袭加沙"

### media_tier
- 按照你对该媒体在全球的权威性和影响力的判断
- S：全球顶级媒体（路透、AP、BBC、新华社等）
- A：主要区域媒体、知名专业媒体
- B：其他媒体、自媒体、博客

### confidence
- 对 primary_country 判断的置信度（0.0~1.0）
- 如果新闻标题模糊，置信度给低一些

## 输入格式
[{"id": "...", "title": "...", "summary": "...", "source": "..."}]

## 输出格式（严格 JSON，无 markdown）
[
  {
    "id": "原始id",
    "primary_country": "US",
    "event_key": "特朗普关税政策",
    "entities": ["特朗普", "关税", "欧盟"],
    "media_tier": "S",
    "confidence": 0.95
  }
]
```

### 4.2 批处理实现

```python
import asyncio
import json
from anthropic import AsyncAnthropic

client = AsyncAnthropic()
BATCH_SIZE = 50
MAX_CONCURRENT = 5  # 并发数，根据 API 限速调整

async def extract_batch(batch: list[dict], semaphore: asyncio.Semaphore) -> list[dict]:
    """处理单批新闻的结构化提取"""
    async with semaphore:
        input_json = json.dumps(batch, ensure_ascii=False)
        
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",  # 使用 Sonnet，性价比最高
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": f"{EXTRACTION_PROMPT}\n\n## 输入\n{input_json}"
            }]
        )
        
        text = response.content[0].text.strip()
        # 防御性解析：去除可能的 markdown 代码块
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        
        return json.loads(text)

async def extract_all(news_list: list[dict]) -> list[dict]:
    """并发处理全部新闻"""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    batches = [
        news_list[i:i+BATCH_SIZE] 
        for i in range(0, len(news_list), BATCH_SIZE)
    ]
    
    tasks = [extract_batch(batch, semaphore) for batch in batches]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    structured = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"批次 {i} 提取失败: {result}，将重试...")
            # TODO: 失败重试逻辑
        else:
            structured.extend(result)
    
    return structured
```

---

## 五、阶段 2：事件聚合与热点判定

### 5.1 热点评分公式

```
score = (S级报道数 × 10) + (A级报道数 × 3) + (B级报道数 × 1) + (独立来源数 × 2)

热点入选条件（满足任一）：
  · score ≥ 10（S级单篇即可入选）
  · article_count ≥ 3（3家以上媒体报道）
  · media_tier == "S"（S级直接入选）
```

### 5.2 event_key 二次规范化（解决 LLM 输出不一致问题）

LLM 可能对同一事件输出略有差异的 event_key，需要做二次合并：

```python
from collections import defaultdict
from difflib import SequenceMatcher

def normalize_event_keys(structured_news: list[dict]) -> list[dict]:
    """
    对相似的 event_key 进行合并归一化。
    使用字符串相似度，将相似度 > 0.75 的 key 合并为出现频率最高的那个。
    """
    # 收集所有 event_key
    all_keys = [n["event_key"] for n in structured_news]
    key_freq = defaultdict(int)
    for k in all_keys:
        key_freq[k] += 1
    
    unique_keys = list(key_freq.keys())
    
    # 构建合并映射
    merge_map = {}  # key → canonical_key
    merged = set()
    
    for i, key_a in enumerate(unique_keys):
        if key_a in merged:
            continue
        group = [key_a]
        for key_b in unique_keys[i+1:]:
            if key_b in merged:
                continue
            similarity = SequenceMatcher(None, key_a, key_b).ratio()
            if similarity > 0.75:
                group.append(key_b)
                merged.add(key_b)
        
        # 选频率最高的作为规范 key
        canonical = max(group, key=lambda k: key_freq[k])
        for k in group:
            merge_map[k] = canonical
    
    # 更新所有条目
    for news in structured_news:
        news["event_key"] = merge_map.get(news["event_key"], news["event_key"])
    
    return structured_news
```

### 5.3 聚合与评分

```python
def aggregate_hotspots(structured_news: list[dict], media_tiers: dict) -> list[HotspotEvent]:
    """按 event_key 聚合，计算热度分，返回热点列表"""
    
    # 先加载媒体分级查找表
    tier_lookup = {}
    for tier, sources in media_tiers.items():
        for source in sources:
            tier_lookup[source.lower()] = tier
    
    groups = defaultdict(list)
    for news in structured_news:
        groups[news["event_key"]].append(news)
    
    hotspots = []
    for event_key, articles in groups.items():
        # 统计各级媒体数量
        s_count = sum(1 for a in articles if a["media_tier"] == "S")
        a_count = sum(1 for a in articles if a["media_tier"] == "A")
        b_count = sum(1 for a in articles if a["media_tier"] == "B")
        
        unique_sources = list(set(a["source"] for a in articles))
        
        score = s_count * 10 + a_count * 3 + b_count * 1 + len(unique_sources) * 2
        
        # 热点判定
        is_hot = (
            s_count >= 1 or
            len(articles) >= 3 or
            score >= 10
        )
        
        if not is_hot:
            continue
        
        # 选代表性标题：S级优先，其次A级，其次最多报道
        rep_article = (
            next((a for a in articles if a["media_tier"] == "S"), None) or
            next((a for a in articles if a["media_tier"] == "A"), None) or
            articles[0]
        )
        
        # 主体国家：投票决定（置信度加权）
        country_votes = defaultdict(float)
        for a in articles:
            country_votes[a["primary_country"]] += a.get("confidence", 0.8)
        primary_country = max(country_votes, key=country_votes.get)
        
        hotspots.append(HotspotEvent(
            event_key=event_key,
            primary_country=primary_country,
            article_count=len(articles),
            s_tier_count=s_count,
            a_tier_count=a_count,
            score=score,
            representative_title=rep_article["title"],
            articles=[a["id"] for a in articles],
            sources=unique_sources
        ))
    
    # 按得分排序，保留 Top 200
    hotspots.sort(key=lambda h: h.score, reverse=True)
    return hotspots[:200]
```

---

## 六、阶段 3：按国家分组与输出

### 6.1 分组逻辑

```python
def group_by_country(hotspots: list[HotspotEvent], top_n: int = 20) -> dict:
    """
    按 primary_country 分组，每国取 Top N。
    对小国（热点数 < 5）触发回补逻辑。
    """
    country_groups = defaultdict(list)
    for h in hotspots:
        country_groups[h.primary_country].append(h)
    
    # 每国内部按 score 排序，取 Top N
    result = {}
    for country, events in country_groups.items():
        events.sort(key=lambda e: e.score, reverse=True)
        result[country] = events[:top_n]
    
    return result

def backfill_small_countries(
    result: dict,
    all_structured_news: list[dict],
    target_countries: list[str],
    min_hotspots: int = 5
):
    """
    对热点数不足 min_hotspots 的目标国家，
    直接从原始 1000+ 条中补充该国新闻。
    """
    for country in target_countries:
        current_count = len(result.get(country, []))
        if current_count < min_hotspots:
            # 从原始结构化条目中找该国新闻（按 confidence 降序）
            country_news = [
                n for n in all_structured_news 
                if n["primary_country"] == country and n.get("confidence", 0) > 0.7
            ]
            country_news.sort(key=lambda n: n.get("confidence", 0), reverse=True)
            
            # 转为热点格式补充
            existing_keys = {e.event_key for e in result.get(country, [])}
            for news in country_news:
                if news["event_key"] not in existing_keys:
                    result.setdefault(country, []).append(
                        HotspotEvent(
                            event_key=news["event_key"],
                            primary_country=country,
                            article_count=1,
                            s_tier_count=1 if news["media_tier"] == "S" else 0,
                            a_tier_count=1 if news["media_tier"] == "A" else 0,
                            score=10 if news["media_tier"] == "S" else 3,
                            representative_title=news["title"],
                            articles=[news["id"]],
                            sources=[news["source"]]
                        )
                    )
                    existing_keys.add(news["event_key"])
                    if len(result[country]) >= min_hotspots:
                        break
```

---

## 七、完整主流程

```python
import asyncio
import json

async def run_pipeline(raw_news: list[dict], config: dict) -> dict:
    """
    完整流水线入口
    
    Args:
        raw_news: 原始新闻列表
        config: 配置（媒体分级、目标国家、Top N 等）
    
    Returns:
        按国家分组的热点字典
    """
    print(f"[1/4] 开始结构化提取，共 {len(raw_news)} 条...")
    structured = await extract_all(raw_news)
    print(f"[1/4] 提取完成，成功 {len(structured)} 条")
    
    print("[2/4] event_key 归一化...")
    structured = normalize_event_keys(structured)
    
    print("[3/4] 聚合热点...")
    hotspots = aggregate_hotspots(structured, config["media_tiers"])
    print(f"[3/4] 得到热点 {len(hotspots)} 条")
    
    print("[4/4] 按国家分组...")
    result = group_by_country(hotspots, top_n=config.get("top_n", 20))
    
    # 对关注的目标国家做补充
    if config.get("target_countries"):
        backfill_small_countries(
            result, structured,
            config["target_countries"],
            min_hotspots=config.get("min_hotspots", 5)
        )
    
    return result


# 运行示例
if __name__ == "__main__":
    with open("raw_news.json") as f:
        raw_news = json.load(f)
    
    config = {
        "media_tiers": json.load(open("media_tiers.json")),
        "top_n": 20,
        "target_countries": ["US", "CN", "RU", "JP", "KR", "DE", "GB", "FR", "IL", "UA"],
        "min_hotspots": 5
    }
    
    result = asyncio.run(run_pipeline(raw_news, config))
    
    with open("hotspots_output.json", "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print("\n=== 输出摘要 ===")
    for country, events in result.items():
        print(f"{country}: {len(events)} 条热点")
```

---

## 八、关键问题解决对照表

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 相似度匹配不准 | 字面文本差异大 | 改用 LLM 提取规范化 `event_key`，直接精确分组 |
| 媒体报道数量不准 | 统计的是相似文章数，边界模糊 | 统计相同 `event_key` 下的文章数，边界清晰 |
| 国家分类乱（美国归中国） | 关键词频率 ≠ 主体国家 | LLM 理解语义提取主体国家，明确 Prompt 定义 |
| LLM 输出 event_key 不一致 | LLM 概率性输出 | 二次相似度归一化（SequenceMatcher）合并相似 key |
| 小国热点被大国挤掉 | 全局竞争得分 | 目标国家热点不足时，从原始条目补充 |

---

## 九、目录结构建议

```
news-hotspot/
├── main.py                  # 主流程入口
├── extractor.py             # 阶段1：LLM 结构化提取
├── aggregator.py            # 阶段2：热点聚合逻辑
├── grouper.py               # 阶段3：国家分组
├── normalizer.py            # event_key 归一化
├── config/
│   ├── media_tiers.json     # 媒体分级配置（需持续维护）
│   └── countries.json       # 目标国家列表
├── data/
│   ├── raw_news.json        # 输入：原始新闻
│   ├── structured.json      # 中间：结构化结果（可缓存）
│   └── hotspots_output.json # 输出：热点结果
└── prompts/
    └── extraction_prompt.txt # LLM Prompt 版本管理
```

---

## 十、成本与性能估算

| 指标 | 数值 |
|------|------|
| 输入规模 | 1000 条 |
| 批次数量 | 20 批（每批 50 条） |
| 并发数 | 5 |
| 预计 API 耗时 | 3~5 分钟 |
| Token 消耗（每条约 200 tokens） | ~200K tokens |
| 费用估算（Sonnet）| ~$0.6 / 次运行 |
| 阶段2/3 耗时（纯 Python 逻辑） | < 1 秒 |

> **优化建议**：结构化结果可缓存到数据库，24小时内相同 `url` 不重复提取。

---

## 十一、后续迭代方向

1. **event_key 字典积累**：随着运行积累，建立事件词典，后续可用本地匹配替代部分 LLM 调用
2. **媒体分级自动化**：接入媒体权威性数据库（如 Alexa Rank）动态维护 S/A/B 级别
3. **多语言对齐**：英文 "Trump tariff" 和中文 "特朗普关税" 应该映射到同一 event_key（可加翻译步骤或统一用英文 key）
4. **实时监控**：对 S 级媒体新条目做即时处理，不等批次聚合
5. **结果评估**：人工标注 100 条，计算 primary_country 准确率和 event_key 聚合率，用于迭代 Prompt
