#!/usr/bin/env python3
"""
事件时间线引擎 — 从选定文章生成完整事件时间线，支持持续跟踪

设计：
1. 关键词提取：复用 hotspot_detector 的实体识别算法
2. 历史搜索：从已有 RSS 数据中搜索相关内容
3. 全文获取：复用 intelligence 的 Jina Reader 逻辑
4. LLM 生成：调用 LLM 整合生成结构化时间线
5. 持续跟踪：定期检查新进展，自动更新时间线

使用方式：
    from timeline import generate_timeline, track_timeline_updates

    timeline = generate_timeline([1, 2, 3])  # 从文章ID创建时间线
    result = track_timeline_updates(timeline_id)  # 跟踪最新进展
"""

import json
import re
from datetime import datetime, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# 导入本项目已有模块
try:
    from store import (
        get_conn, TZ_BJ,
        create_timeline, get_timeline, list_timelines,
        add_timeline_event, update_timeline_events,
        update_timeline_summary, update_timeline_track_time,
        set_timeline_status, delete_timeline, get_articles_by_ids,
        query_by_keyword
    )
    from llm_tagger import _call_deepseek, load_llm_config, is_english_text, batch_translate_to_chinese
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent))
    from store import (
        get_conn, TZ_BJ,
        create_timeline, get_timeline, list_timelines,
        add_timeline_event, update_timeline_events,
        update_timeline_summary, update_timeline_track_time,
        set_timeline_status, delete_timeline, get_articles_by_ids,
        query_by_keyword
    )
    from llm_tagger import _call_deepseek, load_llm_config, is_english_text, batch_translate_to_chinese

# 复用 intelligence.py 的内容清洗函数
from intelligence import clean_jina_content


# ═══════════════════════════════════════════════════════════════════
# 关键词提取（复用 hotspot_detector 算法）
# ═══════════════════════════════════════════════════════════════════

def _tokenize(text: str) -> list:
    """分词：提取中文词组和英文单词"""
    if not text:
        return []
    words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', text)
    return [w.lower() for w in words if len(w) > 1]


def _extract_entities(text: str) -> set:
    """提取命名实体（人名、地名、机构名）"""
    if not text:
        return set()

    entities = set()

    # 常见国际人物名
    person_patterns = [
        r'特朗普|拜登|普京|泽连斯基|习近平|马克龙|苏纳克|岸田文雄|尹锡悦|莫迪',
        r'金正恩|哈梅内伊|内塔尼亚胡|埃尔多安|朔尔茨|冯德莱恩|耶伦|布林肯',
        r'Trump|Biden|Putin|Zelensky|Xi Jinping|Macron|Modi|Kim Jong Un'
    ]

    # 常见国家/地名
    place_patterns = [
        r'美国|中国|俄罗斯|乌克兰|日本|韩国|朝鲜|伊朗|以色列|巴勒斯坦',
        r'台湾|香港|南海|中东|欧洲|欧盟|北约|东盟|非洲|拉美',
        r'US|USA|China|Russia|Ukraine|Japan|Korea|Iran|Israel|Taiwan'
    ]

    # 常见机构名
    org_patterns = [
        r'白宫|克里姆林宫|国防部|外交部|联合国|世卫组织|世贸组织',
        r'中央银行|美联储|欧央行|IMF|世界银行|OPEC|欧盟委员会',
        r'White House|Pentagon|UN|WHO|WTO|Fed|ECB|IMF'
    ]

    for pattern in person_patterns + place_patterns + org_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        entities.update(m.lower() for m in matches)

    return entities


def extract_timeline_keywords(articles: list) -> list:
    """
    从文章中提取事件核心关键词

    策略：
    1. 实体识别（人名/地名/机构名）优先级最高
    2. 高频词统计补充
    3. 标题交集词加权
    """
    all_entities = set()
    all_words = []

    for art in articles:
        title = art.get('title', '')
        summary = art.get('summary', '')
        combined = title + ' ' + (summary or '')

        # 实体提取
        all_entities.update(_extract_entities(combined))

        # 分词
        all_words.extend(_tokenize(combined))

    # 实体优先级最高（最多5个）
    keywords = list(all_entities)[:5]

    # 补充高频词
    word_counts = Counter(all_words)
    for word, _ in word_counts.most_common(10):
        if word not in keywords and len(word) > 2:
            keywords.append(word)

    return keywords[:8]


# ═══════════════════════════════════════════════════════════════════
# RSS 搜索相关内容
# ═══════════════════════════════════════════════════════════════════

def search_related_in_rss(keywords: list, time_range_days: int = 30,
                          exclude_ids: list = None, conn=None) -> list:
    """
    从已有 RSS 数据中搜索相关文章

    Args:
        keywords: 关键词列表
        time_range_days: 搜索时间范围（天）
        exclude_ids: 要排除的文章ID（已选中的）
        conn: 数据库连接

    Returns:
        相关文章列表
    """
    own_conn = False
    if conn is None:
        conn = get_conn()
        own_conn = True

    now = datetime.now(TZ_BJ)
    start = (now - timedelta(days=time_range_days)).isoformat()

    related = []
    # 取前3个核心关键词搜索
    for kw in keywords[:3]:
        items = query_by_keyword(kw, start=start, end=now.isoformat(),
                                 limit=50, conn=conn)
        related.extend(items)

    # 去重并排除已选中的文章
    seen = set()
    exclude_set = set(exclude_ids or [])
    unique = []

    for art in related:
        art_id = art.get('id')
        url_hash = art.get('url_hash')

        # 排除已选中的
        if art_id in exclude_set:
            continue

        # URL hash 去重
        if url_hash and url_hash not in seen:
            seen.add(url_hash)
            unique.append(art)
        elif art_id and art_id not in seen:
            # 没有 url_hash 时用 id 去重
            seen.add(art_id)
            unique.append(art)

    if own_conn:
        conn.close()

    return unique


# ═══════════════════════════════════════════════════════════════════
# 全文获取（复用 intelligence.py 的逻辑）
# ═══════════════════════════════════════════════════════════════════

def fetch_article_content(url: str, timeout: int = 20) -> str:
    """通过 Jina Reader 获取文章全文"""
    jina_url = f"https://r.jina.ai/{url}"
    try:
        resp = requests.get(jina_url, headers={'Accept': 'application/json'}, timeout=timeout)
        if resp.status_code == 200:
            content_type = resp.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                payload = resp.json()
                data = payload.get('data', {})
                content = data.get('content') or data.get('text')
                if content:
                    return clean_jina_content(content)
            else:
                # 非 JSON 格式，直接清洗文本
                return clean_jina_content(resp.text)
    except Exception as e:
        print(f"[WARN] Jina 获取失败: {url[:50]} - {e}")
    return ''


def enrich_article_content(articles: list, max_workers: int = 5) -> list:
    """
    批量获取文章全文内容

    Args:
        articles: 文章列表
        max_workers: 并发数

    Returns:
        更新了 content 字段的文章列表
    """
    # 筛选需要获取全文的文章
    missing_content = [a for a in articles if not a.get('content') or len(a.get('content')) < 50]

    if not missing_content:
        return articles

    print(f"[INFO] 正在为 {len(missing_content)} 篇文章获取全文...")

    enriched = {}

    def fetch_task(item):
        content = fetch_article_content(item['url'])
        return item['id'], content

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_task, item) for item in missing_content]
        for future in as_completed(futures, timeout=60):
            try:
                art_id, content = future.result()
                if content:
                    enriched[art_id] = content
            except Exception as e:
                print(f"[WARN] 获取任务失败: {e}")

    # 更新文章的 content 字段
    for art in articles:
        if art['id'] in enriched:
            art['content'] = enriched[art['id']]
            # 同步更新数据库（可选）
            try:
                from store import update_article_content
                update_article_content(art['id'], enriched[art['id']])
            except:
                pass

    return articles


# ═══════════════════════════════════════════════════════════════════
# LLM 时间线生成
# ═══════════════════════════════════════════════════════════════════

def build_timeline_prompt(articles: list) -> tuple:
    """构建时间线生成的 Prompt"""

    system_prompt = """你是一位资深的事件研究员和新闻时间线编辑。
你的任务是根据提供的新闻素材，构建一个完整的事件时间线。

## 输出要求

返回严格 JSON 格式，结构如下：
{
  "title": "事件时间线标题",
  "summary": "事件概述 (100-200字)",
  "keywords": ["关键词1", "关键词2"],
  "events": [
    {
      "event_time": "2026-03-15T10:00:00+08:00",
      "title": "事件标题",
      "description": "事件描述",
      "is_key_event": true,
      "importance": 3.5
    }
  ]
}

## 时间线构建原则

1. **时间顺序**: 严格按照事件发生时间排序（从早到晚）
2. **事件节点**:
   - 提取关键时间节点（重大突破、转折点、重要表态）
   - 补充背景事件（起因、前情）
   - 标记关键事件 (is_key_event: true)
3. **重要程度**:
   - 5分：重大转折/突破
   - 3-4分：重要进展
   - 1-2分：背景/辅助信息
4. **描述规范**:
   - 简洁客观，每条描述控制在50-100字
   - 注明来源媒体（如 [路透社报道]）
   - 标注不确定信息（如 [时间待核实]）
5. **数据溯源**:
   - 只使用素材中明确提及的信息
   - 素材中没有的时间点，标注 [待补充]

## 注意事项

- 不要添加素材中不存在的信息
- 每个事件节点都要有明确的时间（尽可能精确）
- 事件标题要简短有力，概括核心内容"""

    # 构建素材文本
    context_lines = []
    for idx, art in enumerate(articles):
        body = art.get('content') or art.get('summary') or "(内容暂不可用)"
        if len(body) > 800:
            body = body[:800] + "..."

        info = f"--- [素材 {idx+1}] ---\n"
        info += f"标题: {art['title']}\n"
        info += f"媒体: {art.get('platform', 'N/A')}\n"
        info += f"时间: {art['published']}\n"
        info += f"链接: {art['url']}\n"
        info += f"正文摘要: {body}\n"
        context_lines.append(info)

    articles_data = "\n".join(context_lines)
    user_prompt = f"请根据以下 {len(articles)} 篇新闻素材构建事件时间线：\n\n{articles_data}"

    return system_prompt, user_prompt


def call_llm_for_timeline(articles: list, provider: str = None) -> dict:
    """调用 LLM 生成时间线"""

    cfg = load_llm_config()
    if provider:
        cfg.setdefault('llm', {})['provider'] = provider

    sys_p, user_p = build_timeline_prompt(articles)
    messages = [{"role": "system", "content": sys_p},
                {"role": "user", "content": user_p}]

    raw = _call_deepseek(messages, cfg)

    if not raw:
        # Fallback: 使用本地算法生成简化时间线
        return generate_local_timeline(articles)

    try:
        # 解析 LLM 返回的 JSON
        # 清理可能的 markdown 包装
        cleaned = re.sub(r'```json\n?|\n?```', '', raw).strip()
        data = json.loads(cleaned)
        return data
    except Exception as e:
        print(f"[WARN] LLM 返回解析失败: {e}")
        return generate_local_timeline(articles)


def generate_local_timeline(articles: list) -> dict:
    """
    本地算法生成简化时间线（LLM 失败时的 fallback）

    策略：
    1. 按发布时间排序文章
    2. 提取标题作为事件标题
    3. 使用摘要作为描述
    """
    if not articles:
        return {"title": "空时间线", "summary": "", "keywords": [], "events": []}

    # 按时间排序
    sorted_articles = sorted(articles, key=lambda x: x.get('published', ''))

    # 提取关键词
    keywords = extract_timeline_keywords(articles)

    # 生成事件列表
    events = []
    for idx, art in enumerate(sorted_articles):
        event = {
            "event_time": art.get('published', datetime.now(TZ_BJ).isoformat()),
            "title": art.get('title', ''),
            "description": f"[{art.get('platform', '来源')}] {art.get('summary', '')[:100]}",
            "is_key_event": idx < 3,  # 前几个事件标记为关键
            "importance": 2.0
        }
        events.append(event)

    # 生成标题和概述
    title = f"{keywords[0] if keywords else '事件'}发展时间线"
    summary = f"根据 {len(articles)} 篇媒体报道整理的事件发展脉络。"

    return {
        "title": title,
        "summary": summary,
        "keywords": keywords,
        "events": events
    }


# ═══════════════════════════════════════════════════════════════════
# 主流程：生成时间线
# ═══════════════════════════════════════════════════════════════════

def generate_timeline(article_ids: list, provider: str = None,
                      search_days: int = 30) -> dict:
    """
    从选定文章生成事件时间线

    流程：
    1. 获取文章详情
    2. 提取关键词并搜索相关历史内容
    3. 获取全文内容
    4. 调用 LLM 生成时间线结构
    5. 持久化到数据库

    Args:
        article_ids: 选定的文章 ID 列表
        provider: LLM 提供商（可选）
        search_days: 搜索历史内容的天数

    Returns:
        时间线详情（包含 events）
    """
    print(f"[INFO] 开始生成时间线，源文章数: {len(article_ids)}")

    # 1. 获取源文章
    conn = get_conn()
    source_articles = get_articles_by_ids(article_ids, conn)

    if not source_articles:
        conn.close()
        return {"error": "未找到任何有效文章"}

    print(f"[INFO] 获取到 {len(source_articles)} 篇源文章")

    # 2. 提取关键词
    keywords = extract_timeline_keywords(source_articles)
    print(f"[INFO] 提取关键词: {keywords}")

    # 3. 从 RSS 搜索相关历史内容
    related_articles = search_related_in_rss(
        keywords, time_range_days=search_days,
        exclude_ids=article_ids, conn=conn
    )
    print(f"[INFO] 搜索到 {len(related_articles)} 篇相关历史文章")

    # 合并所有文章
    all_articles = source_articles + related_articles

    # 4. 获取全文内容
    all_articles = enrich_article_content(all_articles)

    # 5. 翻译英文内容（如果有）
    english_titles = []
    for art in all_articles:
        if is_english_text(art.get('title', '')):
            english_titles.append(art['title'])

    if english_titles:
        print(f"[INFO] 翻译 {len(english_titles)} 个英文标题...")
        translation_map = batch_translate_to_chinese(english_titles)
        for art in all_articles:
            orig = art.get('title', '')
            if orig in translation_map:
                art['title'] = translation_map[orig]

    conn.close()

    # 6. 调用 LLM 生成时间线
    print(f"[INFO] 调用 LLM 生成时间线...")
    timeline_data = call_llm_for_timeline(all_articles, provider)

    # 7. 持久化到数据库
    timeline_id = create_timeline(
        title=timeline_data.get('title', '事件时间线'),
        keywords=timeline_data.get('keywords', keywords),
        source_article_ids=article_ids,
        summary=timeline_data.get('summary', '')
    )

    print(f"[INFO] 时间线已创建，ID: {timeline_id}")

    # 8. 插入事件节点（附带溯源信息）
    events = timeline_data.get('events', [])

    # 为每个事件附加溯源信息
    for event in events:
        # 尝试匹配对应的源文章
        matched_article = None
        for art in all_articles:
            # 时间接近匹配
            art_time = art.get('published', '')
            event_time = event.get('event_time', '')
            if art_time and event_time:
                try:
                    dt_art = datetime.fromisoformat(art_time.replace('Z', '+00:00'))
                    dt_evt = datetime.fromisoformat(event_time.replace('Z', '+00:00'))
                    if abs((dt_art - dt_evt).total_seconds()) < 3600 * 12:  # 12小时内
                        matched_article = art
                        break
                except:
                    pass

        if matched_article:
            event['source_type'] = 'rss'
            event['source_url'] = matched_article.get('url', '')
            event['source_platform'] = matched_article.get('platform', '')
            event['source_article_id'] = matched_article.get('id')
        else:
            event['source_type'] = 'llm_inference'
            event['source_url'] = ''
            event['source_platform'] = ''

    # 批量插入事件
    update_timeline_events(timeline_id, events)

    # 9. 返回完整时间线
    return get_timeline(timeline_id)


# ═══════════════════════════════════════════════════════════════════
# 持续跟踪
# ═══════════════════════════════════════════════════════════════════

def extract_new_events(new_articles: list, existing_timeline: dict) -> list:
    """
    从新文章中提取时间线新事件

    Args:
        new_articles: 新发现的文章
        existing_timeline: 现有时间线数据

    Returns:
        需要添加的新事件列表
    """
    existing_events = existing_timeline.get('events', [])
    existing_times = set()
    for e in existing_events:
        try:
            dt = datetime.fromisoformat(e['event_time'].replace('Z', '+00:00'))
            existing_times.add(dt.strftime('%Y-%m-%d'))
        except:
            pass

    new_events = []
    for art in new_articles:
        art_time = art.get('published', '')
        try:
            dt = datetime.fromisoformat(art_time.replace('Z', '+00:00'))
            date_key = dt.strftime('%Y-%m-%d')
            # 只添加不在现有时间线中的事件
            if date_key not in existing_times:
                event = {
                    "event_time": art_time,
                    "title": art.get('title', ''),
                    "description": f"[{art.get('platform', '来源')}] {art.get('summary', '')[:100]}",
                    "is_key_event": False,
                    "importance": 2.0,
                    "source_type": 'rss',
                    "source_url": art.get('url', ''),
                    "source_platform": art.get('platform', ''),
                    "source_article_id": art.get('id')
                }
                new_events.append(event)
        except:
            pass

    return new_events


def track_timeline_updates(timeline_id: int, provider: str = None) -> dict:
    """
    跟踪时间线后续发展

    流程：
    1. 获取时间线关键词
    2. 搜索最近24小时的新文章
    3. 过滤已存在于时间线中的文章
    4. 提取新事件并更新时间线

    Args:
        timeline_id: 时间线 ID
        provider: LLM 提供商

    Returns:
        更新结果
    """
    conn = get_conn()
    timeline = get_timeline(timeline_id, conn)

    if not timeline:
        conn.close()
        return {"error": "时间线不存在"}

    keywords = timeline.get('keywords', [])
    source_ids = timeline.get('source_article_ids', [])

    # 搜索最近24小时的新文章
    new_articles = search_related_in_rss(
        keywords, time_range_days=1,
        exclude_ids=source_ids, conn=conn
    )

    if not new_articles:
        conn.close()
        return {"updated": False, "new_count": 0, "message": "暂无新的进展"}

    print(f"[INFO] 发现 {len(new_articles)} 篇新文章")

    # 获取全文
    new_articles = enrich_article_content(new_articles)

    # 提取新事件
    new_events = extract_new_events(new_articles, timeline)

    if not new_events:
        conn.close()
        return {"updated": False, "new_count": 0, "message": "新文章已存在时间线中"}

    # 获取现有事件并合并
    existing_events = timeline.get('events', [])
    all_events = existing_events + new_events

    # 按时间排序
    all_events.sort(key=lambda x: x.get('event_time', ''))

    # 更新时间线
    update_timeline_events(timeline_id, all_events, conn)

    # 更新源文章列表
    new_ids = [a['id'] for a in new_articles if a.get('id')]
    updated_source_ids = source_ids + new_ids
    from store import create_timeline  # 用于更新，实际需要单独函数
    # 这里简化处理，直接更新数据库
    conn.execute(
        "UPDATE timelines SET source_article_ids = ? WHERE id = ?",
        [json.dumps(updated_source_ids, ensure_ascii=False), timeline_id]
    )
    conn.commit()

    # 更新跟踪时间
    update_timeline_track_time(timeline_id, conn)

    conn.close()

    return {
        "updated": True,
        "new_count": len(new_events),
        "new_events": new_events,
        "message": f"发现 {len(new_events)} 条新进展"
    }


# ═══════════════════════════════════════════════════════════════════
# 导出功能
# ═══════════════════════════════════════════════════════════════════

def export_timeline_markdown(timeline_id: int) -> str:
    """
    导出时间线为 Markdown 格式

    Args:
        timeline_id: 时间线 ID

    Returns:
        Markdown 文本
    """
    timeline = get_timeline(timeline_id)

    if not timeline:
        return "时间线不存在"

    lines = []

    # 标题
    lines.append(f"# {timeline['title']}\n\n")

    # 概述
    lines.append(f"**概述**: {timeline['summary']}\n\n")

    # 关键词
    keywords = timeline.get('keywords', [])
    if keywords:
        lines.append(f"**关键词**: {', '.join(keywords)}\n\n")

    # 时间线
    lines.append("## 事件时间线\n\n")

    events = timeline.get('events', [])
    for event in events:
        time_str = event.get('event_time', '')
        try:
            dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            date_display = dt.strftime('%Y-%m-%d %H:%M')
        except:
            date_display = time_str

        title = event.get('title', '')
        desc = event.get('description', '')
        is_key = event.get('is_key_event', False)

        # 关键事件加粗
        if is_key:
            lines.append(f"### 🔑 {date_display} — {title}\n")
        else:
            lines.append(f"### {date_display} — {title}\n")

        if desc:
            lines.append(f"{desc}\n")

        # 来源链接
        source_url = event.get('source_url', '')
        source_platform = event.get('source_platform', '')
        if source_url:
            lines.append(f"*来源: [{source_platform or '原文'}]({source_url})*\n")

        lines.append("\n")

    # 页脚
    lines.append("---\n")
    lines.append(f"*生成时间: {timeline.get('created_at', '')}*\n")
    lines.append(f"*最后更新: {timeline.get('updated_at', '')}*\n")

    return '\n'.join(lines)