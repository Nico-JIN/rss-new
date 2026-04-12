#!/usr/bin/env python3
"""
定时热点检测引擎

功能：
1. 支持6类热点检测（中国、美国、日本、中东、港澳台、亚洲其他）
2. 支持定时执行和手动触发
3. 支持历史记录存储
4. 提供CLI和API两种调用方式
5. 基于 event_key 流水线的热点聚合

使用方式：
    python scripts/scheduled_hotspot.py --help
    python scripts/scheduled_hotspot.py --list
    python scripts/scheduled_hotspot.py --run china_related
    python scripts/scheduled_hotspot.py --run-all
    python scripts/scheduled_hotspot.py --daemon
"""

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

try:
    from store import get_conn, init_db, TZ_BJ
    from hotspot_detector import detect_hot_events
    from filter_utils import is_chinese_media
except ImportError:
    print("[ERROR] 无法导入项目模块，请确保在项目根目录运行")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════════════════════

BASE = Path(__file__).parent.parent
CONFIG_PATH = BASE / "config" / "hotspot_schedule.yaml"


def load_config() -> dict:
    """加载配置文件"""
    if not CONFIG_PATH.exists():
        print(f"[ERROR] 配置文件不存在: {CONFIG_PATH}")
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text('utf-8')) or {}
    except Exception as e:
        print(f"[ERROR] 配置文件解析失败: {e}")
        return {}


def save_config(cfg: dict):
    """保存配置文件"""
    try:
        CONFIG_PATH.write_text(
            yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
            'utf-8'
        )
    except Exception as e:
        print(f"[ERROR] 配置文件保存失败: {e}")


def get_category_config(category_id: str = None) -> dict:
    """获取分类配置"""
    cfg = load_config()
    categories = cfg.get('categories', {})

    if category_id:
        return categories.get(category_id, {})
    return categories


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库操作
# ═══════════════════════════════════════════════════════════════════════════════

def init_hotspot_table(conn=None):
    """初始化热点记录表"""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_hotspots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id TEXT NOT NULL,
                category_name TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                time_window_hours INTEGER,
                events TEXT NOT NULL,
                event_count INTEGER DEFAULT 0,
                article_count INTEGER DEFAULT 0,
                keywords_used TEXT,
                duration_seconds INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success',
                created_at TEXT DEFAULT (datetime('now', '+8 hours'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hotspot_category ON scheduled_hotspots(category_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hotspot_executed ON scheduled_hotspots(executed_at)")
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def save_hotspot_result(category_id: str, category_name: str, events: list,
                         hours: int, keywords: list, conn=None,
                         duration_seconds: int = 0, status: str = 'success') -> int:
    """
    保存热点检测结果

    Args:
        category_id: 分类ID
        category_name: 分类名称
        events: 热点事件列表
        hours: 时间窗口
        keywords: 使用的关键词
        conn: 数据库连接
        duration_seconds: 执行耗时（秒）
        status: 执行状态（success/failed/partial）

    Returns:
        记录ID
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        now = datetime.now(TZ_BJ).isoformat()

        # 统计文章数
        article_count = sum(e.get('article_count', len(e.get('items', []))) for e in events)

        conn.execute("""
            INSERT INTO scheduled_hotspots
            (category_id, category_name, executed_at, time_window_hours, events, event_count, article_count, keywords_used, duration_seconds, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            category_id,
            category_name,
            now,
            hours,
            json.dumps(events, ensure_ascii=False),
            len(events),
            article_count,
            json.dumps(keywords, ensure_ascii=False),
            duration_seconds,
            status
        ])
        conn.commit()

        # 返回插入的ID
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        if own_conn:
            conn.close()


def get_latest_hotspot(category_id: str = None, conn=None) -> dict:
    """获取最新的热点结果"""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        if category_id:
            row = conn.execute("""
                SELECT * FROM scheduled_hotspots
                WHERE category_id = ?
                ORDER BY executed_at DESC LIMIT 1
            """, [category_id]).fetchone()
        else:
            row = conn.execute("""
                SELECT * FROM scheduled_hotspots
                ORDER BY executed_at DESC LIMIT 1
            """).fetchone()

        if row:
            return dict(row)
        return {}
    finally:
        if own_conn:
            conn.close()


def get_hotspot_history(category_id: str = None, days: int = 7, conn=None) -> list:
    """获取历史记录"""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        cutoff = (datetime.now(TZ_BJ) - timedelta(days=days)).isoformat()

        if category_id:
            rows = conn.execute("""
                SELECT * FROM scheduled_hotspots
                WHERE category_id = ? AND executed_at >= ?
                ORDER BY executed_at DESC
            """, [category_id, cutoff]).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM scheduled_hotspots
                WHERE executed_at >= ?
                ORDER BY executed_at DESC
            """, [cutoff]).fetchall()

        return [dict(r) for r in rows]
    finally:
        if own_conn:
            conn.close()


def cleanup_old_records(days: int = 30, conn=None):
    """清理过期记录"""
    own_conn = conn is None
    if own_conn:
        conn = get_conn()

    try:
        cutoff = (datetime.now(TZ_BJ) - timedelta(days=days)).isoformat()
        result = conn.execute("DELETE FROM scheduled_hotspots WHERE executed_at < ?", [cutoff])
        conn.commit()
        return result.rowcount
    finally:
        if own_conn:
            conn.close()




# ═══════════════════════════════════════════════════════════════════════════════
# 核心检测逻辑（v3 — event_key 流水线模式）
# ═══════════════════════════════════════════════════════════════════════════════

# 分类 ID 映射：旧分类名 → 新分类名
CATEGORY_ID_MAP = {
    'china_related': 'foreign_china',
    'hk_tw_macau': 'greater_china',
    'asia_neighbors': 'asia_other',
    # us_news, japan_news, middle_east, international 保持不变
}

# Agent 可用的分类列表
AGENT_CATEGORIES = [
    'international',    # 国际热点
    'foreign_china',    # 外媒报道中国
    'us_news',          # 美国新闻
    'japan_news',       # 日本新闻
    'middle_east',      # 中东新闻
    'greater_china',    # 港澳台新闻
    'asia_other',       # 亚洲其他国家
]

# 旧分类名（兼容）
LEGACY_CATEGORIES = ['china_related', 'us_news', 'japan_news', 'middle_east', 'hk_tw_macau', 'asia_neighbors']


def run_detection_v3(
    category_id: str,
    hours: int = 24,
    max_results: int = 20,
    provider: str = None,
    quiet: bool = False
) -> dict:
    """
    执行热点检测 (v3 — event_key 流水线模式)

    新流程：
    1. 拉取未分析文章
    2. LLM 提取 event_key 等字段
    3. 按 event_key 聚合
    4. 六大分类映射

    Args:
        category_id: 分类ID（支持旧名称自动映射）
        hours: 时间窗口
        max_results: 最大结果数
        provider: LLM 提供商
        quiet: 静默模式

    Returns:
        检测结果
    """
    from hotspot_pipeline import (
        run_pipeline,
        get_category_hotspots,
        get_category_name as v3_get_category_name
    )

    # 记录开始时间
    start_time = datetime.now(TZ_BJ)

    # 分类 ID 映射
    mapped_category_id = CATEGORY_ID_MAP.get(category_id, category_id)

    # 获取配置
    cat_cfg = get_category_config(category_id)
    category_name = cat_cfg.get('name', category_id) if cat_cfg else v3_get_category_name(mapped_category_id)
    hours = hours or (cat_cfg.get('hours', 24) if cat_cfg else 24)
    max_results = max_results or (cat_cfg.get('max_results', 20) if cat_cfg else 20)

    if provider is None:
        cfg = load_config()
        provider = cfg.get('settings', {}).get('narrative_provider', 'volcengine')

    if not quiet:
        print(f"\n{'='*60}", flush=True)
        print(f"[检测 v3] {category_name}", flush=True)
        print(f"  时间窗口: {hours}h", flush=True)
        print(f"  模式: event_key 流水线", flush=True)
        print(f"  LLM Provider: {provider}", flush=True)
        print('='*60, flush=True)

    # 执行流水线
    result = run_pipeline(
        hours=hours,
        provider=provider,
        max_results=max_results,
        quiet=quiet
    )

    # 提取指定分类的热点
    hotspots = result.get('categories', {}).get(mapped_category_id, [])

    # 导入 HotspotEvent 用于类型检查
    from event_aggregator import HotspotEvent

    # 转换为旧格式（兼容现有前端）
    # 注意：确保返回的数据格式与数据库存储格式一致
    events = []
    for h in hotspots[:max_results]:
        # 处理 HotspotEvent 对象和字典
        if isinstance(h, HotspotEvent):
            h_dict = h.to_dict()
        else:
            h_dict = h

        event = {
            'title': h_dict.get('representative_title', ''),
            'representative_title': h_dict.get('representative_title', ''),  # 添加此字段保持一致
            'score': h_dict.get('score', 0),
            'count': h_dict.get('article_count', 0),
            'media_count': len(h_dict.get('sources', [])),
            's_tier_count': h_dict.get('s_tier_count', 0),  # 添加此字段
            'platforms': h_dict.get('sources', []),
            'sources': h_dict.get('sources', []),  # 添加此字段
            'tags': h_dict.get('entities', []),
            'entities': h_dict.get('entities', []),  # 添加此字段
            'latest_published': h_dict.get('latest_published', ''),  # 添加时间字段
            's_tier_sources': h_dict.get('s_tier_sources', []),  # 核心修复：添加 S 级媒体列表
            'is_china_related': mapped_category_id == 'foreign_china',
            'article_ids': [a.get('id') for a in h_dict.get('articles', []) if a.get('id')],
            'article_count': h_dict.get('article_count', 0),
            'items': h_dict.get('articles', []),
        }
        events.append(event)

    # 计算执行耗时
    end_time = datetime.now(TZ_BJ)
    duration_seconds = int((end_time - start_time).total_seconds())

    # ── 保存结果 ──
    if events:
        # 保存前清理详情内容以避免数据库过大
        events_for_save = []
        for e in events:
            # 保留关键字段，移除大字段
            save_event = {
                'title': e.get('title', ''),
                'representative_title': e.get('representative_title', ''),
                'score': e.get('score', 0),
                'count': e.get('count', 0),
                'media_count': e.get('media_count', 0),
                's_tier_count': e.get('s_tier_count', 0),
                'sources': e.get('sources', []),  # 确保 sources 被保存
                'platforms': e.get('platforms', []),
                'entities': e.get('entities', []),
                'tags': e.get('tags', []),
                'latest_published': e.get('latest_published', ''),
                'article_ids': e.get('article_ids', []),
                'article_count': e.get('article_count', 0),
            }
            save_event['articles'] = [
                {
                    'id': a.get('id'),
                    'title': a.get('title', ''),
                    'url': a.get('url', ''),
                    'platform': a.get('platform', ''),
                    'published': a.get('published', ''),
                    'summary': a.get('summary', '')[:200],
                    'image': a.get('image', ''),
                }
                for a in e.get('items', [])
            ]
            events_for_save.append(save_event)

        save_hotspot_result(
            category_id, category_name, events_for_save,
            hours, [], conn=None
        )

    # 确定状态
    status = 'success' if events else 'partial'

    return {
        'category_id': category_id,
        'category_name': category_name,
        'executed_at': result.get('stats', {}).get('executed_at', ''),
        'time_window_hours': hours,
        'keywords_used': [],
        'event_count': len(events),
        'events': events,
        'stats': result.get('stats', {}),
        'duration_seconds': duration_seconds,
        'status': status,
    }


def run_all_categories_v3(
    hours: int = 24,
    provider: str = None,
    max_results: int = 20,
    quiet: bool = False
) -> list:
    """
    执行所有分类的 v3 检测

    Args:
        hours: 时间窗口
        provider: LLM 提供商
        max_results: 每分类最大结果数
        quiet: 静默模式

    Returns:
        各分类结果列表
    """
    from hotspot_pipeline import run_pipeline

    if provider is None:
        cfg = load_config()
        provider = cfg.get('settings', {}).get('narrative_provider', 'volcengine')

    # 执行一次完整流水线
    result = run_pipeline(
        hours=hours,
        provider=provider,
        max_results=max_results,
        quiet=quiet
    )

    # 转换为旧格式
    results = []
    for old_cat_id in ['china_related', 'us_news', 'japan_news', 'middle_east', 'hk_tw_macau', 'asia_neighbors']:
        mapped_cat_id = CATEGORY_ID_MAP.get(old_cat_id, old_cat_id)
        hotspots = result.get('categories', {}).get(mapped_cat_id, [])

        cat_cfg = get_category_config(old_cat_id)
        category_name = cat_cfg.get('name', old_cat_id) if cat_cfg else mapped_cat_id

        events = []
        for h in hotspots[:max_results]:
            event = {
                'title': h.get('representative_title', ''),
                'score': h.get('score', 0),
                'count': h.get('article_count', 0),
                'media_count': len(h.get('sources', [])),
                'platforms': h.get('sources', []),
                'tags': h.get('entities', []),
                'is_china_related': mapped_cat_id == 'foreign_china',
                'article_ids': [a.get('id') for a in h.get('articles', []) if a.get('id')],
                's_tier_sources': h.get('s_tier_sources', []),  # 核心修复：添加 S 级媒体列表
                'items': h.get('articles', []),
            }
            events.append(event)

        cat_result = {
            'category_id': old_cat_id,
            'category_name': category_name,
            'executed_at': result.get('stats', {}).get('executed_at', ''),
            'time_window_hours': hours,
            'keywords_used': [],
            'event_count': len(events),
            'events': events,
        }

        # ── 保存各分类结果 ──
        if events:
            events_for_save = []
            for e in events:
                save_event = {k: v for k, v in e.items() if k != 'items'}
                save_event['articles'] = [
                    {
                        'id': a.get('id'),
                        'title': a.get('title', ''),
                        'url': a.get('url', ''),
                        'platform': a.get('platform', ''),
                        'published': a.get('published', ''),
                        'summary': a.get('summary', '')[:200],
                        'image': a.get('image', ''),
                    }
                    for a in e.get('items', [])
                ]
                save_event['article_count'] = len(save_event['articles'])
                events_for_save.append(save_event)

            save_hotspot_result(
                old_cat_id, category_name, events_for_save,
                hours, [], conn=None
            )

        results.append(cat_result)

    return results


def run_all_categories(conn=None) -> list:
    """执行所有启用的分类检测（使用 v3 流水线）"""
    cfg = load_config()
    categories = cfg.get('categories', {})

    results = []
    for cat_id, cat_cfg in categories.items():
        if cat_cfg.get('enabled', True):
            try:
                result = run_detection_v3(cat_id)
                results.append(result)
            except Exception as e:
                print(f"[ERROR] {cat_id} 检测失败: {e}")

    # 清理过期记录
    retention_days = cfg.get('settings', {}).get('retention_days', 30)
    cleanup_old_records(retention_days, conn)

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 定时任务
# ═══════════════════════════════════════════════════════════════════════════════

_daemon_running = False
_daemon_thread = None


def _parse_cron(cron_expr: str) -> dict:
    """解析cron表达式"""
    parts = cron_expr.split()
    if len(parts) != 5:
        return {}

    return {
        'minute': parts[0],
        'hour': parts[1],
        'day': parts[2],
        'month': parts[3],
        'weekday': parts[4]
    }


def _should_run_now(cron_expr: str, now: datetime) -> bool:
    """判断当前时间是否应该执行"""
    parsed = _parse_cron(cron_expr)
    if not parsed:
        return False

    minute = parsed['minute']
    hour = parsed['hour']

    # 简化判断：只检查分钟和小时
    if minute.startswith('*/'):
        interval = int(minute[2:])
        if now.minute % interval != 0:
            return False
    elif minute != '*' and now.minute != int(minute):
        return False

    if hour.startswith('*/'):
        interval = int(hour[2:])
        if now.hour % interval != 0:
            return False
    elif hour != '*' and now.hour != int(hour):
        return False

    return True


def _daemon_loop():
    """定时任务主循环"""
    global _daemon_running
    _daemon_running = True

    print("[DAEMON] 定时热点检测服务启动")
    last_run = {}  # 记录每个分类的最后执行时间

    while _daemon_running:
        try:
            now = datetime.now(TZ_BJ)
            cfg = load_config()
            categories = cfg.get('categories', {})

            for cat_id, cat_cfg in categories.items():
                if not cat_cfg.get('enabled', True):
                    continue

                schedule = cat_cfg.get('schedule', '')
                if not schedule:
                    continue

                # 检查是否应该执行
                if _should_run_now(schedule, now):
                    # 避免同一分钟内重复执行
                    last_key = f"{cat_id}_{now.strftime('%Y%m%d%H%M')}"
                    if last_key not in last_run:
                        print(f"\n[DAEMON] 触发定时任务: {cat_cfg.get('name', cat_id)} (V3 流水线)")
                        try:
                            # 迁移至 V3 流水线
                            run_detection_v3(cat_id)
                        except Exception as e:
                            print(f"[ERROR] 定时探测（V3）执行失败: {e}")
                        last_run[last_key] = True

            # 清理过期的执行记录（每小时清理一次）
            if now.minute == 0:
                retention_days = cfg.get('settings', {}).get('retention_days', 30)
                cleanup_old_records(retention_days)
                # 清理 last_run 缓存
                cutoff = (now - timedelta(hours=2)).strftime('%Y%m%d%H%M')
                last_run = {k: v for k, v in last_run.items() if k.split('_')[-1] >= cutoff}

            time.sleep(60)  # 每分钟检查一次

        except Exception as e:
            print(f"[ERROR] 定时任务异常: {e}")
            time.sleep(60)

    print("[DAEMON] 定时热点检测服务停止")


def start_daemon():
    """启动定时服务"""
    global _daemon_thread

    if _daemon_thread and _daemon_thread.is_alive():
        print("[WARN] 定时服务已在运行")
        return

    _daemon_thread = threading.Thread(target=_daemon_loop, daemon=True)
    _daemon_thread.start()
    print("[INFO] 定时服务已启动（后台运行）")


def stop_daemon():
    """停止定时服务"""
    global _daemon_running
    _daemon_running = False
    print("[INFO] 定时服务停止请求已发送")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='定时热点检测',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # Agent 推荐使用 --type 参数（返回 JSON）
  python scripts/scheduled_hotspot.py --type international --hours 12 --json
  python scripts/scheduled_hotspot.py --type foreign_china --hours 24 --json
  python scripts/scheduled_hotspot.py --type us_news --hours 6 --json
  python scripts/scheduled_hotspot.py --type middle_east --hours 12 --json
  python scripts/scheduled_hotspot.py --type japan_news --hours 24 --json

  # 其他操作
  python scripts/scheduled_hotspot.py --list
  python scripts/scheduled_hotspot.py --run foreign_china
  python scripts/scheduled_hotspot.py --run-all
  python scripts/scheduled_hotspot.py --daemon
        """
    )

    # Agent 专用接口（推荐）
    parser.add_argument('--type', type=str, metavar='CATEGORY',
                        help='获取指定分类热点（返回JSON）: international, foreign_china, us_news, japan_news, middle_east, greater_china, asia_other')

    # 主要操作
    parser.add_argument('--list', action='store_true', help='列出所有分类配置')
    parser.add_argument('--run', type=str, metavar='CATEGORY', help='执行指定分类的检测')
    parser.add_argument('--run-all', action='store_true', help='执行所有启用的检测')
    parser.add_argument('--history', action='store_true', help='查看历史记录')
    parser.add_argument('--daemon', action='store_true', help='启动定时服务')
    parser.add_argument('--stop', action='store_true', help='停止定时服务')

    # 参数覆盖
    parser.add_argument('--hours', type=int, default=24, help='时间窗口（小时），默认24')
    parser.add_argument('--max', type=int, help='最大结果数')
    parser.add_argument('--provider', type=str, help='指定 LLM 提供商')

    # 输出格式
    parser.add_argument('--json', action='store_true', help='JSON格式输出')
    parser.add_argument('--brief', action='store_true', help='简洁模式')
    parser.add_argument('--simple', action='store_true', help='Agent精简模式（仅热点标题+来源文章）')

    # 历史记录参数
    parser.add_argument('--category', type=str, help='指定分类')
    parser.add_argument('--days', type=int, default=7, help='历史记录天数')

    # 配置管理
    parser.add_argument('--enable', type=str, metavar='CATEGORY', help='启用分类')
    parser.add_argument('--disable', type=str, metavar='CATEGORY', help='禁用分类')
    parser.add_argument('--config', type=str, metavar='CATEGORY', help='修改分类配置')

    args = parser.parse_args()

    # 初始化数据库
    init_hotspot_table()

    # --type 参数：Agent 专用接口（优先处理）
    if args.type:
        # 支持新旧分类名
        valid_categories = AGENT_CATEGORIES + LEGACY_CATEGORIES
        if args.type not in valid_categories:
            result = {
                'error': f'无效分类: {args.type}',
                'valid_categories': AGENT_CATEGORIES
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        result = run_detection_v3(
            args.type,
            hours=args.hours,
            max_results=args.max,
            provider=args.provider,
            quiet=True
        )

        # Agent 精简模式
        if args.simple:
            simple_result = {
                'category': result.get('category_name', ''),
                'count': result.get('event_count', 0),
                'hotspots': []
            }
            for e in result.get('events', []):
                hotspot = {
                    'title': e.get('title', ''),
                    'score': e.get('score', 0),
                    'media_count': e.get('media_count', 0),
                    'articles': []
                }
                # 只保留文章核心字段
                for a in e.get('items', []):
                    article = {
                        'title': a.get('title', ''),
                        'url': a.get('url', ''),
                        'platform': a.get('platform', ''),
                        'published': a.get('published', ''),
                        'image': a.get('image', ''),
                    }
                    # 摘要（如果有）
                    if a.get('summary'):
                        article['summary'] = a['summary'][:300]
                    hotspot['articles'].append(article)
                simple_result['hotspots'].append(hotspot)

            output = json.dumps(simple_result, ensure_ascii=False, indent=2)
        else:
            output = json.dumps(result, ensure_ascii=False, indent=2)

        try:
            print(output)
        except UnicodeEncodeError:
            sys.stdout.buffer.write(output.encode('utf-8'))
        return

    # 列出配置
    if args.list:
        cfg = load_config()
        categories = cfg.get('categories', {})

        print("\n可用分类:")
        print("-" * 60)
        for cat_id, cat_cfg in categories.items():
            status = "[OK]" if cat_cfg.get('enabled', True) else "[--]"
            schedule = cat_cfg.get('schedule', '-')
            keywords = cat_cfg.get('keywords', [])
            print(f"  {status} {cat_id}")
            print(f"      名称: {cat_cfg.get('name', '-')}")
            print(f"      定时: {schedule}")
            print(f"      关键字: {', '.join(keywords[:5])}{'...' if len(keywords) > 5 else ''}")
            print()
        return

    # 执行指定分类
    if args.run:
        result = run_detection_v3(
            args.run,
            hours=args.hours,
            max_results=args.max,
            provider=args.provider
        )

        if args.json:
            # Windows终端编码处理
            output = json.dumps(result, ensure_ascii=False, indent=2)
            try:
                print(output)
            except UnicodeEncodeError:
                # 回退到ASCII输出
                print(output.encode('utf-8', errors='replace').decode('utf-8'))
        else:
            print_result(result, brief=args.brief)
        return

    # 执行所有分类
    if args.run_all:
        results = run_all_categories_v3(
            hours=args.hours,
            provider=args.provider,
            max_results=args.max
        )
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for r in results:
                print_result(r, brief=args.brief)
        return

    # 查看历史
    if args.history:
        records = get_hotspot_history(category_id=args.category, days=args.days)

        if args.json:
            output = []
            for r in records:
                r['events'] = json.loads(r['events']) if isinstance(r['events'], str) else r['events']
                output.append(r)
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print(f"\n历史记录 (最近 {args.days} 天):")
            print("-" * 60)
            for r in records:
                print(f"  [{r['category_name']}] {r['executed_at'][:16]}")
                print(f"      热点: {r['event_count']} | 文章: {r['article_count']}")
            print()
        return

    # 启动定时服务
    if args.daemon:
        start_daemon()
        # 保持运行
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_daemon()
        return

    # 停止定时服务
    if args.stop:
        stop_daemon()
        return

    # 启用分类
    if args.enable:
        cfg = load_config()
        if args.enable in cfg.get('categories', {}):
            cfg['categories'][args.enable]['enabled'] = True
            save_config(cfg)
            print(f"[OK] 已启用: {args.enable}")
        else:
            print(f"[ERROR] 分类不存在: {args.enable}")
        return

    # 禁用分类
    if args.disable:
        cfg = load_config()
        if args.disable in cfg.get('categories', {}):
            cfg['categories'][args.disable]['enabled'] = False
            save_config(cfg)
            print(f"[OK] 已禁用: {args.disable}")
        else:
            print(f"[ERROR] 分类不存在: {args.disable}")
        return

    # 修改配置
    if args.config:
        cfg = load_config()
        if args.config not in cfg.get('categories', {}):
            print(f"[ERROR] 分类不存在: {args.config}")
            return

        cat = cfg['categories'][args.config]
        if args.hours:
            cat['hours'] = args.hours
        if args.max:
            cat['max_results'] = args.max
        if args.keywords:
            cat['keywords'] = args.keywords.split(',')

        save_config(cfg)
        print(f"[OK] 配置已更新: {args.config}")
        return

    # 默认显示帮助
    parser.print_help()


def print_result(result: dict, brief: bool = False):
    """打印检测结果"""
    if 'error' in result:
        print(f"[ERROR] {result['error']}")
        return

    print(f"\n分类: {result.get('category_name', '-')}")
    print(f"时间: {result.get('executed_at', '-')}")
    print(f"热点数: {result.get('event_count', 0)}")
    print("-" * 60)

    events = result.get('events', [])
    for i, event in enumerate(events, 1):
        title = event.get('title', '无标题')
        # 过滤非法 Unicode 字符
        title = ''.join(c for c in title if c.isprintable() and ord(c) < 0x10000)
        score = event.get('score', 0)
        media_count = event.get('media_count', len(event.get('platforms', [])))
        article_count = event.get('article_count', event.get('count', 0))

        if brief:
            print(f"  {i}. [{score}] {title[:40]}...")
        else:
            print(f"\n【{i}】{title}")
            print(f"    热度: {score} | 媒体: {media_count} | 文章: {article_count}")
            platforms = event.get('platforms', event.get('all_platforms', []))
            if platforms:
                print(f"    来源: {', '.join(platforms[:5])}")


if __name__ == '__main__':
    main()