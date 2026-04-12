#!/usr/bin/env python3
"""
热点分类模块 — 将热点归入六大分类

职责：
  1. 加载分类规则配置
  2. 根据规则将热点归入对应分类
  3. 处理特殊规则（foreign_china 的媒体过滤）

设计原则：
  - 纯本地逻辑，确定性映射
  - 支持多分类（一个热点可归入多个分类）
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import yaml

BASE = Path(__file__).parent.parent

# ═══════════════════════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════════════════════

_config_cache = None


def load_category_rules() -> dict:
    """加载分类规则配置"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_path = BASE / "config" / "category_rules.yaml"
    if not config_path.exists():
        print(f"[WARN] 配置文件不存在: {config_path}", file=sys.stderr)
        return {}

    try:
        _config_cache = yaml.safe_load(config_path.read_text("utf-8")) or {}
        return _config_cache
    except Exception as e:
        print(f"[WARN] 加载配置失败: {e}", file=sys.stderr)
        return {}


def get_chinese_domestic_media() -> set:
    """获取中国本土媒体列表"""
    cfg = load_category_rules()
    media_list = cfg.get("chinese_domestic_media", [])
    return set(m.lower() for m in media_list)


def get_category_definitions() -> dict:
    """获取分类定义"""
    cfg = load_category_rules()
    return cfg.get("categories", {})


# ═══════════════════════════════════════════════════════════════════════════════
# 分类逻辑
# ═══════════════════════════════════════════════════════════════════════════════

def tier_gte(tier_a: str, tier_b: str) -> bool:
    """
    比较媒体级别：tier_a >= tier_b
    S > A > B
    """
    order = {"S": 3, "A": 2, "B": 1}
    return order.get(tier_a, 0) >= order.get(tier_b, 0)


def classify_hotspot(hotspot: dict, s_tier_sources: set = None) -> list[str]:
    """
    将热点归入分类

    Args:
        hotspot: 热点字典，需包含：
            - primary_country: 主体国家
            - related_countries: 关联国家列表
            - sources: 来源媒体列表
            - s_tier_count: S 级媒体数量
            - entities: 关键实体列表（用于 INTL 类型推断）
        s_tier_sources: S 级媒体集合（用于判断媒体级别）

    Returns:
        匹配的分类 ID 列表（可多归属）
    """
    categories = get_category_definitions()
    if not categories:
        return []

    chinese_media = get_chinese_domestic_media()
    if s_tier_sources is None:
        from event_aggregator import load_s_tier_sources
        s_tier_sources = load_s_tier_sources()

    matched = []

    primary_country = hotspot.get("primary_country", "")
    related_countries = hotspot.get("related_countries", [])
    sources = hotspot.get("sources", [])
    s_tier_count = hotspot.get("s_tier_count", 0)
    entities = hotspot.get("entities", [])

    # 判断媒体级别
    max_tier = "S" if s_tier_count > 0 else "B"

    # ── INTL 特殊处理：根据 entities 和标题推断国家 ──
    inferred_countries = set()
    if primary_country == "INTL":
        # 实体中的国家关键词映射
        entity_country_map = {
            # 美国
            "美国": "US", "美方": "US", "白宫": "US", "特朗普": "US", "拜登": "US",
            "万斯": "US", "美军": "US", "国务院": "US", "五角大楼": "US",
            # 中国
            "中国": "CN", "中方": "CN", "北京": "CN", "习近平": "CN",
            # 日本
            "日本": "JP", "日方": "JP", "东京": "JP", "岸田": "JP",
            # 中东
            "伊朗": "IR", "伊方": "IR", "以色列": "IL", "以方": "IL",
            "巴勒斯坦": "PS", "加沙": "PS", "哈马斯": "PS",
            "沙特": "SA", "土耳其": "TR", "伊拉克": "IQ",
            # 其他
            "俄罗斯": "RU", "俄方": "RU", "普京": "RU",
            "韩国": "KR", "朝鲜": "KP", "印度": "IN",
            "台湾": "TW", "香港": "HK",
        }

        # 从 entities 推断
        for entity in entities:
            entity_lower = entity.lower() if isinstance(entity, str) else str(entity).lower()
            for keyword, country in entity_country_map.items():
                if keyword in entity_lower or entity_lower in keyword:
                    inferred_countries.add(country)
                    break

        # 从标题推断（补充）
        title = hotspot.get("representative_title", "")
        if title:
            for keyword, country in entity_country_map.items():
                if keyword in title:
                    inferred_countries.add(country)

    # 合并推断的国家到待分类列表
    all_countries = {primary_country} | set(related_countries) | inferred_countries

    # ── 国际热点优先匹配 ──
    # INTL 类型且热度较高，优先归入 international 分类
    score = hotspot.get("score", 0)
    if primary_country == "INTL" and score >= 50:
        # 检查是否涉及多个国家
        unique_countries = len(all_countries - {"INTL"})
        if unique_countries >= 2:
            matched.append("international")
            # 注意：不 return，继续匹配其他分类（多归属）

    for category_id, rule in categories.items():
        # 跳过 international，上面已经处理
        if category_id == "international":
            continue
        primary_list = rule.get("primary", [])
        related_trigger = rule.get("related_trigger", [])

        # ── 规则1：primary_country 直接命中 ──
        if primary_country in primary_list:
            # foreign_china 特殊处理：排除国内媒体
            if category_id == "foreign_china":
                has_foreign = any(
                    s.lower() not in chinese_media
                    for s in sources
                )
                if has_foreign:
                    matched.append(category_id)
            else:
                matched.append(category_id)
            continue

        # ── 规则1.5：INTL 类型，使用推断的国家匹配 ──
        # INTL 类型的热点，只要涉及某个分类的国家，就归入该分类（多归属）
        if primary_country == "INTL" and inferred_countries:
            if inferred_countries & set(primary_list):
                if category_id == "foreign_china":
                    has_foreign = any(
                        s.lower() not in chinese_media
                        for s in sources
                    )
                    if has_foreign:
                        matched.append(category_id)
                else:
                    matched.append(category_id)
                continue

        # ── 规则2：related_countries 触发 ──
        if not related_trigger:
            continue

        has_related = any(
            c in related_trigger
            for c in all_countries
        )

        if not has_related:
            continue

        # 检查媒体级别要求
        require_tier = rule.get("related_require_tier", "B")
        tier_ok = tier_gte(max_tier, require_tier)

        # foreign_china related 触发：排除港澳台作为 primary
        if category_id == "foreign_china" and tier_ok:
            exclude_primary = rule.get("related_exclude_primary", [])
            if primary_country not in exclude_primary:
                matched.append(category_id)
        elif tier_ok:
            matched.append(category_id)

    # ── 规则3：关键词强制分类 (置顶词穿透) ──
    title = hotspot.get("representative_title", "").lower()

    # 美媒 -> 美国新闻 (即使标签是 INTL)
    if "美媒" in title and "us_news" not in matched:
        matched.append("us_news")

    # 日媒 -> 日本新闻
    if "日媒" in title and "japan_news" not in matched:
        matched.append("japan_news")

    # 突发 -> 归入 亚洲/综合 (如果还未分类)
    if "突发" in title and not matched:
        matched.append("asia_other")

    return list(set(matched)) # 去重


def classify_all_hotspots(
    hotspots: list,
    s_tier_sources: set = None
) -> dict[str, list]:
    """
    将所有热点按分类分组

    Args:
        hotspots: HotspotEvent 列表
        s_tier_sources: S 级媒体集合

    Returns:
        {category_id: [hotspot_dict, ...]}
    """
    result = defaultdict(list)

    for hotspot in hotspots:
        # 转换为字典（如果是 dataclass）
        if hasattr(hotspot, "to_dict"):
            h_dict = hotspot.to_dict()
            # 保留原始数据用于后续处理
            h_dict["_original"] = hotspot
        else:
            h_dict = hotspot

        categories = classify_hotspot(h_dict, s_tier_sources)

        for cat_id in categories:
            h_dict["matched_categories"] = categories
            result[cat_id].append(h_dict)

    # 每个分类内部按 score 排序
    for cat_id in result:
        result[cat_id].sort(key=lambda h: h.get("score", 0), reverse=True)

    return dict(result)


# ═══════════════════════════════════════════════════════════════════════════════
# 小国回补逻辑
# ═══════════════════════════════════════════════════════════════════════════════

def backfill_categories(
    categorized: dict[str, list],
    all_articles: list[dict],
    target_categories: list[str],
    min_hotspots: int = 5,
    s_tier_sources: set = None
) -> dict[str, list]:
    """
    对热点数不足的分类进行回补

    Args:
        categorized: 已分类的热点
        all_articles: 所有已分析文章
        target_categories: 需要回补的分类列表
        min_hotspots: 最小热点数
        s_tier_sources: S 级媒体集合

    Returns:
        补充后的分类结果
    """
    categories = get_category_definitions()
    if s_tier_sources is None:
        from event_aggregator import load_s_tier_sources
        s_tier_sources = load_s_tier_sources()

    result = dict(categorized)

    for cat_id in target_categories:
        current_count = len(result.get(cat_id, []))

        if current_count >= min_hotspots:
            continue

        # 找到该分类对应的国家列表
        cat_def = categories.get(cat_id, {})
        primary_countries = cat_def.get("primary", [])

        if not primary_countries:
            continue

        # 从文章中找符合条件的
        existing_keys = {
            h.get("event_key")
            for h in result.get(cat_id, [])
        }

        added = 0
        for article in all_articles:
            # 检查国家
            if article.get("primary_country") not in primary_countries:
                continue

            # 检查是否已存在
            event_key = article.get("event_key", "")
            if event_key in existing_keys:
                continue

            # 检查置顶关键词
            title = article.get("title", "")
            # 加载全局置顶关键词
            from pathlib import Path
            import yaml
            BASE = Path(__file__).parent.parent
            config_path = BASE / "config" / "hotspot_schedule.yaml"
            pinning_keywords = []
            if config_path.exists():
                try:
                    cfg = yaml.safe_load(config_path.read_text("utf-8")) or {}
                    pinning_keywords = cfg.get("settings", {}).get("pinning_keywords", [])
                except: pass
            
            has_pinning = any(kw in title for kw in pinning_keywords)
            
            # 定义媒体信息用于后续判断
            media_group = article.get("media_group", "")
            platform = article.get("platform", "Unknown")
            is_s_tier = (media_group in s_tier_sources) or (platform in s_tier_sources)

            hotspot = {
                "event_key": event_key,
                "primary_country": article.get("primary_country", ""),
                "article_count": 1,
                "s_tier_count": 1 if is_s_tier else 0,
                "score": 10 if is_s_tier else 3,
                "representative_title": title,
                "articles": [{
                    "id": article.get("id"),
                    "title": title,
                    "url": article.get("url", ""),
                    "platform": article.get("platform", ""),
                }],
                "sources": [media_group] if media_group else [article.get("platform", "Unknown")],
                "s_tier_sources": [media_group or article.get("platform")] if is_s_tier else [],
                "entities": article.get("entities", []),
                "matched_categories": [cat_id],
                "is_china_related": cat_id == "foreign_china",
            }
            
            if has_pinning:
                hotspot["score"] += 100

            result.setdefault(cat_id, []).append(hotspot)
            existing_keys.add(event_key)
            added += 1

            if len(result[cat_id]) >= min_hotspots:
                break

    # 重新排序
    for cat_id in result:
        result[cat_id].sort(key=lambda h: h.get("score", 0), reverse=True)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def get_category_name(category_id: str) -> str:
    """获取分类名称"""
    categories = get_category_definitions()
    return categories.get(category_id, {}).get("name", category_id)


def get_category_description(category_id: str) -> str:
    """获取分类描述"""
    categories = get_category_definitions()
    return categories.get(category_id, {}).get("description", "")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="热点分类")
    parser.add_argument("--hours", type=int, default=24, help="时间窗口")
    parser.add_argument("--top", type=int, default=10, help="每分类 Top N")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    from event_aggregator import run_aggregation

    # 获取热点
    hotspots = run_aggregation(hours=args.hours, quiet=True)

    # 分类
    categorized = classify_all_hotspots(hotspots)

    # 输出
    if args.json:
        output = {}
        for cat_id, hotspots_list in categorized.items():
            output[cat_id] = {
                "name": get_category_name(cat_id),
                "count": len(hotspots_list),
                "hotspots": hotspots_list[:args.top]
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== 热点分类结果 ===\n")
        for cat_id, hotspots_list in categorized.items():
            print(f"【{get_category_name(cat_id)}】({len(hotspots_list)} 条)")
            for i, h in enumerate(hotspots_list[:args.top], 1):
                print(f"  {i}. [{h.get('score', 0):.0f}] {h.get('representative_title', '')[:50]}")
            print()