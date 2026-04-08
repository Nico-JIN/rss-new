#!/usr/bin/env python3
"""面向 Agent 的轻量查询 CLI — 直接读取 SQLite，零网络请求"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from store import (
    init_db, get_conn, query_by_time, query_by_keyword,
    query_by_period, get_stats, count_articles, query_by_tag
)

TZ_BJ = timezone(timedelta(hours=8))
INTERNAL_FIELDS = {'id', 'url_hash', 'title_hash', 'created_at'}


def clean_item(item: dict, with_content: bool = False) -> dict:
    """清除内部字段 + 空值，减少 token。默认隐藏 content 以节省 token。"""
    res = {}
    for k, v in item.items():
        if k in INTERNAL_FIELDS or v in ('', None):
            continue
        if k == 'content' and not with_content:
            continue
        # llm_tags: 空列表不输出，非空列表保留
        if k == 'llm_tags':
            if isinstance(v, str):
                try:
                    import json as _json
                    v = _json.loads(v)
                except Exception:
                    v = []
            if not v:
                continue
        # 强制保留摘要和图片字段（即便为空），方便客户端渲染
        if k in ('summary', 'image'):
             res[k] = v or ''
             continue

        if v in ('', None, []):
            continue
        res[k] = v
    return res


def main():
    ap = argparse.ArgumentParser(description='RSS 新闻查询（从 SQLite 本地库读取）')
    ap.add_argument('--period', choices=['day', 'week', 'month'],
                    help='按日/周/月查询')
    ap.add_argument('--offset', type=int, default=0,
                    help='期偏移量：0=本期, -1=上期')
    ap.add_argument('--hours', type=float, default=None,
                    help='按最近 N 小时查询')
    ap.add_argument('--start', type=str, default=None,
                    help='起始时间 (ISO 8601)')
    ap.add_argument('--end', type=str, default=None,
                    help='结束时间 (ISO 8601)')
    ap.add_argument('--keyword', type=str, default=None,
                    help='关键字模糊搜索')
    ap.add_argument('--media', type=str, default=None,
                    help='按媒体组过滤')
    ap.add_argument('--tag', type=str, default=None,
                    help='按 LLM 语义标签筛选（如 "中国言论"）')
    ap.add_argument('--limit', type=int, default=100,
                    help='最大返回条数')
    ap.add_argument('--with-content', action='store_true',
                    help='返回时携带完整 content (警告: 会占用大量 Token)')
    ap.add_argument('--stats', action='store_true',
                    help='输出数据库统计信息')
    args = ap.parse_args()

    # 初始化数据库
    conn = get_conn()
    init_db(conn)

    # 统计模式
    if args.stats:
        stats = get_stats(conn)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        conn.close()
        return

    # 确定时间范围
    now = datetime.now(TZ_BJ)
    start_str = args.start
    end_str = args.end

    if args.hours is not None:
        start_str = (now - timedelta(hours=args.hours)).isoformat()
        end_str = now.isoformat()

    # 执行查询
    if args.tag:
        items = query_by_tag(
            tag=args.tag, start=start_str, end=end_str,
            media_group=args.media, limit=args.limit, conn=conn)
        period_label = f"tag='{args.tag}'"
        if start_str:
            period_label += f" [{start_str} ~ {end_str or 'now'}]"
    elif args.period:
        items = query_by_period(
            period=args.period, offset_n=args.offset,
            media_group=args.media, limit=args.limit, conn=conn)
        period_label = f"{args.period}(offset={args.offset})"
    elif args.keyword:
        items = query_by_keyword(
            keyword=args.keyword, start=start_str, end=end_str,
            media_group=args.media, limit=args.limit, conn=conn)
        period_label = f"keyword='{args.keyword}'"
        if start_str:
            period_label += f" [{start_str} ~ {end_str or 'now'}]"
    elif start_str:
        items = query_by_time(
            start=start_str, end=end_str or now.isoformat(),
            media_group=args.media, limit=args.limit, conn=conn)
        period_label = f"{start_str} ~ {end_str or now.isoformat()}"
    else:
        # 默认：今天
        items = query_by_period(
            period='day', offset_n=0,
            media_group=args.media, limit=args.limit, conn=conn)
        period_label = "today"

    conn.close()

    output = {
        'count': len(items),
        'query': period_label,
        'items': [clean_item(i, args.with_content) for i in items]
    }
    sys.stdout.buffer.write(
        json.dumps(output, ensure_ascii=False, indent=2).encode('utf-8'))
    print()


if __name__ == '__main__':
    main()
