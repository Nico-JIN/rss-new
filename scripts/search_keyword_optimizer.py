#!/usr/bin/env python3
"""
智能搜索关键字处理器

根据搜索引擎类型和时间窗口，智能生成优化的搜索关键字。

使用方式:
    from search_keyword_optimizer import optimize_search_query

    result = optimize_search_query(
        keyword="中国",
        hours=1,
        engine="google_news",
        use_llm=True
    )
    # result = {
    #     "optimized_keyword": "China news past hour latest",
    #     "time_filter": {"from": "...", "to": "..."},
    #     "expanded_keywords": ["China", "Chinese", ...]
    # }
"""

import re
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Literal

TZ_BJ = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════════════════
# 时间描述生成
# ═══════════════════════════════════════════════════════════════════

def get_time_description(hours: int, language: str = "en") -> Dict:
    """
    根据时间窗口生成时间描述

    Args:
        hours: 时间窗口（小时）
        language: 语言 (en/zh)

    Returns:
        {
            "short": "1h",  # 简短格式
            "natural": "past hour",  # 自然语言
            "search_terms": ["latest", "recent"],  # 搜索词
            "iso_range": {"from": "...", "to": "..."}  # ISO时间范围
        }
    """
    now = datetime.now(TZ_BJ)
    start = now - timedelta(hours=hours)

    # 时间描述映射
    if language == "zh":
        if hours <= 1:
            natural = "近1小时"
            search_terms = ["最新", "刚刚", "突发"]
        elif hours <= 3:
            natural = f"近{hours}小时"
            search_terms = ["最新", "今日"]
        elif hours <= 6:
            natural = f"近{hours}小时"
            search_terms = ["今日", "最新"]
        elif hours <= 24:
            natural = "今日" if hours == 24 else f"近{hours}小时"
            search_terms = ["今日", "最新"]
        elif hours <= 72:
            natural = f"近{hours//24}天"
            search_terms = ["近日", "最新"]
        elif hours <= 168:
            natural = "本周"
            search_terms = ["本周", "近期"]
        else:
            natural = f"近{hours//24}天"
            search_terms = ["近期"]
    else:
        # English
        if hours <= 1:
            natural = "past hour"
            search_terms = ["latest", "recent", "breaking"]
        elif hours <= 3:
            natural = f"past {hours} hours"
            search_terms = ["latest", "today"]
        elif hours <= 6:
            natural = f"past {hours} hours"
            search_terms = ["today", "latest"]
        elif hours <= 24:
            natural = "past 24 hours" if hours == 24 else f"past {hours} hours"
            search_terms = ["today", "latest"]
        elif hours <= 72:
            days = hours // 24
            natural = f"past {days} days"
            search_terms = ["recent", "latest"]
        elif hours <= 168:
            natural = "past week"
            search_terms = ["this week", "recent"]
        else:
            days = hours // 24
            natural = f"past {days} days"
            search_terms = ["recent"]

    return {
        "short": f"{hours}h",
        "natural": natural,
        "search_terms": search_terms,
        "iso_range": {
            "from": start.isoformat(),
            "to": now.isoformat()
        }
    }


# ═══════════════════════════════════════════════════════════════════
# 关键字优化（无LLM版本）
# ═══════════════════════════════════════════════════════════════════

def is_chinese_keyword(keyword: str) -> bool:
    """检测关键词是否包含中文字符"""
    for char in keyword:
        if '\u4e00' <= char <= '\u9fff':
            return True
    return False


def optimize_keyword_simple(keyword: str, hours: int,
                            engine: str) -> Dict:
    """
    简单的关键字优化（不使用LLM）

    根据引擎类型添加时间描述和相关词
    """
    time_desc = get_time_description(hours, language="en")
    time_desc_zh = get_time_description(hours, language="zh")

    # 引擎类型分类
    engine_type = get_engine_type(engine)

    # 检测是否为中文关键词
    is_chinese = is_chinese_keyword(keyword)

    result = {
        "original_keyword": keyword,
        "optimized_keyword": keyword,
        "time_filter": time_desc["iso_range"],
        "expanded_keywords": [keyword],
        "engine_type": engine_type
    }

    if engine_type == "news_aggregator":
        # NewsAPI 等：使用 API 时间参数
        result["optimized_keyword"] = keyword
        result["use_time_param"] = True

    elif engine_type == "ai_search":
        # AI 搜索引擎：使用自然语言描述
        result["optimized_keyword"] = f"{keyword} {time_desc_zh['natural']} 最新新闻动态"
        result["use_time_param"] = False

    else:
        # Google/Bing 等：关键字加时间词
        if is_chinese:
            # 中文关键词：添加中文时间词
            result["optimized_keyword"] = f"{keyword} {time_desc_zh['natural']}"
        else:
            # 英文关键词：添加英文时间词
            time_terms = " ".join(time_desc["search_terms"][:2])
            result["optimized_keyword"] = f"{keyword} {time_desc['natural']} {time_terms}"
        result["use_time_param"] = False

    return result


def get_engine_type(engine: str) -> str:
    """获取引擎类型"""
    engine_lower = engine.lower()

    # 新闻聚合器（支持API时间参数）
    news_aggregators = ['newsapi', 'news_api']
    if engine_lower in news_aggregators:
        return "news_aggregator"

    # AI搜索引擎
    ai_engines = ['tavily', 'perplexity', 'perplexity_ai']
    if engine_lower in ai_engines:
        return "ai_search"

    # 社交媒体
    social = ['twitter', 'reddit', 'youtube']
    if engine_lower in social:
        return "social_media"

    # 默认：传统搜索引擎
    return "search_engine"


# ═══════════════════════════════════════════════════════════════════
# 关键字扩充（使用LLM）
# ═══════════════════════════════════════════════════════════════════

def expand_keywords_with_llm(keyword: str, hours: int,
                              max_expansions: int = 5) -> List[str]:
    """
    使用 LLM 扩充搜索关键词

    Args:
        keyword: 原始关键词
        hours: 时间窗口
        max_expansions: 最大扩充数量

    Returns:
        扩充后的关键词列表
    """
    try:
        from local_llm_client import get_local_llm
        client = get_local_llm()

        if not client.is_available():
            return [keyword]

        time_desc = get_time_description(hours, language="zh")

        prompt = f"""
用户要搜索新闻，主题是："{keyword}"
时间范围：{time_desc['natural']}

请生成{max_expansions}个相关的搜索关键词，用于扩大搜索覆盖面。

要求：
1. 包含原词的中文和英文版本
2. 包含同义词、相关人物/机构、相关事件
3. 每个关键词一行，不要序号
4. 不要解释，只输出关键词

示例输入：中国
示例输出：
China
Chinese
Beijing
Xi Jinping
中国最新动态
"""

        result = client.generate(prompt)
        if 'response' in result:
            lines = [line.strip() for line in result['response'].strip().split('\n') if line.strip()]
            # 清理序号
            keywords = []
            for line in lines:
                line = re.sub(r'^\d+[\.、\s:]+', '', line)
                line = re.sub(r'^[\-\*]+\s*', '', line)
                if line and len(line) < 50:  # 过滤过长的
                    keywords.append(line)
            return keywords[:max_expansions]

    except Exception as e:
        print(f"[WARN] LLM 关键词扩充失败: {e}")

    return [keyword]


def optimize_keyword_with_llm(keyword: str, hours: int,
                               engine: str,
                               expand: bool = True) -> Dict:
    """
    使用 LLM 智能优化搜索关键字

    Args:
        keyword: 原始关键词
        hours: 时间窗口
        engine: 搜索引擎
        expand: 是否扩充关键词

    Returns:
        优化结果
    """
    try:
        from local_llm_client import get_local_llm
        client = get_local_llm()

        if not client.is_available():
            return optimize_keyword_simple(keyword, hours, engine)

        engine_type = get_engine_type(engine)
        time_desc = get_time_description(hours, language="en")
        time_desc_zh = get_time_description(hours, language="zh")

        # 根据引擎类型生成不同的提示
        if engine_type == "ai_search":
            prompt = f"""
用户要搜索新闻，请生成优化的搜索描述。

主题：{keyword}
时间范围：{time_desc_zh['natural']}
目标引擎：AI搜索引擎（支持自然语言）

要求：
1. 生成一段自然语言搜索描述，包含主题和时间
2. 语言简洁，适合AI搜索引擎理解
3. 不要输出解释，只输出搜索描述

输出格式：
{{"search_query": "搜索描述"}}
"""
        elif engine_type == "news_aggregator":
            # NewsAPI 使用原关键字 + API 时间参数
            return optimize_keyword_simple(keyword, hours, engine)
        else:
            prompt = f"""
用户要搜索新闻，请生成优化的搜索关键词。

主题：{keyword}
时间范围：{time_desc['natural']}
目标引擎：{engine}

要求：
1. 生成适合搜索引擎的关键词组合
2. 包含时间相关的词（如 latest, recent, today）
3. 同时包含英文和中文版本
4. 不要输出解释，只输出关键词

输出格式：
{{"search_query": "优化后的搜索关键词", "alternatives": ["备选1", "备选2"]}}
"""

        result = client.generate(prompt)
        if 'response' in result:
            try:
                # 清理 markdown 标记
                response_text = result['response'].strip()
                response_text = re.sub(r'^```json\s*', '', response_text)
                response_text = re.sub(r'^```\s*', '', response_text)
                response_text = re.sub(r'\s*```$', '', response_text)
                parsed = json.loads(response_text)

                optimized = parsed.get('search_query', keyword)
                alternatives = parsed.get('alternatives', [])

                # 扩充关键词
                expanded = [keyword, optimized] + alternatives
                if expand:
                    llm_expansions = expand_keywords_with_llm(keyword, hours)
                    expanded.extend(llm_expansions)

                return {
                    "original_keyword": keyword,
                    "optimized_keyword": optimized,
                    "time_filter": time_desc["iso_range"],
                    "expanded_keywords": list(dict.fromkeys(expanded)),  # 去重保持顺序
                    "engine_type": engine_type,
                    "use_time_param": engine_type == "news_aggregator"
                }

            except json.JSONDecodeError:
                # JSON 解析失败，尝试直接提取
                optimized = result['response'].strip().split('\n')[0]
                return {
                    "original_keyword": keyword,
                    "optimized_keyword": optimized,
                    "time_filter": time_desc["iso_range"],
                    "expanded_keywords": [keyword, optimized],
                    "engine_type": engine_type
                }

    except Exception as e:
        print(f"[WARN] LLM 关键词优化失败: {e}")

    return optimize_keyword_simple(keyword, hours, engine)


# ═══════════════════════════════════════════════════════════════════
# 主入口函数
# ═══════════════════════════════════════════════════════════════════

def optimize_search_query(keyword: str,
                          hours: int = 24,
                          engine: str = "google_news",
                          use_llm: bool = True,
                          expand: bool = True) -> Dict:
    """
    智能优化搜索关键字

    Args:
        keyword: 原始关键词
        hours: 时间窗口（小时）
        engine: 搜索引擎
        use_llm: 是否使用 LLM 优化
        expand: 是否扩充关键词

    Returns:
        {
            "original_keyword": "中国",
            "optimized_keyword": "China past hour latest recent",
            "time_filter": {"from": "...", "to": "..."},
            "expanded_keywords": ["中国", "China", ...],
            "engine_type": "search_engine",
            "use_time_param": false
        }
    """
    if use_llm:
        return optimize_keyword_with_llm(keyword, hours, engine, expand)
    else:
        return optimize_keyword_simple(keyword, hours, engine)


# ═══════════════════════════════════════════════════════════════════
# CLI 测试
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='智能搜索关键字优化')
    parser.add_argument('--keyword', type=str, required=True, help='原始关键词')
    parser.add_argument('--hours', type=int, default=24, help='时间窗口（小时）')
    parser.add_argument('--engine', type=str, default='google_news',
                        choices=['google_news', 'bing_news', 'newsapi', 'tavily', 'perplexity'],
                        help='搜索引擎')
    parser.add_argument('--no-llm', action='store_true', help='不使用LLM')
    parser.add_argument('--json', action='store_true', help='JSON输出')

    args = parser.parse_args()

    result = optimize_search_query(
        keyword=args.keyword,
        hours=args.hours,
        engine=args.engine,
        use_llm=not args.no_llm
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n原始关键词: {result['original_keyword']}")
        print(f"优化后: {result['optimized_keyword']}")
        print(f"引擎类型: {result['engine_type']}")
        print(f"使用时间参数: {result.get('use_time_param', False)}")
        print(f"扩充关键词: {result['expanded_keywords'][:5]}")
        print(f"时间范围: {result['time_filter']['from'][:16]} ~ {result['time_filter']['to'][:16]}")