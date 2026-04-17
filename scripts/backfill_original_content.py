#!/usr/bin/env python3
"""原文补抓任务 - 凌晨定时运行

功能：
1. 检查最近N天缺少原文的文章
2. 使用三层策略抓取：
   - Jina Reader 优先
   - Trafilatura 兜底
   - 直接HTTP + 特殊选择器
3. 更新数据库
4. 记录执行日志

运行方式：
    python scripts/backfill_original_content.py [--days 1] [--limit 500] [--trigger manual]

定时任务：
    每天凌晨 2:30 运行
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
import sqlite3

# 尝试导入 trafilatura
try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False
    print("[WARN] trafilatura 未安装，部分源可能无法抓取")

sys.path.insert(0, str(Path(__file__).parent))

from store import get_conn, init_db

DB_PATH = Path(__file__).parent.parent / "data" / "news.db"


# ═══════════════════════════════════════════════════════════════════════════════
# 执行日志
# ═══════════════════════════════════════════════════════════════════════════════

def create_backfill_log(conn, days_back: int, trigger_type: str = 'scheduled') -> int:
    """创建执行日志记录，返回 log_id"""
    cursor = conn.execute('''
        INSERT INTO backfill_logs (started_at, status, days_back, trigger_type)
        VALUES (?, 'running', ?, ?)
    ''', (datetime.now().isoformat(), days_back, trigger_type))
    conn.commit()
    return cursor.lastrowid


def update_backfill_log(conn, log_id: int, stats: dict, error_msg: str = ''):
    """更新执行日志"""
    finished_at = datetime.now().isoformat()
    duration = stats.get('duration_seconds', 0)

    conn.execute('''
        UPDATE backfill_logs
        SET finished_at = ?, status = ?, duration_seconds = ?,
            total_articles = ?, success_count = ?, failed_count = ?, skipped_count = ?,
            by_method = ?, by_platform = ?, error_message = ?
        WHERE id = ?
    ''', (
        finished_at,
        'success' if stats.get('success', 0) > 0 or stats.get('failed', 0) == 0 else 'failed',
        duration,
        stats.get('total', 0),
        stats.get('success', 0),
        stats.get('failed', 0),
        stats.get('skipped', 0),
        json.dumps(stats.get('by_method', {}), ensure_ascii=False),
        json.dumps(stats.get('by_platform', {}), ensure_ascii=False),
        error_msg,
        log_id
    ))
    conn.commit()


def get_backfill_logs(conn, limit: int = 20) -> list:
    """获取执行日志列表"""
    cursor = conn.execute('''
        SELECT * FROM backfill_logs
        ORDER BY started_at DESC
        LIMIT ?
    ''', (limit,))
    return [dict(row) for row in cursor.fetchall()]

# ═══════════════════════════════════════════════════════════════════════════════
# 抓取策略
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_with_jina(url: str, timeout: int = 20) -> tuple[bool, str, int]:
    """使用 Jina Reader 抓取"""
    jina_url = f"https://r.jina.ai/{url}"
    try:
        resp = requests.get(jina_url, timeout=timeout)
        if resp.status_code == 200:
            text = resp.text or ''
            if len(text) > 100:
                # 清洗内容
                text = re.sub(r'\n{3,}', '\n\n', text)
                return True, text, len(text)
        return False, f"HTTP {resp.status_code}", 0
    except Exception as e:
        return False, str(e)[:50], 0


def fetch_with_trafilatura(url: str) -> tuple[bool, str, str, int]:
    """使用 Trafilatura 抓取，同时提取标题"""
    if not HAS_TRAFILATURA:
        return False, "", "", 0

    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            # 提取元数据（包含标题）
            metadata = trafilatura.extract_metadata(downloaded)
            orig_title = metadata.title if metadata else ""

            # 提取正文
            text = trafilatura.extract(downloaded)
            if text and len(text) > 50:
                return True, text, orig_title, len(text)
        return False, "", "", 0
    except Exception as e:
        return False, "", "", 0


def fetch_with_http(url: str, platform: str = "") -> tuple[bool, str, str, int]:
    """直接 HTTP 请求 + HTML 解析"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            return False, "", "", 0

        html = resp.text
        content = ""
        orig_title = ""

        # 提取原文标题
        title_match = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if title_match:
            orig_title = title_match.group(1).strip()

        # 根据平台选择不同的提取策略
        if 'rthk.hk' in url or '香港电台' in platform:
            # 香港电台特殊处理 - 使用 itemFullText 类
            match = re.search(r'<div[^>]*class=["\']itemFullText["\'][^>]*>(.*?)</div>', html, re.DOTALL)
            if match:
                content = re.sub(r'<[^>]+>', ' ', match.group(1))
                content = re.sub(r'\s+', ' ', content).strip()
        else:
            # 通用提取：article 标签
            match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL | re.IGNORECASE)
            if match:
                content = re.sub(r'<[^>]+>', ' ', match.group(1))

            # 如果没找到，尝试 p 标签
            if not content or len(content) < 100:
                p_contents = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL | re.IGNORECASE)
                if p_contents:
                    content = ' '.join(re.sub(r'<[^>]+>', '', p) for p in p_contents[:15])

        if content:
            content = re.sub(r'\s+', ' ', content).strip()
            if len(content) > 50:
                return True, content, orig_title, len(content)

        return False, "", "", 0
    except Exception as e:
        return False, "", "", 0


def extract_original_title(html: str) -> str:
    """从 HTML 中提取原文标题"""
    import re

    # 1. 尝试 og:title
    match = re.search(r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 2. 尝试 <title>
    match = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    if match:
        title = match.group(1).strip()
        # 清理常见的网站名后缀
        title = re.sub(r'\s*[-|]\s*[^-|]+$', '', title)
        return title

    return ""


def fetch_content(url: str, platform: str = "") -> tuple[bool, str, str, str]:
    """
    三层抓取策略

    Returns:
        (success, content, original_title, method_used)
    """
    # 跳过 X.com (需要 JavaScript 渲染)
    is_x_com = 'x.com' in url or 'twitter.com' in url
    if is_x_com:
        return False, "X.com 需要 JavaScript 渲染，跳过", "", ""

    # 1. Jina Reader 优先
    success, result, length = fetch_with_jina(url)
    if success:
        # Jina 返回的内容中提取标题（第一行通常是标题）
        lines = result.strip().split('\n')
        orig_title = lines[0] if lines else ""
        return True, result, orig_title, "jina"

    jina_error = result

    # 2. Trafilatura 兜底 (针对 451 限制)
    if HAS_TRAFILATURA:
        success, result, trafi_title, length = fetch_with_trafilatura(url)
        if success:
            return True, result, trafi_title, "trafilatura"

    # 3. 直接 HTTP (最后手段)
    success, result, http_title, length = fetch_with_http(url, platform)
    if success:
        return True, result, http_title, "http"

    return False, f"Jina: {jina_error}", "", ""


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════

def get_articles_to_backfill(conn, days: int, limit: int) -> list:
    """获取需要补抓的文章列表"""
    cutoff = datetime.now() - timedelta(days=days)

    cursor = conn.execute('''
        SELECT id, url, title, platform, original_title, original_content, content
        FROM articles
        WHERE published >= ?
        AND (
            original_title = '' OR original_title IS NULL
            OR original_content = '' OR original_content IS NULL
            OR content = '' OR content IS NULL OR LENGTH(content) < 50
        )
        ORDER BY published DESC
        LIMIT ?
    ''', (cutoff.isoformat(), limit))

    return [dict(row) for row in cursor.fetchall()]


def update_article_content(conn, article_id: int, original_title: str, original_content: str, content: str):
    """更新文章的原文和内容"""
    conn.execute('''
        UPDATE articles
        SET original_title = ?, original_content = ?, content = ?
        WHERE id = ?
    ''', (original_title, original_content, content, article_id))
    conn.commit()


def run_backfill(days: int = 1, limit: int = 500, trigger_type: str = 'scheduled') -> dict:
    """
    执行补抓任务（可被API调用）

    Args:
        days: 补抓最近N天
        limit: 最大处理文章数
        trigger_type: 触发类型 (scheduled/manual)

    Returns:
        统计结果字典
    """
    start_time = datetime.now()

    # 连接数据库
    conn = get_conn()
    init_db(conn)
    conn.row_factory = sqlite3.Row

    # 创建执行日志
    log_id = create_backfill_log(conn, days, trigger_type)

    # 获取需要补抓的文章
    articles = get_articles_to_backfill(conn, days, limit)

    if not articles:
        stats = {
            'total': 0, 'success': 0, 'failed': 0, 'skipped': 0,
            'duration_seconds': 0, 'by_method': {}, 'by_platform': {}
        }
        update_backfill_log(conn, log_id, stats)
        conn.close()
        return stats

    # 统计
    stats = {
        'total': len(articles),
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'by_method': {'jina': 0, 'trafilatura': 0, 'http': 0},
        'by_platform': {},
    }

    # 逐篇处理
    for article in articles:
        article_id = article['id']
        url = article['url']
        title = article['title']
        platform = article['platform']

        # 抓取内容
        success, result, orig_title, method = fetch_content(url, platform)

        if success:
            if not orig_title:
                orig_title = title
            update_article_content(conn, article_id, orig_title, result, result)

            stats['success'] += 1
            stats['by_method'][method] = stats['by_method'].get(method, 0) + 1
            stats['by_platform'][platform] = stats['by_platform'].get(platform, {'success': 0, 'failed': 0})
            stats['by_platform'][platform]['success'] += 1
        else:
            # 判断是否是跳过（X.com）
            if 'X.com' in result or 'JavaScript' in result:
                stats['skipped'] += 1
            else:
                stats['failed'] += 1
            stats['by_platform'][platform] = stats['by_platform'].get(platform, {'success': 0, 'failed': 0})
            stats['by_platform'][platform]['failed'] += 1

        time.sleep(0.3)

    # 计算耗时
    stats['duration_seconds'] = int((datetime.now() - start_time).total_seconds())

    # 更新日志
    update_backfill_log(conn, log_id, stats)
    conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(description='原文补抓任务')
    parser.add_argument('--days', type=int, default=1, help='补抓最近N天的数据 (默认1天)')
    parser.add_argument('--limit', type=int, default=500, help='最大处理文章数 (默认500)')
    parser.add_argument('--dry-run', action='store_true', help='只检查不执行')
    parser.add_argument('--trigger', type=str, default='manual', help='触发类型 (manual/scheduled)')
    args = parser.parse_args()

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 原文补抓任务启动")
    print(f"  参数: days={args.days}, limit={args.limit}, dry_run={args.dry_run}")

    # 连接数据库
    conn = get_conn()
    init_db(conn)
    conn.row_factory = sqlite3.Row

    # 获取需要补抓的文章
    articles = get_articles_to_backfill(conn, args.days, args.limit)
    print(f"[INFO] 找到 {len(articles)} 篇需要补抓的文章")

    if args.dry_run:
        print("\n[DRY-RUN] 文章列表:")
        for a in articles[:10]:
            print(f"  - [{a['platform']}] {a['title'][:40]}...")
        if len(articles) > 10:
            print(f"  ... 还有 {len(articles) - 10} 篇")
        conn.close()
        return

    if not articles:
        print("[INFO] 没有需要补抓的文章")
        conn.close()
        return

    conn.close()

    # 执行补抓
    stats = run_backfill(args.days, args.limit, args.trigger)

    # 输出统计
    print("\n" + "=" * 60)
    print("补抓完成统计")
    print("=" * 60)
    print(f"总计: {stats['total']} 篇")
    print(f"成功: {stats['success']} 篇")
    print(f"失败: {stats['failed']} 篇")
    print(f"跳过: {stats['skipped']} 篇")
    print(f"耗时: {stats['duration_seconds']} 秒")
    print()
    print("按方法统计:")
    for method, count in stats['by_method'].items():
        if count > 0:
            print(f"  - {method}: {count} 篇")
    print()
    print("按平台统计 (失败数>0):")
    for platform, pstats in sorted(stats['by_platform'].items(), key=lambda x: -x[1]['failed']):
        if pstats['failed'] > 0:
            print(f"  - {platform}: 成功{pstats['success']}, 失败{pstats['failed']}")


if __name__ == "__main__":
    main()
