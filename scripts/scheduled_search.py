#!/usr/bin/env python3
"""
定时关键词搜索调度器

功能：
1. 关键词监控任务管理
2. 定时触发搜索
3. 结果存储与去重

使用方式：
    # CLI 触发一次性搜索
    python scheduled_search.py --keyword "中国 关税" --sources google_news,brave

    # 运行所有待执行的监控任务
    python scheduled_search.py --run-all
"""

import argparse
import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from pathlib import Path

TZ_BJ = timezone(timedelta(hours=8))


class ScheduledSearchManager:
    """定时搜索管理器"""

    # 搜索间隔映射（秒）
    INTERVAL_SECONDS = {
        'hourly': 3600,
        'daily': 86400,
        'weekly': 604800
    }

    def __init__(self, conn=None):
        """
        Args:
            conn: 数据库连接，若为 None 则自动获取
        """
        self._own_conn = conn is None
        self._conn = conn

        if self._own_conn:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from store import get_conn, init_db
            self._conn = get_conn()
            init_db(self._conn)

    def _get_conn(self):
        return self._conn

    def close(self):
        if self._own_conn and self._conn:
            self._conn.close()
            self._conn = None

    # ── 监控任务管理 ──────────────────────────────────────────────

    def add_watch(self, keyword: str, sources: List[str],
                  interval: str = 'daily') -> int:
        """
        添加关键词监控任务

        Args:
            keyword: 监控关键词
            sources: 搜索源列表 ['google_news', 'bing', 'brave', 'tavily']
            interval: 搜索间隔 (hourly/daily/weekly)

        Returns:
            watch_id
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import create_keyword_watch

        return create_keyword_watch(
            keyword=keyword,
            sources=sources,
            interval=interval,
            conn=self._get_conn()
        )

    def list_watches(self, enabled_only: bool = True) -> List[Dict]:
        """列出监控任务"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import list_keyword_watches

        return list_keyword_watches(enabled_only=enabled_only, conn=self._get_conn())

    def enable_watch(self, watch_id: int, enabled: bool = True):
        """启用/禁用监控"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import set_keyword_watch_enabled

        set_keyword_watch_enabled(watch_id, enabled, conn=self._get_conn())

    def remove_watch(self, watch_id: int):
        """删除监控"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import delete_keyword_watch

        delete_keyword_watch(watch_id, conn=self._get_conn())

    # ── 搜索执行 ──────────────────────────────────────────────

    def execute_search(self, keyword: str, sources: List[str],
                       max_results: int = 20) -> List[Dict]:
        """
        执行关键词搜索

        Args:
            keyword: 搜索关键词
            sources: 搜索源列表
            max_results: 每个源最大结果数

        Returns:
            合并后的搜索结果列表
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from external_fetcher import UnifiedSearcher

        searcher = UnifiedSearcher()
        results = searcher.search(
            query=keyword,
            sources=sources,
            max_results=max_results
        )

        return results

    def execute_watch(self, watch_id: int) -> Dict:
        """
        执行单个监控任务的搜索

        Args:
            watch_id: 监控任务ID

        Returns:
            执行结果 {'success': bool, 'results': list, 'stored': int}
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import (list_keyword_watches, update_keyword_watch_last_search,
                           upsert_external_articles)

        # 获取监控任务
        watches = list_keyword_watches(enabled_only=False, conn=self._get_conn())
        watch = None
        for w in watches:
            if w['id'] == watch_id:
                watch = w
                break

        if not watch:
            return {'success': False, 'error': '监控任务不存在'}

        if not watch['enabled']:
            return {'success': False, 'error': '监控任务已禁用'}

        # 执行搜索
        keyword = watch['keyword']
        sources = watch['sources']

        print(f"[INFO] 执行监控 #{watch_id}: '{keyword}' 从 {sources}")

        try:
            results = self.execute_search(keyword, sources)

            # 存储结果
            stored_count = upsert_external_articles(
                articles=results,
                keyword_match=keyword,
                conn=self._get_conn()
            )

            # 更新最后搜索时间
            update_keyword_watch_last_search(watch_id, conn=self._get_conn())

            return {
                'success': True,
                'watch_id': watch_id,
                'keyword': keyword,
                'results_count': len(results),
                'stored_count': stored_count
            }

        except Exception as e:
            return {
                'success': False,
                'watch_id': watch_id,
                'error': str(e)
            }

    def run_due_watches(self) -> List[Dict]:
        """
        运行所有到期需要执行的监控任务

        根据每个任务的 interval 和 last_search 判断是否需要执行

        Returns:
            执行结果列表
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import list_keyword_watches

        watches = list_keyword_watches(enabled_only=True, conn=self._get_conn())
        now = datetime.now(TZ_BJ)

        results = []
        for watch in watches:
            # 检查是否到期
            last_search = watch.get('last_search', '')
            interval = watch.get('interval', 'daily')

            if last_search:
                try:
                    last_dt = datetime.fromisoformat(last_search)
                    interval_secs = self.INTERVAL_SECONDS.get(interval, 86400)
                    elapsed = (now - last_dt.replace(tzinfo=TZ_BJ)).total_seconds()

                    if elapsed < interval_secs:
                        # 未到期，跳过
                        continue
                except:
                    pass

            # 执行搜索
            result = self.execute_watch(watch['id'])
            results.append(result)

        return results

    # ── CLI 快捷命令 ──────────────────────────────────────────────

    def search_and_store(self, keyword: str, sources: List[str],
                         max_results: int = 20) -> Dict:
        """
        一次性搜索并存储结果

        Args:
            keyword: 搜索关键词
            sources: 搜索源列表
            max_results: 每个源最大结果数

        Returns:
            搜索结果摘要
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import upsert_external_articles

        results = self.execute_search(keyword, sources, max_results)
        stored_count = upsert_external_articles(
            articles=results,
            keyword_match=keyword,
            conn=self._get_conn()
        )

        return {
            'keyword': keyword,
            'sources': sources,
            'total_results': len(results),
            'stored_count': stored_count,
            'results': results
        }

    def get_stored_results(self, keyword: str = None,
                           source_type: str = None,
                           hours: int = 24,
                           limit: int = 50) -> List[Dict]:
        """
        获取已存储的搜索结果

        Args:
            keyword: 过滤关键词
            source_type: 过滤来源类型
            hours: 时间窗口（小时）
            limit: 结果上限
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import query_external_articles

        now = datetime.now(TZ_BJ)
        start = (now - timedelta(hours=hours)).isoformat()

        return query_external_articles(
            keyword=keyword,
            source_type=source_type,
            start=start,
            limit=limit,
            conn=self._get_conn()
        )


# ═══════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='定时关键词搜索调度器')

    parser.add_argument('--keyword', type=str, help='搜索关键词')
    parser.add_argument('--sources', type=str, default='google_news',
                        help='搜索源（逗号分隔）：google_news,bing,brave,tavily')
    parser.add_argument('--max', type=int, default=20, help='每源最大结果数')

    parser.add_argument('--add-watch', action='store_true',
                        help='添加监控任务')
    parser.add_argument('--interval', choices=['hourly', 'daily', 'weekly'],
                        default='daily', help='监控间隔')

    parser.add_argument('--list-watches', action='store_true',
                        help='列出监控任务')
    parser.add_argument('--run-all', action='store_true',
                        help='运行所有到期监控')
    parser.add_argument('--execute', type=int,
                        help='执行指定ID的监控任务')

    parser.add_argument('--get-results', action='store_true',
                        help='获取已存储结果')
    parser.add_argument('--hours', type=int, default=24,
                        help='时间窗口（小时）')

    parser.add_argument('--json', action='store_true',
                        help='JSON格式输出')

    args = parser.parse_args()

    mgr = ScheduledSearchManager()

    try:
        # 添加监控
        if args.add_watch and args.keyword:
            sources = [s.strip() for s in args.sources.split(',')]
            watch_id = mgr.add_watch(args.keyword, sources, args.interval)
            print(f"[OK] 监控任务已创建，ID: {watch_id}")

        # 列出监控
        elif args.list_watches:
            watches = mgr.list_watches(enabled_only=False)
            if args.json:
                print(json.dumps(watches, ensure_ascii=False, indent=2))
            else:
                print(f"\n共 {len(watches)} 个监控任务:")
                for w in watches:
                    status = "启用" if w['enabled'] else "禁用"
                    last = w.get('last_search', '从未执行')[:16]
                    print(f"  [{w['id']}] '{w['keyword']}' - {w['interval']} - {status}")
                    print(f"       来源: {w['sources']} | 最后执行: {last}")

        # 运行所有到期监控
        elif args.run_all:
            results = mgr.run_due_watches()
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                print(f"\n执行了 {len(results)} 个监控任务:")
                for r in results:
                    if r['success']:
                        print(f"  [OK] #{r['watch_id']}: {r['stored_count']} 条新结果")
                    else:
                        print(f"  [FAIL] #{r.get('watch_id', '?')}: {r.get('error', '未知错误')}")

        # 执行指定监控
        elif args.execute:
            result = mgr.execute_watch(args.execute)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                if result['success']:
                    print(f"[OK] 搜索完成: {result['stored_count']} 条新结果")
                else:
                    print(f"[FAIL] {result.get('error', '未知错误')}")

        # 获取已存储结果
        elif args.get_results:
            results = mgr.get_stored_results(
                keyword=args.keyword,
                hours=args.hours
            )
            if args.json:
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                print(f"\n共 {len(results)} 条结果:")
                for r in results[:20]:
                    print(f"  [{r['source_type']}] {r['title'][:50]}")
                    print(f"       时间: {r['published'][:16]}")

        # 一次性搜索
        elif args.keyword:
            sources = [s.strip() for s in args.sources.split(',')]
            result = mgr.search_and_store(args.keyword, sources, args.max)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"\n搜索: {args.keyword}")
                print(f"来源: {sources}")
                print(f"结果: {result['total_results']} 条 | 存储: {result['stored_count']} 条新")
                for r in result['results'][:10]:
                    print(f"  [{r['source_type']}] {r['title'][:50]}")

        else:
            parser.print_help()

    finally:
        mgr.close()