#!/usr/bin/env python3
"""
事件聚合模块 — 按 event_key 聚合热点

职责：
  1. event_key 归一化（解决 LLM 输出不一致问题）
  2. 按 event_key 聚合文章
  3. 计算热点评分
  4. 输出热点列表

设计原则：
  - 纯本地逻辑，不调用 LLM
  - 使用确定性算法，结果可复现
"""

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import yaml

TZ_BJ = timezone(timedelta(hours=8))
BASE = Path(__file__).parent.parent

# ═══════════════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HotspotEvent:
    """热点事件"""
    event_key: str
    primary_country: str
    article_count: int = 0
    s_tier_count: int = 0
    score: float = 0.0
    representative_title: str = ""
    articles: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    latest_published: str = ""
    first_published: str = ""

    def to_dict(self) -> dict:
        return {
            "event_key": self.event_key,
            "primary_country": self.primary_country,
            "article_count": self.article_count,
            "s_tier_count": self.s_tier_count,
            "score": self.score,
            "representative_title": self.representative_title,
            "articles": [
                {"id": a.get("id"), "title": a.get("title", ""), "url": a.get("url", ""),
                 "platform": a.get("platform", ""), "published": a.get("published", "")}
                for a in self.articles[:10]  # 只保留前10篇详情
            ],
            "sources": self.sources,
            "entities": self.entities,
            "latest_published": self.latest_published,
            "first_published": self.first_published,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════════════════════

def load_scoring_config() -> dict:
    """加载评分配置"""
    config_path = BASE / "config" / "category_rules.yaml"
    if config_path.exists():
        try:
            cfg = yaml.safe_load(config_path.read_text("utf-8")) or {}
            return cfg.get("scoring", {})
        except Exception:
            pass

    # 默认配置
    return {
        "s_tier_weight": 10,
        "normal_weight": 1,
        "source_weight": 2,
        "min_score": 10,
        "min_articles": 3,
        "s_tier_automatch": True,
        "max_hotspots_per_category": 20,
    }


def load_s_tier_sources() -> set:
    """
    从 feeds.yaml 加载 S 级媒体列表

    Returns:
        media_group 集合
    """
    feeds_path = BASE / "config" / "feeds.yaml"
    if not feeds_path.exists():
        return set()

    try:
        cfg = yaml.safe_load(feeds_path.read_text("utf-8")) or {}
        feeds = cfg.get("feeds", [])

        s_tier_groups = set()
        for feed in feeds:
            if feed.get("is_s_tier"):
                mg = feed.get("media_group", "")
                if mg:
                    s_tier_groups.add(mg)

        return s_tier_groups
    except Exception:
        return set()


# ═══════════════════════════════════════════════════════════════════════════════
# event_key 归一化
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_event_keys(articles: list[dict], threshold: float = 0.75) -> list[dict]:
    """
    对相似的 event_key 进行归一化合并

    Args:
        articles: 文章列表，每项需含 event_key
        threshold: 相似度阈值

    Returns:
        更新后的文章列表
    """
    if not articles:
        return articles

    # 收集所有 event_key
    key_freq = defaultdict(int)
    for a in articles:
        key = a.get("event_key", "")
        if key:
            key_freq[key] += 1

    if not key_freq:
        return articles

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
            if similarity > threshold:
                group.append(key_b)
                merged.add(key_b)

        # 选频率最高的作为规范 key
        canonical = max(group, key=lambda k: key_freq[k])
        for k in group:
            merge_map[k] = canonical

    # 更新所有文章
    for a in articles:
        old_key = a.get("event_key", "")
        if old_key in merge_map:
            a["event_key"] = merge_map[old_key]

    return articles


# ═══════════════════════════════════════════════════════════════════════════════
# 热点聚合与评分
# ═══════════════════════════════════════════════════════════════════════════════

def aggregate_hotspots(
    articles: list[dict],
    scoring_cfg: dict = None,
    s_tier_sources: set = None
) -> list[HotspotEvent]:
    """
    按 event_key 聚合，计算热度分

    Args:
        articles: 文章列表，每项需含 event_key, primary_country, media_group
        scoring_cfg: 评分配置
        s_tier_sources: S 级媒体集合

    Returns:
        热点列表
    """
    if not articles:
        return []

    # 加载配置
    if scoring_cfg is None:
        scoring_cfg = load_scoring_config()
    if s_tier_sources is None:
        s_tier_sources = load_s_tier_sources()

    s_weight = scoring_cfg.get("s_tier_weight", 10)
    n_weight = scoring_cfg.get("normal_weight", 1)
    src_weight = scoring_cfg.get("source_weight", 2)

    # 按 event_key 分组
    groups = defaultdict(list)
    for a in articles:
        key = a.get("event_key", "")
        if key:
            groups[key].append(a)

    hotspots = []

    for event_key, group_articles in groups.items():
        if not group_articles:
            continue

        # 统计各级媒体
        s_count = 0
        n_count = 0
        unique_sources = set()
        country_votes = defaultdict(float)
        all_entities = []
        published_times = []

        for a in group_articles:
            media_group = a.get("media_group", "")
            if media_group in s_tier_sources:
                s_count += 1
            else:
                n_count += 1

            if media_group:
                unique_sources.add(media_group)

            # 国家投票（置信度加权）
            country = a.get("primary_country", "")
            confidence = a.get("llm_confidence", 0.8)
            if country:
                country_votes[country] += confidence

            # 收集实体
            entities = a.get("entities", [])
            if isinstance(entities, str):
                try:
                    entities = json.loads(entities)
                except:
                    entities = []
            if isinstance(entities, list):
                all_entities.extend(entities)

            # 收集时间
            pub = a.get("published", "")
            if pub:
                published_times.append(pub)

        # 计算得分
        score = s_count * s_weight + n_count * n_weight + len(unique_sources) * src_weight

        # 热点判定
        is_hot = (
            s_count >= 1 or  # S 级直接入选
            len(group_articles) >= scoring_cfg.get("min_articles", 3) or
            score >= scoring_cfg.get("min_score", 10)
        )

        if not is_hot:
            continue

        # 确定主体国家（投票）
        primary_country = max(country_votes, key=country_votes.get) if country_votes else "INTL"

        # 选代表性标题（S 级优先）
        rep_article = None
        for a in group_articles:
            if a.get("media_group") in s_tier_sources:
                rep_article = a
                break
        if not rep_article:
            rep_article = group_articles[0]

        # 时间范围
        published_times.sort()
        latest = published_times[-1] if published_times else ""
        first = published_times[0] if published_times else ""

        # 实体去重排序
        from collections import Counter
        entity_counter = Counter(all_entities)
        top_entities = [e for e, _ in entity_counter.most_common(5)]

        hotspot = HotspotEvent(
            event_key=event_key,
            primary_country=primary_country,
            article_count=len(group_articles),
            s_tier_count=s_count,
            score=score,
            representative_title=rep_article.get("title", ""),
            articles=group_articles,
            sources=list(unique_sources),
            entities=top_entities,
            latest_published=latest,
            first_published=first,
        )

        hotspots.append(hotspot)

    # 按得分排序
    hotspots.sort(key=lambda h: h.score, reverse=True)

    return hotspots


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库查询
# ═══════════════════════════════════════════════════════════════════════════════

def get_analyzed_articles(hours: int = 24, conn=None) -> list[dict]:
    """
    获取已分析的文章（过滤非政治情报类）

    Args:
        hours: 时间窗口
        conn: 数据库连接

    Returns:
        文章列表（仅包含 is_political=1 的文章）
    """
    from store import get_conn, init_db

    own_conn = conn is None
    if own_conn:
        conn = get_conn()
        init_db(conn)

    now = datetime.now(TZ_BJ)
    start_time = (now - timedelta(hours=hours)).isoformat()

    rows = conn.execute("""
        SELECT id, url_hash, title, url, summary, platform, media_group,
               published, primary_country, event_key, related_countries,
               entities, llm_confidence, is_political
        FROM articles
        WHERE published >= ?
          AND event_key IS NOT NULL
          AND event_key != ''
          AND (is_political IS NULL OR is_political = 1)
        ORDER BY published DESC
    """, [start_time]).fetchall()

    articles = []
    for r in rows:
        a = dict(r)
        # 解析 JSON 字段
        try:
            a["related_countries"] = json.loads(a.get("related_countries") or "[]")
        except:
            a["related_countries"] = []
        try:
            a["entities"] = json.loads(a.get("entities") or "[]")
        except:
            a["entities"] = []
        articles.append(a)

    if own_conn:
        conn.close()

    return articles


# ═══════════════════════════════════════════════════════════════════════════════
# 完整流程
# ═══════════════════════════════════════════════════════════════════════════════

def run_aggregation(hours: int = 24, conn=None, quiet: bool = False) -> list[HotspotEvent]:
    """
    执行完整的聚合流程（仅聚合政治情报类文章）

    Args:
        hours: 时间窗口
        conn: 数据库连接
        quiet: 静默模式

    Returns:
        热点列表
    """
    if not quiet:
        print(f"[聚合] 获取已分析文章（{hours}h）...")

    articles = get_analyzed_articles(hours=hours, conn=conn)

    if not quiet:
        print(f"[聚合] 找到 {len(articles)} 条政治情报类文章（已过滤软新闻）")

    if not articles:
        return []

    if not quiet:
        print("[聚合] 归一化 event_key...")

    articles = normalize_event_keys(articles)

    if not quiet:
        print("[聚合] 聚合热点...")

    hotspots = aggregate_hotspots(articles)

    if not quiet:
        print(f"[聚合] 得到 {len(hotspots)} 个热点")

    return hotspots


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="事件聚合")
    parser.add_argument("--hours", type=int, default=24, help="时间窗口（小时）")
    parser.add_argument("--top", type=int, default=20, help="显示 Top N")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    hotspots = run_aggregation(hours=args.hours)

    if args.json:
        output = [h.to_dict() for h in hotspots[:args.top]]
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== Top {min(args.top, len(hotspots))} 热点 ===\n")
        for i, h in enumerate(hotspots[:args.top], 1):
            print(f"{i}. [{h.score:.0f}] {h.representative_title[:40]}")
            print(f"   国家: {h.primary_country} | 文章: {h.article_count} | S级: {h.s_tier_count}")
            print(f"   event_key: {h.event_key}")
            print()