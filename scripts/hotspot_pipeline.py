#!/usr/bin/env python3
"""
热点提取主流水线

流程：
  1. 拉取未分析文章
  2. LLM 批量提取（event_key 等）
  3. event_key 归一化 + 聚合
  4. 六大分类映射
  5. 写入数据库

使用方式：
    python scripts/hotspot_pipeline.py --hours 24
    python scripts/hotspot_pipeline.py --run-all
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from event_extractor import (
    get_unanalyzed_articles,
    extract_all_sync,
    save_extraction_results,
    load_llm_config,
)
from event_aggregator import (
    run_aggregation,
    load_s_tier_sources,
    load_scoring_config,
)
from category_classifier import (
    classify_all_hotspots,
    backfill_categories,
    get_category_name,
)
from store import get_conn, init_db

TZ_BJ = timezone(timedelta(hours=8))
BASE = Path(__file__).parent.parent

# ═══════════════════════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════════════════════

def load_pipeline_config() -> dict:
    """加载流水线配置"""
    config_path = BASE / "config" / "hotspot_schedule.yaml"
    if config_path.exists():
        try:
            return yaml.safe_load(config_path.read_text("utf-8")) or {}
        except:
            pass

    return {
        "settings": {
            "default_hours": 24,
            "default_max_results": 20,
            "narrative_provider": "volcengine",
        }
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 核心流程
# ═══════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    hours: int = 24,
    provider: str = None,
    max_results: int = 20,
    min_hotspots: int = 5,
    dry_run: bool = False,
    quiet: bool = False
) -> dict:
    """
    执行完整的热点提取流程

    Args:
        hours: 时间窗口
        provider: LLM 提供商
        max_results: 每分类最大结果数
        min_hotspots: 最小热点数（用于回补）
        dry_run: 只预览不保存
        quiet: 静默模式

    Returns:
        {
            "categories": {category_id: [hotspot, ...]},
            "stats": {...}
        }
    """
    start_time = datetime.now(TZ_BJ)

    # 加载配置
    llm_cfg = load_llm_config()
    pipeline_cfg = load_pipeline_config()
    scoring_cfg = load_scoring_config()
    s_tier_sources = load_s_tier_sources()

    if provider is None:
        provider = pipeline_cfg.get("settings", {}).get("narrative_provider", "volcengine")

    # ── Step 1: 获取未分析文章 ────────────────────────────────────
    if not quiet:
        print(f"\n{'='*60}", flush=True)
        print(f"[流水线] 热点提取开始", flush=True)
        print(f"  时间窗口: {hours}h", flush=True)
        print(f"  LLM Provider: {provider}", flush=True)
        print(f"{'='*60}\n", flush=True)

    conn = get_conn()
    init_db(conn)

    unanalyzed = get_unanalyzed_articles(hours=hours, limit=2000, conn=conn)

    if not quiet:
        print(f"[Step 1] 未分析文章: {len(unanalyzed)} 条", flush=True)

    # ── Step 2: LLM 提取 ──────────────────────────────────────────
    extracted_count = 0
    if unanalyzed:
        if not quiet:
            print(f"[Step 2] LLM 结构化提取...", flush=True)

        results = extract_all_sync(
            unanalyzed,
            llm_cfg,
            provider=provider,
            quiet=quiet
        )

        if results and not dry_run:
            extracted_count = save_extraction_results(results, conn)
            if not quiet:
                print(f"[Step 2] 已保存 {extracted_count} 条", flush=True)
    else:
        if not quiet:
            print("[Step 2] 无待提取文章，跳过", flush=True)

    # ── Step 3: 聚合热点 ──────────────────────────────────────────
    if not quiet:
        print(f"[Step 3] 聚合热点...", flush=True)

    hotspots = run_aggregation(hours=hours, conn=conn, quiet=quiet)

    if not quiet:
        print(f"[Step 3] 得到 {len(hotspots)} 个热点", flush=True)

    # ── Step 4: 分类映射 ──────────────────────────────────────────
    if not quiet:
        print(f"[Step 4] 六大分类映射...", flush=True)

    categorized = classify_all_hotspots(hotspots, s_tier_sources)

    # ── Step 5: 小国回补 ──────────────────────────────────────────
    target_categories = [
        "foreign_china",
        "us_news",
        "japan_news",
        "greater_china",
        "middle_east",
        "asia_other",
    ]

    # 获取所有已分析文章用于回补
    from event_aggregator import get_analyzed_articles
    all_analyzed = get_analyzed_articles(hours=hours, conn=conn)

    categorized = backfill_categories(
        categorized,
        all_analyzed,
        target_categories,
        min_hotspots=min_hotspots,
        s_tier_sources=s_tier_sources
    )

    if not quiet:
        for cat_id, h_list in categorized.items():
            print(f"  {get_category_name(cat_id)}: {len(h_list)} 条", flush=True)

    # ── Step 6: 写入数据库 ──────────────────────────────────────────
    if not dry_run:
        if not quiet:
            print(f"[Step 5] 写入数据库...", flush=True)

        saved_count = save_hotspots_to_db(categorized, hours, conn)

        if not quiet:
            print(f"[Step 5] 已保存 {saved_count} 条热点", flush=True)
    else:
        if not quiet:
            print("[DRY-RUN] 跳过数据库写入", flush=True)

    # ── 统计 ──────────────────────────────────────────────────────
    end_time = datetime.now(TZ_BJ)
    duration = (end_time - start_time).total_seconds()

    stats = {
        "hours": hours,
        "provider": provider,
        "unanalyzed_count": len(unanalyzed),
        "extracted_count": extracted_count,
        "hotspot_count": len(hotspots),
        "category_count": len(categorized),
        "duration_sec": round(duration, 1),
        "executed_at": start_time.isoformat(),
    }

    conn.close()

    if not quiet:
        print(f"\n{'='*60}", flush=True)
        print(f"[完成] 耗时 {duration:.1f}s", flush=True)
        print(f"{'='*60}\n", flush=True)

    return {
        "categories": categorized,
        "stats": stats,
    }


def save_hotspots_to_db(
    categorized: dict,
    hours: int,
    conn
) -> int:
    """
    将分类后的热点保存到 scheduled_hotspots 表

    Returns:
        保存的记录数
    """
    # 导入 HotspotEvent 用于类型检查
    from event_aggregator import HotspotEvent

    now = datetime.now(TZ_BJ).isoformat()
    saved = 0

    for category_id, hotspots in categorized.items():
        if not hotspots:
            continue

        # 转换为存储格式（处理 HotspotEvent 对象和 _original 字段）
        events_list = []
        for h in hotspots:
            if isinstance(h, HotspotEvent):
                event_dict = h.to_dict()
            elif isinstance(h, dict):
                event_dict = h.copy()
                # 移除不可序列化的字段
                if "_original" in event_dict:
                    del event_dict["_original"]
            else:
                continue
            events_list.append(event_dict)

        events_json = json.dumps(events_list, ensure_ascii=False)
        article_count = sum(h.get("article_count", 0) for h in events_list)

        conn.execute("""
            INSERT INTO scheduled_hotspots
            (category_id, category_name, executed_at, time_window_hours, events, event_count, article_count, keywords_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            category_id,
            get_category_name(category_id),
            now,
            hours,
            events_json,
            len(events_list),
            article_count,
            "[]"
        ])
        saved += 1

    conn.commit()
    return saved


# ═══════════════════════════════════════════════════════════════════════════════
# API 接口（供外部调用）
# ═══════════════════════════════════════════════════════════════════════════════

def get_category_hotspots(
    category_id: str,
    hours: int = 24,
    max_results: int = 20
) -> dict:
    """
    获取指定分类的热点（从数据库读取最新结果）

    Args:
        category_id: 分类 ID
        hours: 时间窗口
        max_results: 最大结果数

    Returns:
        分类热点结果
    """
    conn = get_conn()
    init_db(conn)

    # 查询最新的热点记录
    row = conn.execute("""
        SELECT * FROM scheduled_hotspots
        WHERE category_id = ?
        ORDER BY executed_at DESC
        LIMIT 1
    """, [category_id]).fetchone()

    conn.close()

    if not row:
        return {
            "category_id": category_id,
            "category_name": get_category_name(category_id),
            "events": [],
            "event_count": 0,
        }

    result = dict(row)
    events = json.loads(result.get("events", "[]"))
    events = events[:max_results]

    return {
        "category_id": category_id,
        "category_name": result.get("category_name", ""),
        "executed_at": result.get("executed_at", ""),
        "time_window_hours": result.get("time_window_hours", 24),
        "events": events,
        "event_count": len(events),
    }


def run_all_categories(
    hours: int = 24,
    provider: str = None,
    max_results: int = 20
) -> list[dict]:
    """
    运行所有分类的热点检测

    Returns:
        各分类热点结果列表
    """
    result = run_pipeline(
        hours=hours,
        provider=provider,
        max_results=max_results,
        quiet=True
    )

    outputs = []
    for category_id, hotspots in result.get("categories", {}).items():
        outputs.append({
            "category_id": category_id,
            "category_name": get_category_name(category_id),
            "executed_at": result["stats"]["executed_at"],
            "time_window_hours": hours,
            "events": hotspots[:max_results],
            "event_count": len(hotspots),
        })

    return outputs


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="热点提取流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/hotspot_pipeline.py --hours 24
  python scripts/hotspot_pipeline.py --run-all --json
  python scripts/hotspot_pipeline.py --type foreign_china --hours 6
        """
    )

    # 主要操作
    parser.add_argument("--run-all", action="store_true", help="运行所有分类")
    parser.add_argument("--type", type=str, metavar="CATEGORY", help="获取指定分类热点")

    # 参数
    parser.add_argument("--hours", type=int, default=24, help="时间窗口（小时）")
    parser.add_argument("--max", type=int, default=20, help="每分类最大结果数")
    parser.add_argument("--provider", type=str, help="指定 LLM 提供商")
    parser.add_argument("--min-hotspots", type=int, default=5, help="最小热点数（回补阈值）")

    # 输出选项
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写入数据库")
    parser.add_argument("--brief", action="store_true", help="简洁模式")

    args = parser.parse_args()

    # ── 获取指定分类 ──
    if args.type:
        valid_categories = [
            "foreign_china", "us_news", "japan_news",
            "greater_china", "middle_east", "asia_other"
        ]
        if args.type not in valid_categories:
            print(json.dumps({
                "error": f"无效分类: {args.type}",
                "valid_categories": valid_categories
            }, ensure_ascii=False))
            return

        result = get_category_hotspots(args.type, args.hours, args.max)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # ── 运行所有分类 ──
    if args.run_all:
        results = run_all_categories(
            hours=args.hours,
            provider=args.provider,
            max_results=args.max
        )

        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for r in results:
                print(f"\n【{r['category_name']}】{r['event_count']} 条热点")
                for i, h in enumerate(r['events'][:5], 1):
                    title = h.get('representative_title', '')[:40]
                    score = h.get('score', 0)
                    print(f"  {i}. [{score:.0f}] {title}")
        return

    # ── 默认：运行完整流水线 ──
    result = run_pipeline(
        hours=args.hours,
        provider=args.provider,
        max_results=args.max,
        min_hotspots=args.min_hotspots,
        dry_run=args.dry_run
    )

    if args.json:
        output = {
            "stats": result["stats"],
            "categories": {}
        }
        for cat_id, hotspots in result["categories"].items():
            # Convert HotspotEvent objects to dicts
            serializable_hotspots = []
            for h in hotspots[:args.max]:
                if hasattr(h, 'to_dict'):
                    serializable_hotspots.append(h.to_dict())
                elif isinstance(h, dict):
                    # Remove _original field if present
                    h_copy = h.copy()
                    if "_original" in h_copy:
                        del h_copy["_original"]
                    serializable_hotspots.append(h_copy)
                else:
                    serializable_hotspots.append(h)
            output["categories"][cat_id] = {
                "name": get_category_name(cat_id),
                "count": len(hotspots),
                "hotspots": serializable_hotspots
            }
        # Windows 编码处理
        output_str = json.dumps(output, ensure_ascii=False, indent=2)
        try:
            print(output_str)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(output_str.encode('utf-8'))
    else:
        # 安全输出函数
        def safe_print(text):
            try:
                print(text)
            except UnicodeEncodeError:
                # 过滤无法编码的字符
                safe_text = text.encode('gbk', errors='replace').decode('gbk')
                print(safe_text)

        safe_print("\n=== 结果摘要 ===")
        safe_print(f"时间: {result['stats']['executed_at']}")
        safe_print(f"耗时: {result['stats']['duration_sec']}s")
        safe_print(f"热点: {result['stats']['hotspot_count']} 个")
        safe_print("")
        for cat_id, hotspots in result["categories"].items():
            safe_print(f"【{get_category_name(cat_id)}】{len(hotspots)} 条")
            for i, h in enumerate(hotspots[:3], 1):
                title = h.get('representative_title', '')[:50]
                if args.brief:
                    safe_print(f"  {i}. {title}")
                else:
                    safe_print(f"  {i}. [{h.get('score', 0):.0f}] {title}")
                    safe_print(f"      文章: {h.get('article_count', 0)} | S级: {h.get('s_tier_count', 0)}")
            safe_print("")


if __name__ == "__main__":
    main()