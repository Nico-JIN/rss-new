#!/usr/bin/env python3
"""
统一数据接口 CLI — 面向 Agent 和外部应用的标准化 JSON 接口

5 个子命令：
  feed      全量内容获取（RSS + 外部源合并去重）
  search    关键字 + 时间范围搜索
  hotspot   自动热点捕获
  research  深度研究（多源时间线）
  value     价值分析（专家评估）

使用示例：
  python scripts/api_cli.py feed --hours 6
  python scripts/api_cli.py search --keyword "中国" --hours 1
  python scripts/api_cli.py hotspot --hours 24 --max 10
  python scripts/api_cli.py research --keyword "特朗普关税" --hours 72
  python scripts/api_cli.py value --article-id 100
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from store import (
    get_conn, init_db, query_by_time, query_by_keyword,
    count_articles, get_stats, query_by_tag, upsert_external_articles
)
from external_fetcher import UnifiedSearcher
from filter_utils import is_chinese_media, filter_chinese_results

TZ_BJ = timezone(timedelta(hours=8))
INTERNAL_FIELDS = {'url_hash', 'title_hash', 'created_at'}


def _now():
    return datetime.now(TZ_BJ)


def _clean(item: dict, with_content: bool = False) -> dict:
    """清除内部字段 + 空值"""
    res = {}
    for k, v in item.items():
        if k in INTERNAL_FIELDS:
            continue
        if k == 'content' and not with_content:
            continue
        if k == 'llm_tags' and isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                v = []
        # 强制保留摘要和图片字段（即便为空），方便客户端渲染
        if k in ('summary', 'image'):
             res[k] = v or ''
             continue

        if v in ('', None, [], '[]'):
            continue
        res[k] = v
    return res


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.strip().lower().encode()).hexdigest()[:16]


def _translate_ext_results(ext_results: list[dict]):
    """使用 LLM 批量翻译外部结果的标题和摘要"""
    if not ext_results:
        return
    try:
        from llm_tagger import batch_translate_to_chinese, load_llm_config
        cfg = load_llm_config()
        # 提取专用的翻译配置，或者回退到默认配置
        if cfg and 'translation_llm' in cfg:
            trans_cfg = {'llm': cfg['translation_llm']}
        else:
            trans_cfg = cfg
            
        if not trans_cfg or not trans_cfg.get('llm', {}).get('enabled'):
            return
            
        print(f"[INFO] 正在批量翻译 {len(ext_results)} 条外部补充数据...", file=sys.stderr)
        
        titles = [r['title'] for r in ext_results if r.get('title')]
        if titles:
            title_map = batch_translate_to_chinese(titles, trans_cfg)
            for r in ext_results:
                if r.get('title'):
                    r['title'] = title_map.get(r['title'], r['title'])
                    
        summaries = [r['summary'] for r in ext_results if r.get('summary')]
        if summaries:
            summary_map = batch_translate_to_chinese(summaries, trans_cfg)
            for r in ext_results:
                if r.get('summary'):
                    r['summary'] = summary_map.get(r['summary'], r['summary'])
    except Exception as e:
        print(f"[WARN] 外部结果翻译失败: {e}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════
# 接口 1: 全量内容获取
# ═══════════════════════════════════════════════════════════════════

def cmd_feed(args):
    """获取指定时间段的全部内容（RSS + 外部源合并去重）"""
    now = _now()

    if args.start:
        start = args.start
        end = args.end or now.isoformat()
    elif args.hours:
        start = (now - timedelta(hours=args.hours)).isoformat()
        end = now.isoformat()
    else:
        start = (now - timedelta(hours=6)).isoformat()
        end = now.isoformat()

    conn = get_conn()
    init_db(conn)

    # 1. 从本地 DB 获取 RSS 数据
    items = query_by_time(start, end, limit=args.limit or 500, conn=conn)
    conn.close()

    # 2. 从外部源获取全量情报补充 (Google News RSS)
    if not getattr(args, 'no_external', False):
        try:
            searcher = UnifiedSearcher()
            # 这里的 query 可以是空，或者通用的 "news"
            ext_results = searcher.search("", sources=['google_news'], max_results=50)
            
            # --- 新增：强制翻译外部内容 ---
            _translate_ext_results(ext_results)
            # --------------------------
            
            items.extend(ext_results)
            # 入库机制: 存储新抓取的高价值外部数据 (不含中国媒体，filter 会在后面统一做或者提前做)
            valid_ext = [r for r in ext_results if not is_chinese_media(r.get('platform', r.get('source', '')), r.get('url', ''))]
            if valid_ext:
                upsert_external_articles(valid_ext, keyword_match='feed_supplement')
        except Exception as e:
            print(f"[WARN] 外部源获取失败: {e}", file=sys.stderr)

    # 3. URL 哈希去重 + 严格过滤中国媒体
    seen = set()
    unique = []
    for item in items:
        # 统一格式处理
        platform = item.get('platform', item.get('source', ''))
        url = item.get('url', '')
        
        # 强制过滤中国媒体
        if is_chinese_media(platform, url):
            continue
            
        uh = _url_hash(url)
        if uh not in seen:
            seen.add(uh)
            unique.append(_clean(item, args.with_content))

    output = {
        'api': 'feed',
        'query': {
            'start': start,
            'end': end,
            'generated_at': now.isoformat(),
        },
        'count': len(unique),
        'items': unique
    }

    _output_json(output)


# ═══════════════════════════════════════════════════════════════════
# 接口 2: 关键字 + 时间搜索
# ═══════════════════════════════════════════════════════════════════

def cmd_search(args):
    """按关键字 + 时间范围搜索"""
    now = _now()

    if not args.keyword:
        _error("必须提供 --keyword 参数")
        return

    if args.start:
        start = args.start
        end = args.end or now.isoformat()
    elif args.hours:
        start = (now - timedelta(hours=args.hours)).isoformat()
        end = now.isoformat()
    else:
        start = None
        end = None

    conn = get_conn()
    init_db(conn)

    items = query_by_keyword(
        keyword=args.keyword,
        start=start, end=end,
        media_group=args.media or None,
        country=args.country or None,
        limit=args.limit or 200,
        conn=conn
    )
    conn.close()

    # 2. 调用外部搜索引擎 (Google News, Twitter, Tavily 等)
    if not getattr(args, 'no_external', False):
        try:
            searcher = UnifiedSearcher()
            # 默认源：Google News, Brave, Twitter (视 Key 决定)
            sources = ['google_news', 'brave', 'twitter', 'tavily']
            ext_results = searcher.search(args.keyword, sources=sources, max_results=30)
            
            # --- 新增：强制翻译外部内容 ---
            _translate_ext_results(ext_results)
            # --------------------------
            
            items.extend(ext_results)
            # 入库机制: 存储新抓取的外部条目
            valid_ext = [r for r in ext_results if not is_chinese_media(r.get('platform', r.get('source', '')), r.get('url', ''))]
            if valid_ext:
                upsert_external_articles(valid_ext, keyword_match=args.keyword)
        except Exception as e:
            print(f"[WARN] 外部搜索失败: {e}", file=sys.stderr)

    # 3. 合并、去重且严格过滤中国媒体
    seen = set()
    unique = []
    for item in items:
        # 统一格式处理
        platform = item.get('platform', item.get('source', ''))
        url = item.get('url', '')
        
        # 强制过滤中国媒体
        if is_chinese_media(platform, url):
            continue

        uh = _url_hash(url)
        if uh not in seen:
            seen.add(uh)
            unique.append(_clean(item, args.with_content))

    output = {
        'api': 'search',
        'query': {
            'keyword': args.keyword,
            'start': start,
            'end': end,
            'media': args.media,
            'country': args.country,
            'generated_at': now.isoformat(),
        },
        'count': len(unique),
        'items': unique
    }

    _output_json(output)


# ═══════════════════════════════════════════════════════════════════
# 接口 3: 热点捕获
# ═══════════════════════════════════════════════════════════════════

def cmd_hotspot(args):
    """自动热点事件检测"""
    from hotspot_detector import detect_hot_events

    hours = args.hours or 24
    max_results = args.max or 15

    events = detect_hot_events(
        hours=hours,
        start_time=args.start or None,
        end_time=args.end or None,
        max_results=max_results,
        keyword=getattr(args, 'keyword', None)
    )

    now = _now()
    output = {
        'api': 'hotspot',
        'query': {
            'hours': hours,
            'max_results': max_results,
            'start': args.start,
            'end': args.end,
            'generated_at': now.isoformat(),
        },
        'count': len(events),
        'events': []
    }

    for rank, event in enumerate(events, 1):
        e = {
            'rank': rank,
            'title': event.get('title', ''),
            'score': event.get('score', 0),
            'media_count': len(event.get('platforms', [])),
            'article_count': event.get('count', 0),
            'platforms': event.get('platforms', []),
            'tags': event.get('tags', []),
            'is_china_related': event.get('is_china_related', False),
            'articles': [
                {
                    'id': a.get('id'),
                    'title': a.get('title', ''),
                    'url': a.get('url', ''),
                    'platform': a.get('platform', ''),
                    'published': a.get('published', ''),
                    'summary': a.get('summary', ''),
                    'image': a.get('image', ''),
                }
                for a in event.get('items', [])
            ]
        }
        output['events'].append(e)

    _output_json(output)


# ═══════════════════════════════════════════════════════════════════
# 接口 4: 深度研究（多源时间线）
# ═══════════════════════════════════════════════════════════════════

def cmd_research(args):
    """深度研究：多源汇聚 + 时间线生成"""
    from timeline import (
        generate_timeline, search_related_in_rss,
        extract_timeline_keywords, enrich_article_content,
        generate_local_timeline, call_llm_for_timeline
    )
    from store import get_articles_by_ids

    now = _now()

    # 来源 A: 通过文章 ID
    if args.article_ids:
        ids = [int(x.strip()) for x in args.article_ids.split(',') if x.strip().isdigit()]
        if ids:
            result = generate_timeline(ids, search_days=args.days or 30)
            output = {
                'api': 'research',
                'query': {
                    'article_ids': ids,
                    'generated_at': now.isoformat(),
                },
                'timeline': result
            }
            _output_json(output)
            return

    # 来源 B: 通过关键字搜索
    if args.keyword:
        hours = args.hours or 72
        start = (now - timedelta(hours=hours)).isoformat()
        end = now.isoformat()

        conn = get_conn()
        init_db(conn)

        # 搜索数据库中的相关文章
        articles = query_by_keyword(
            keyword=args.keyword,
            start=start, end=end,
            limit=50, conn=conn
        )

        if not articles:
            conn.close()
            _output_json({
                'api': 'research',
                'query': {'keyword': args.keyword, 'hours': hours},
                'error': f'未找到与 "{args.keyword}" 相关的文章'
            })
            return

        # 提取关键词 → 搜索历史扩展
        keywords = extract_timeline_keywords(articles)

        related = search_related_in_rss(
            keywords,
            time_range_days=args.days or 30,
            exclude_ids=[a['id'] for a in articles if a.get('id')],
            conn=conn
        )
        conn.close()

        all_articles = articles + related

        # 获取全文
        all_articles = enrich_article_content(all_articles)

        # 生成时间线
        timeline_data = call_llm_for_timeline(all_articles)

        # 附加源文章信息
        timeline_data['source_articles'] = [
            {
                'id': a.get('id'),
                'title': a.get('title', ''),
                'url': a.get('url', ''),
                'platform': a.get('platform', ''),
                'published': a.get('published', ''),
                'summary': a.get('summary', ''),
            }
            for a in all_articles[:30]  # 限制返回量
        ]

        output = {
            'api': 'research',
            'query': {
                'keyword': args.keyword,
                'hours': hours,
                'articles_found': len(articles),
                'related_found': len(related),
                'generated_at': now.isoformat(),
            },
            'timeline': timeline_data
        }
        _output_json(output)
        return

    _error("必须提供 --keyword 或 --article-ids 参数")


# ═══════════════════════════════════════════════════════════════════
# 接口 5: 价值分析
# ═══════════════════════════════════════════════════════════════════

def cmd_value(args):
    """文章价值分析 — 专家角度评估"""
    from store import get_articles_by_ids
    from llm_tagger import _call_deepseek, load_llm_config

    now = _now()

    # 解析文章 ID
    ids = []
    if args.article_id:
        ids = [int(args.article_id)]
    elif args.article_ids:
        ids = [int(x.strip()) for x in args.article_ids.split(',') if x.strip().isdigit()]

    if not ids:
        _error("必须提供 --article-id 或 --article-ids 参数")
        return

    conn = get_conn()
    init_db(conn)
    articles = get_articles_by_ids(ids, conn)
    conn.close()

    if not articles:
        _output_json({
            'api': 'value',
            'error': '未找到指定的文章',
            'query': {'article_ids': ids}
        })
        return

    # 构造评估 Prompt
    cfg = load_llm_config()
    results = []

    for art in articles:
        body = art.get('content') or art.get('summary') or art.get('title', '')
        if len(body) > 2000:
            body = body[:2000] + '...'

        prompt = f"""你是一位资深的国际关系研究员和媒体分析专家，拥有丰富的学术发表经验。
请从专家角度评估以下新闻文章的价值。

## 文章信息
- 标题：{art.get('title', '')}
- 来源：{art.get('platform', '')}
- 时间：{art.get('published', '')}
- 内容：{body}

## 评估维度（每项 1-10 分）

1. **新闻价值** (news_value)：时效性、重要性、影响范围、独家性
2. **研究价值** (research_value)：是否值得深入研究、学术引用潜力、数据支撑
3. **发表价值** (publication_value)：是否适合作为刊物素材、语言质量、结构完整性
4. **政策价值** (policy_value)：对决策的参考价值、政策敏感度、可操作性

## 输出格式（严格 JSON）

{{
  "scores": {{
    "news_value": 8,
    "research_value": 7,
    "publication_value": 6,
    "policy_value": 9,
    "overall": 7.5
  }},
  "assessment": "200字以内的综合评估文字",
  "recommended_outlets": ["推荐发表的刊物/平台1", "刊物2"],
  "research_angles": ["可深入研究的角度1", "角度2", "角度3"],
  "key_findings": ["核心发现1", "核心发现2"]
}}

只输出 JSON，不要有其他文字。"""

        messages = [
            {"role": "system", "content": "你是一位资深的国际关系研究员和媒体分析专家。请严格按照 JSON 格式输出评估结果。"},
            {"role": "user", "content": prompt}
        ]

        assessment = None
        if cfg and cfg.get('llm', {}).get('enabled'):
            try:
                raw = _call_deepseek(messages, cfg)
                if raw:
                    cleaned = re.sub(r'```json\n?|\n?```', '', raw).strip()
                    assessment = json.loads(cleaned)
            except Exception as e:
                print(f"[WARN] LLM 价值分析失败: {e}", file=sys.stderr)

        # 降级：本地算法打分
        if not assessment:
            assessment = _local_value_assessment(art)

        result = {
            'article': {
                'id': art.get('id'),
                'title': art.get('title', ''),
                'url': art.get('url', ''),
                'platform': art.get('platform', ''),
                'published': art.get('published', ''),
                'summary': art.get('summary', ''),
            },
            **assessment
        }
        results.append(result)

    output = {
        'api': 'value',
        'query': {
            'article_ids': ids,
            'generated_at': now.isoformat(),
        },
        'count': len(results),
        'assessments': results
    }

    _output_json(output)


def _local_value_assessment(art: dict) -> dict:
    """本地降级的价值评估（当 LLM 不可用时）"""
    title = art.get('title', '')
    content = art.get('content', '') or art.get('summary', '') or ''
    platform = art.get('platform', '').lower()

    # 简单启发式评分
    news_score = 5
    research_score = 4
    pub_score = 4
    policy_score = 4

    # 时效性加分
    try:
        pub_time = datetime.fromisoformat(art.get('published', '').replace('Z', '+00:00'))
        hours_old = (_now() - pub_time).total_seconds() / 3600
        if hours_old < 6:
            news_score += 2
        elif hours_old < 24:
            news_score += 1
    except Exception:
        pass

    # 权威来源加分
    tier1 = ['路透社', 'reuters', '纽约时报', 'cnn', 'bbc', '华尔街日报']
    tier2 = ['半岛电视台', 'nhk', '联合早报', '南华早报']
    if any(t in platform for t in tier1):
        news_score += 2
        research_score += 1
    elif any(t in platform for t in tier2):
        news_score += 1

    # 内容丰富度
    if len(content) > 500:
        research_score += 1
        pub_score += 1
    if len(content) > 1500:
        research_score += 1

    # 中国/政策相关
    china_kw = ['中国', '北京', '外交部', '贸易战', '关税', '台湾', '南海']
    if any(kw in title + content for kw in china_kw):
        policy_score += 2

    overall = round((news_score + research_score + pub_score + policy_score) / 4, 1)

    return {
        'scores': {
            'news_value': min(news_score, 10),
            'research_value': min(research_score, 10),
            'publication_value': min(pub_score, 10),
            'policy_value': min(policy_score, 10),
            'overall': min(overall, 10.0),
        },
        'assessment': f'[本地算法评估] 该文章来自{art.get("platform", "未知来源")}，'
                       f'内容长度{len(content)}字。基于来源权威度、内容丰富度和时效性的启发式评分。'
                       f'建议启用 LLM 获取更精准的专家评估。',
        'recommended_outlets': [],
        'research_angles': [],
        'key_findings': [],
    }


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def _output_json(data: dict):
    """输出 JSON 到 stdout"""
    sys.stdout.buffer.write(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
    print()


def _error(msg: str):
    """输出错误 JSON"""
    _output_json({'error': msg})
    sys.exit(1)


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='RSS 新闻统一数据接口 CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令示例:
  python scripts/api_cli.py feed --hours 6
  python scripts/api_cli.py search --keyword "中国" --hours 1
  python scripts/api_cli.py hotspot --hours 24 --max 10
  python scripts/api_cli.py research --keyword "特朗普" --hours 72
  python scripts/api_cli.py value --article-id 100
        """
    )

    sub = parser.add_subparsers(dest='command', help='子命令')

    # feed
    p_feed = sub.add_parser('feed', help='全量内容获取')
    p_feed.add_argument('--hours', type=float, help='最近 N 小时')
    p_feed.add_argument('--start', type=str, help='起始时间 (ISO)')
    p_feed.add_argument('--end', type=str, help='结束时间 (ISO)')
    p_feed.add_argument('--limit', type=int, default=500, help='最大条数')
    p_feed.add_argument('--with-content', action='store_true', help='包含全文')

    # search
    p_search = sub.add_parser('search', help='关键字+时间搜索')
    p_search.add_argument('--keyword', type=str, required=True, help='搜索关键字')
    p_search.add_argument('--hours', type=float, help='最近 N 小时')
    p_search.add_argument('--start', type=str, help='起始时间 (ISO)')
    p_search.add_argument('--end', type=str, help='结束时间 (ISO)')
    p_search.add_argument('--media', type=str, help='媒体组过滤')
    p_search.add_argument('--country', type=str, help='国家过滤')
    p_search.add_argument('--limit', type=int, default=200, help='最大条数')
    p_search.add_argument('--with-content', action='store_true', help='包含全文')

    # hotspot
    p_hotspot = sub.add_parser('hotspot', help='热点捕获')
    p_hotspot.add_argument('--hours', type=float, default=24, help='时间窗口 (小时)')
    p_hotspot.add_argument('--start', type=str, help='起始时间 (ISO)')
    p_hotspot.add_argument('--end', type=str, help='结束时间 (ISO)')
    p_hotspot.add_argument('--max', type=int, default=15, help='最大热点数')
    p_hotspot.add_argument('--keyword', type=str, help='热点类别关键字 (如: 中国、国际、周边)')

    # Global flags
    parser.add_argument('--no-external', action='store_true', help='禁用外部搜索源 (仅搜索本地DB)')

    # research
    p_research = sub.add_parser('research', help='深度研究')
    p_research.add_argument('--keyword', type=str, help='研究主题/关键字')
    p_research.add_argument('--article-ids', type=str, help='文章 ID 列表 (逗号分隔)')
    p_research.add_argument('--hours', type=float, default=72, help='搜索时间窗口 (小时)')
    p_research.add_argument('--days', type=int, default=30, help='历史搜索天数')

    # value
    p_value = sub.add_parser('value', help='价值分析')
    p_value.add_argument('--article-id', type=str, help='单篇文章 ID')
    p_value.add_argument('--article-ids', type=str, help='文章 ID 列表 (逗号分隔)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        'feed': cmd_feed,
        'search': cmd_search,
        'hotspot': cmd_hotspot,
        'research': cmd_research,
        'value': cmd_value,
    }

    commands[args.command](args)


if __name__ == '__main__':
    main()
