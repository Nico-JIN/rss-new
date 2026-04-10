"""SQLite 持久化存储层 — 文章入库 + 抓取日志 + 查询接口"""

import sqlite3
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ_BJ = timezone(timedelta(hours=8))
DB_PATH = Path(__file__).parent.parent / "data" / "news.db"


def _ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_conn(db_path=None):
    _ensure_dir()
    p = db_path or str(DB_PATH)
    # 增加超时时间(默认5秒可能不够)，允许多线程访问
    conn = sqlite3.connect(p, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")  # 30秒忙等待超时
    return conn


def _retry_on_locked(func, max_retries=5, delay=0.5):
    """数据库锁定时的重试包装器"""
    last_error = None
    for i in range(max_retries):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                last_error = e
                print(f"[WARN] 数据库锁定，重试 {i+1}/{max_retries}...")
                time.sleep(delay * (i + 1))  # 递增延迟
            else:
                raise
    raise last_error


def init_db(conn=None):
    """创建所有表和索引"""
    c = conn or get_conn()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash    TEXT UNIQUE,
            title_hash  TEXT,
            url         TEXT NOT NULL,
            title       TEXT NOT NULL,
            platform    TEXT,
            media_group TEXT,
            published   TEXT NOT NULL,
            summary     TEXT DEFAULT '',
            content     TEXT DEFAULT '',
            image       TEXT DEFAULT '',
            video       TEXT DEFAULT '',
            llm_tags    TEXT DEFAULT '[]',
            country     TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published);
        CREATE INDEX IF NOT EXISTS idx_articles_media_group ON articles(media_group);
        CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(title);
        CREATE INDEX IF NOT EXISTS idx_articles_url_hash ON articles(url_hash);
        CREATE INDEX IF NOT EXISTS idx_articles_country ON articles(country);


        CREATE TABLE IF NOT EXISTS fetch_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    TEXT NOT NULL,
            finished_at   TEXT,
            status        TEXT DEFAULT 'running',
            duration_sec  REAL,
            feeds_total   INTEGER DEFAULT 0,
            feeds_ok      INTEGER DEFAULT 0,
            feeds_failed  INTEGER DEFAULT 0,
            articles_new  INTEGER DEFAULT 0,
            articles_total INTEGER DEFAULT 0,
            failed_feeds  TEXT DEFAULT '[]',
            details       TEXT DEFAULT '{}',
            created_at    TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_fetch_logs_started ON fetch_logs(started_at);

        -- 时间线元信息表
        CREATE TABLE IF NOT EXISTS timelines (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            keywords        TEXT DEFAULT '[]',
            summary         TEXT DEFAULT '',
            status          TEXT DEFAULT 'active',
            created_at      TEXT DEFAULT (datetime('now', '+8 hours')),
            updated_at      TEXT DEFAULT (datetime('now', '+8 hours')),
            last_track_at   TEXT DEFAULT '',
            source_article_ids TEXT DEFAULT '[]'
        );

        -- 事件节点表
        CREATE TABLE IF NOT EXISTS timeline_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timeline_id     INTEGER NOT NULL,
            event_time      TEXT NOT NULL,
            title           TEXT NOT NULL,
            description     TEXT DEFAULT '',
            source_type     TEXT DEFAULT 'rss',
            source_url      TEXT DEFAULT '',
            source_platform TEXT DEFAULT '',
            source_article_id INTEGER DEFAULT NULL,
            is_key_event    INTEGER DEFAULT 0,
            importance      REAL DEFAULT 1.0,
            created_at      TEXT DEFAULT (datetime('now', '+8 hours')),
            FOREIGN KEY (timeline_id) REFERENCES timelines(id)
        );
        CREATE INDEX IF NOT EXISTS idx_timeline_events_timeline ON timeline_events(timeline_id);
        CREATE INDEX IF NOT EXISTS idx_timeline_events_time ON timeline_events(event_time);

        -- 发布物表
        CREATE TABLE IF NOT EXISTS publications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            pub_type        TEXT DEFAULT 'daily_digest',
            template_id     TEXT DEFAULT '',
            status          TEXT DEFAULT 'draft',
            content_md      TEXT DEFAULT '',
            content_html    TEXT DEFAULT '',
            source_hotspots TEXT DEFAULT '[]',
            source_articles TEXT DEFAULT '[]',
            author          TEXT DEFAULT 'system',
            reviewer        TEXT DEFAULT '',
            quality_score   REAL DEFAULT 0,
            quality_checks  TEXT DEFAULT '{}',
            version         INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now', '+8 hours')),
            updated_at      TEXT DEFAULT (datetime('now', '+8 hours')),
            published_at    TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_publications_status ON publications(status);
        CREATE INDEX IF NOT EXISTS idx_publications_type ON publications(pub_type);

        -- 发布物版本历史表
        CREATE TABLE IF NOT EXISTS publication_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pub_id          INTEGER NOT NULL,
            version         INTEGER NOT NULL,
            content_md      TEXT DEFAULT '',
            status          TEXT DEFAULT '',
            changed_by      TEXT DEFAULT '',
            change_note     TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now', '+8 hours')),
            FOREIGN KEY (pub_id) REFERENCES publications(id)
        );
        CREATE INDEX IF NOT EXISTS idx_pub_history_pub ON publication_history(pub_id);

        -- 关键词监控表
        CREATE TABLE IF NOT EXISTS keyword_watches (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword         TEXT NOT NULL,
            sources         TEXT DEFAULT '["google_news"]',
            interval        TEXT DEFAULT 'daily',
            enabled         INTEGER DEFAULT 1,
            last_search     TEXT DEFAULT '',
            created_at      TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_keyword_watches_enabled ON keyword_watches(enabled);

        -- 外部搜索结果表
        CREATE TABLE IF NOT EXISTS external_articles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash        TEXT UNIQUE,
            url             TEXT NOT NULL,
            title           TEXT NOT NULL,
            published       TEXT,
            platform        TEXT DEFAULT '',
            summary         TEXT DEFAULT '',
            source_type     TEXT DEFAULT '',
            keyword_match   TEXT DEFAULT '',
            raw_metadata    TEXT DEFAULT '{}',
            created_at      TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_external_articles_time ON external_articles(published);
        CREATE INDEX IF NOT EXISTS idx_external_articles_keyword ON external_articles(keyword_match);
        CREATE INDEX IF NOT EXISTS idx_external_articles_url_hash ON external_articles(url_hash);

        -- 定时热点检测结果表
        CREATE TABLE IF NOT EXISTS scheduled_hotspots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id     TEXT NOT NULL,
            category_name   TEXT NOT NULL,
            executed_at     TEXT NOT NULL,
            time_window_hours INTEGER,
            events          TEXT NOT NULL,
            event_count     INTEGER DEFAULT 0,
            article_count   INTEGER DEFAULT 0,
            keywords_used   TEXT,
            created_at      TEXT DEFAULT (datetime('now', '+8 hours'))
        );
        CREATE INDEX IF NOT EXISTS idx_scheduled_hotspots_category ON scheduled_hotspots(category_id);
        CREATE INDEX IF NOT EXISTS idx_scheduled_hotspots_executed ON scheduled_hotspots(executed_at);
    """)

    if conn is None:
        c.close()
    return c


# ── 文章操作 ──────────────────────────────────────────────


def upsert_articles(items: list[dict], conn=None):
    """
    批量写入文章。
    1. 使用 ON CONFLICT 处理 url_hash 冲突。
    2. 如果冲突且原记录无图，则尝试更新图片字段（补全历史记录）。
    3. 返回真正新增的条数。
    """
    def _insert():
        c = conn or get_conn()

        # 统计入库前的数量
        old_count = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

        # 确保基础字段存在
        for item in items:
            item.setdefault('content', '')
            item.setdefault('llm_tags', '[]')
            item.setdefault('video', '')

        # 使用 UPSERT 语法：冲突时如果旧记录没图，则更新图片
        sql = """
            INSERT INTO articles
                (url_hash, title_hash, url, title, platform, media_group, country, published, summary, content, image, video, llm_tags)
            VALUES
                (:url_hash, :title_hash, :url, :title, :platform, :media_group, :country, :published, :summary, :content, :image, :video, :llm_tags)
            ON CONFLICT(url_hash) DO UPDATE SET
                image = CASE WHEN (image IS NULL OR image = '') THEN excluded.image ELSE image END,
                video = CASE WHEN (video IS NULL OR video = '') THEN excluded.video ELSE video END
        """

        c.executemany(sql, items)
        c.commit()

        # 统计入库后的数量
        new_count = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        inserted = new_count - old_count

        if conn is None:
            c.close()
        return inserted

    return _retry_on_locked(_insert)


def query_by_time(start: str, end: str, media_group=None, platform=None, country=None, limit=200, offset=0, conn=None):
    """按时间范围查询。start/end 为 None 时不加时间过滤（全量查询）。"""
    c = conn or get_conn()
    sql = "SELECT * FROM articles WHERE 1=1"
    params = []
    if start:
        sql += " AND published >= ?"
        params.append(start)
    if end:
        sql += " AND published <= ?"
        params.append(end)
    if media_group:
        sql += " AND media_group = ?"
        params.append(media_group)
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    if country:
        sql += " AND country = ?"
        params.append(country)
    sql += " ORDER BY published DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(sql, params).fetchall()
    if conn is None:
        c.close()
    return [dict(r) for r in rows]



def query_by_keyword(keyword: str, start=None, end=None, media_group=None, platform=None, country=None,
                     limit=100, offset=0, conn=None):
    """
    按关键字模糊搜索（title + summary）

    注意：time_only 的源只按时间过滤，不匹配关键字
    这些源通常是关注特定地区的源（如联合早报、路透社China等）
    """
    c = conn or get_conn()
    like = f"%{keyword}%"

    # time_only 的源（只按时间过滤，不匹配关键字）
    # 这些源名称包含特定关键词
    time_only_patterns = [
        '%联合早报%',
        '%路透社%China%',
        '%纽约时报%China%',
        '%CNN%China%',
        '%南华早报%',
        '%德国之声%',
        '%路透社%Japan%',
        '%共同社%',
        '%印度时报%',
        '%加拿大广播%',
    ]

    # 构建 SQL：time_only 的源只按时间，其他源按时间+关键字
    time_only_sql = ' OR '.join([f"platform LIKE '{p}'" for p in time_only_patterns])

    sql = f"""SELECT * FROM articles WHERE published >= ? AND published <= ?
              AND (
                  ({time_only_sql})
                  OR title LIKE ?
                  OR summary LIKE ?
              )"""
    params = [start or '1970-01-01', end or '2099-12-31']

    # 关键字参数
    params.extend([like, like])

    if media_group:
        sql += " AND media_group = ?"
        params.append(media_group)
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    if country:
        sql += " AND country = ?"
        params.append(country)
    sql += " ORDER BY published DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(sql, params).fetchall()
    if conn is None:
        c.close()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d['llm_tags'] = json.loads(d.get('llm_tags') or '[]')
        except (json.JSONDecodeError, TypeError):
            d['llm_tags'] = []
        results.append(d)
    return results


def query_by_period(period='day', offset_n=0, media_group=None, platform=None, limit=200, conn=None):
    """按日/周/月查询。offset_n: 0=本期, -1=上一期, ..."""
    now = datetime.now(TZ_BJ)
    if period == 'day':
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = base + timedelta(days=offset_n)
        end = start + timedelta(days=1)
    elif period == 'week':
        base = now - timedelta(days=now.weekday())
        base = base.replace(hour=0, minute=0, second=0, microsecond=0)
        start = base + timedelta(weeks=offset_n)
        end = start + timedelta(weeks=1)
    elif period == 'month':
        y = now.year
        m = now.month + offset_n
        while m <= 0:
            y -= 1
            m += 12
        while m > 12:
            y += 1
            m -= 12
        start = datetime(y, m, 1, tzinfo=TZ_BJ)
        nm = m + 1
        ny = y
        if nm > 12:
            nm = 1
            ny += 1
        end = datetime(ny, nm, 1, tzinfo=TZ_BJ)
    else:
        raise ValueError(f"未知的 period: {period}")

    return query_by_time(start.isoformat(), end.isoformat(),
                         media_group=media_group, platform=platform, limit=limit, conn=conn)


def get_stats(conn=None):
    """数据库统计"""
    c = conn or get_conn()
    total = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    today_start = datetime.now(TZ_BJ).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()
    today = c.execute(
        "SELECT COUNT(*) FROM articles WHERE published >= ?",
        [today_start]).fetchone()[0]
    week_start = (datetime.now(TZ_BJ) - timedelta(days=datetime.now(TZ_BJ).weekday())
                  ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    this_week = c.execute(
        "SELECT COUNT(*) FROM articles WHERE published >= ?",
        [week_start]).fetchone()[0]


    by_media = c.execute(
        "SELECT media_group, COUNT(*) as cnt FROM articles GROUP BY media_group ORDER BY cnt DESC"
    ).fetchall()
    time_range = c.execute(
        "SELECT MIN(published) as earliest, MAX(published) as latest FROM articles"
    ).fetchone()

    result = {
        'total': total,
        'today': today,
        'this_week': this_week,
        'by_media': [dict(r) for r in by_media],
        'earliest': time_range['earliest'] if time_range else None,
        'latest': time_range['latest'] if time_range else None,
    }
    if conn is None:
        c.close()
    return result


def delete_before(date_str: str, conn=None):
    """删除指定日期之前的文章"""
    def _delete():
        c = conn or get_conn()
        deleted = c.execute(
            "DELETE FROM articles WHERE published < ?", [date_str]).rowcount
        c.commit()
        if conn is None:
            c.close()
        return deleted

    return _retry_on_locked(_delete)


def query_by_tag(tag: str, start=None, end=None, media_group=None, platform=None,
                 limit=100, offset=0, conn=None):
    """按 LLM 语义标签查询文章"""
    c = conn or get_conn()
    like = f'%"{tag}"%'
    sql = "SELECT * FROM articles WHERE llm_tags LIKE ?"
    params = [like]
    if start:
        sql += " AND published >= ?"
        params.append(start)
    if end:
        sql += " AND published <= ?"
        params.append(end)
    if media_group:
        sql += " AND media_group = ?"
        params.append(media_group)
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    sql += " ORDER BY published DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = c.execute(sql, params).fetchall()
    if conn is None:
        c.close()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d['llm_tags'] = json.loads(d.get('llm_tags') or '[]')
        except (json.JSONDecodeError, TypeError):
            d['llm_tags'] = []
        results.append(d)
    return results


def update_llm_tags(url_hash: str, tags: list, conn=None):
    """更新单篇文章的 LLM 标签"""
    def _update():
        c = conn or get_conn()
        c.execute(
            "UPDATE articles SET llm_tags = ? WHERE url_hash = ?",
            [json.dumps(tags, ensure_ascii=False), url_hash]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def update_article_content(article_id: int, content: str, published: str = None, conn=None):
    """更新文章内容和发布时间（通常用于 Jina 抓取后补全）"""
    def _update():
        c = conn or get_conn()
        if published:
            c.execute(
                "UPDATE articles SET content = ?, published = ? WHERE id = ?",
                [content, published, article_id]
            )
        else:
            c.execute(
                "UPDATE articles SET content = ? WHERE id = ?",
                [content, article_id]
            )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)

def count_articles(start=None, end=None, keyword=None, media_group=None, platform=None, country=None, conn=None):
    """计数查询（用于分页）"""
    c = conn or get_conn()
    sql = "SELECT COUNT(*) FROM articles WHERE 1=1"
    params = []
    if start:
        sql += " AND published >= ?"
        params.append(start)
    if end:
        sql += " AND published <= ?"
        params.append(end)
    if keyword:
        like = f"%{keyword}%"
        sql += " AND (title LIKE ? OR summary LIKE ?)"
        params.extend([like, like])
    if media_group:
        sql += " AND media_group = ?"
        params.append(media_group)
    if platform:
        sql += " AND platform = ?"
        params.append(platform)
    if country:
        sql += " AND country = ?"
        params.append(country)
    total = c.execute(sql, params).fetchone()[0]
    if conn is None:
        c.close()
    return total


# ── 抓取日志 ──────────────────────────────────────────────


def log_fetch_start(conn=None):
    """记录抓取开始，返回 log_id"""
    def _insert():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        cursor = c.execute(
            "INSERT INTO fetch_logs (started_at, status) VALUES (?, 'running')",
            [now])
        log_id = cursor.lastrowid
        c.commit()
        if conn is None:
            c.close()
        return log_id

    return _retry_on_locked(_insert)


def log_fetch_end(log_id: int, stats: dict, conn=None):
    """记录抓取完成"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        c.execute("""
            UPDATE fetch_logs SET
                finished_at = ?,
                status = ?,
                duration_sec = ?,
                feeds_total = ?,
                feeds_ok = ?,
                feeds_failed = ?,
                articles_new = ?,
                articles_total = ?,
                failed_feeds = ?,
                details = ?
            WHERE id = ?
        """, [
            now,
            stats.get('status', 'done'),
            stats.get('duration_sec', 0),
            stats.get('feeds_total', 0),
            stats.get('feeds_ok', 0),
            stats.get('feeds_failed', 0),
            stats.get('articles_new', 0),
            stats.get('articles_total', 0),
            json.dumps(stats.get('failed_feeds', []), ensure_ascii=False),
            json.dumps(stats.get('details', {}), ensure_ascii=False),
            log_id
        ])
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def get_fetch_logs(limit=20, conn=None):
    """获取最近的抓取日志"""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT * FROM fetch_logs ORDER BY id DESC LIMIT ?",
        [limit]).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['failed_feeds'] = json.loads(d.get('failed_feeds') or '[]')
        d['details'] = json.loads(d.get('details') or '{}')
        result.append(d)
    if conn is None:
        c.close()
    return result


def get_latest_fetch_status(conn=None):
    """获取最新一条抓取状态"""
    c = conn or get_conn()
    row = c.execute(
        "SELECT * FROM fetch_logs ORDER BY id DESC LIMIT 1").fetchone()
    if conn is None:
        c.close()
    if row:
        d = dict(row)
        d['failed_feeds'] = json.loads(d.get('failed_feeds') or '[]')
        d['details'] = json.loads(d.get('details') or '{}')
        return d
    return None


# ── 时间线操作 ──────────────────────────────────────────────


def create_timeline(title: str, keywords: list, source_article_ids: list,
                    summary: str = '', conn=None) -> int:
    """创建时间线，返回 timeline_id"""
    def _insert():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        cursor = c.execute(
            """INSERT INTO timelines
               (title, keywords, summary, source_article_ids, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [title, json.dumps(keywords, ensure_ascii=False), summary,
             json.dumps(source_article_ids, ensure_ascii=False), now, now]
        )
        timeline_id = cursor.lastrowid
        c.commit()
        if conn is None:
            c.close()
        return timeline_id

    return _retry_on_locked(_insert)


def get_timeline(timeline_id: int, conn=None) -> dict:
    """获取单条时间线及其事件"""
    c = conn or get_conn()
    row = c.execute("SELECT * FROM timelines WHERE id = ?", [timeline_id]).fetchone()
    if not row:
        if conn is None:
            c.close()
        return None

    timeline = dict(row)
    timeline['keywords'] = json.loads(timeline.get('keywords') or '[]')
    timeline['source_article_ids'] = json.loads(timeline.get('source_article_ids') or '[]')

    # 获取事件列表（按时间升序）
    event_rows = c.execute(
        "SELECT * FROM timeline_events WHERE timeline_id = ? ORDER BY event_time ASC",
        [timeline_id]
    ).fetchall()
    timeline['events'] = [dict(e) for e in event_rows]

    if conn is None:
        c.close()
    return timeline


def list_timelines(status=None, limit=50, conn=None) -> list:
    """获取时间线列表"""
    c = conn or get_conn()
    sql = "SELECT * FROM timelines"
    params = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    rows = c.execute(sql, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d['keywords'] = json.loads(d.get('keywords') or '[]')
        d['source_article_ids'] = json.loads(d.get('source_article_ids') or '[]')
        # 获取事件数量
        event_count = c.execute(
            "SELECT COUNT(*) FROM timeline_events WHERE timeline_id = ?",
            [d['id']]
        ).fetchone()[0]
        d['event_count'] = event_count
        results.append(d)

    if conn is None:
        c.close()
    return results


def add_timeline_event(timeline_id: int, event_data: dict, conn=None) -> int:
    """添加事件节点"""
    def _insert():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        cursor = c.execute(
            """INSERT INTO timeline_events
               (timeline_id, event_time, title, description, source_type,
                source_url, source_platform, source_article_id, is_key_event,
                importance, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [timeline_id, event_data.get('event_time', now),
             event_data.get('title', ''), event_data.get('description', ''),
             event_data.get('source_type', 'rss'), event_data.get('source_url', ''),
             event_data.get('source_platform', ''), event_data.get('source_article_id'),
             event_data.get('is_key_event', 0), event_data.get('importance', 1.0), now]
        )
        event_id = cursor.lastrowid
        c.commit()
        # 更新时间线的 updated_at
        c.execute("UPDATE timelines SET updated_at = ? WHERE id = ?", [now, timeline_id])
        c.commit()
        if conn is None:
            c.close()
        return event_id

    return _retry_on_locked(_insert)


def update_timeline_events(timeline_id: int, events: list, conn=None):
    """批量更新事件（先删除旧事件，再插入新事件）"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        # 删除旧事件
        c.execute("DELETE FROM timeline_events WHERE timeline_id = ?", [timeline_id])
        # 插入新事件
        for e in events:
            c.execute(
                """INSERT INTO timeline_events
                   (timeline_id, event_time, title, description, source_type,
                    source_url, source_platform, source_article_id, is_key_event,
                    importance, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [timeline_id, e.get('event_time', now),
                 e.get('title', ''), e.get('description', ''),
                 e.get('source_type', 'rss'), e.get('source_url', ''),
                 e.get('source_platform', ''), e.get('source_article_id'),
                 e.get('is_key_event', 0), e.get('importance', 1.0), now]
            )
        # 更新时间线的 updated_at
        c.execute("UPDATE timelines SET updated_at = ? WHERE id = ?", [now, timeline_id])
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def update_timeline_summary(timeline_id: int, title: str, summary: str,
                            keywords: list, conn=None):
    """更新时间线元信息"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        c.execute(
            """UPDATE timelines SET title = ?, summary = ?, keywords = ?, updated_at = ?
               WHERE id = ?""",
            [title, summary, json.dumps(keywords, ensure_ascii=False), now, timeline_id]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def update_timeline_track_time(timeline_id: int, conn=None):
    """更新时间线的跟踪时间"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        c.execute(
            "UPDATE timelines SET last_track_at = ?, updated_at = ? WHERE id = ?",
            [now, now, timeline_id]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def set_timeline_status(timeline_id: int, status: str, conn=None):
    """设置时间线状态 (active/archived/completed)"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        c.execute(
            "UPDATE timelines SET status = ?, updated_at = ? WHERE id = ?",
            [status, now, timeline_id]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def delete_timeline(timeline_id: int, conn=None):
    """删除时间线及其所有事件"""
    def _delete():
        c = conn or get_conn()
        c.execute("DELETE FROM timeline_events WHERE timeline_id = ?", [timeline_id])
        c.execute("DELETE FROM timelines WHERE id = ?", [timeline_id])
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_delete)


def get_articles_by_ids(article_ids: list, conn=None) -> list:
    """根据ID列表获取文章详情"""
    if not article_ids:
        return []
    c = conn or get_conn()
    # 过滤无效ID
    valid_ids = [int(id) for id in article_ids if id and str(id) not in ('null', 'None', 'undefined')]
    if not valid_ids:
        if conn is None:
            c.close()
        return []

    placeholders = ','.join(['?' for _ in valid_ids])
    rows = c.execute(
        f"SELECT * FROM articles WHERE id IN ({placeholders})",
        valid_ids
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d['llm_tags'] = json.loads(d.get('llm_tags') or '[]')
        except (json.JSONDecodeError, TypeError):
            d['llm_tags'] = []
        results.append(d)

    if conn is None:
        c.close()
    return results


# ═══════════════════════════════════════════════════════════════════
# 发布物操作
# ═══════════════════════════════════════════════════════════════════

def create_publication(title: str, pub_type: str, template_id: str = '',
                       source_hotspots: list = None, source_articles: list = None,
                       author: str = 'system', conn=None) -> int:
    """创建发布物，返回 publication_id"""
    def _insert():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        cursor = c.execute(
            """INSERT INTO publications
               (title, pub_type, template_id, source_hotspots, source_articles, author, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [title, pub_type, template_id,
             json.dumps(source_hotspots or [], ensure_ascii=False),
             json.dumps(source_articles or [], ensure_ascii=False),
             author, now, now]
        )
        pub_id = cursor.lastrowid
        c.commit()
        if conn is None:
            c.close()
        return pub_id

    return _retry_on_locked(_insert)


def get_publication(pub_id: int, conn=None) -> dict:
    """获取单条发布物"""
    c = conn or get_conn()
    row = c.execute("SELECT * FROM publications WHERE id = ?", [pub_id]).fetchone()
    if not row:
        if conn is None:
            c.close()
        return None

    pub = dict(row)
    pub['source_hotspots'] = json.loads(pub.get('source_hotspots') or '[]')
    pub['source_articles'] = json.loads(pub.get('source_articles') or '[]')
    pub['quality_checks'] = json.loads(pub.get('quality_checks') or '{}')

    if conn is None:
        c.close()
    return pub


def list_publications(status: str = None, pub_type: str = None,
                      limit: int = 50, conn=None) -> list:
    """获取发布物列表"""
    c = conn or get_conn()
    sql = "SELECT * FROM publications WHERE 1=1"
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if pub_type:
        sql += " AND pub_type = ?"
        params.append(pub_type)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    rows = c.execute(sql, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d['source_hotspots'] = json.loads(d.get('source_hotspots') or '[]')
        d['source_articles'] = json.loads(d.get('source_articles') or '[]')
        d['quality_checks'] = json.loads(d.get('quality_checks') or '{}')
        results.append(d)

    if conn is None:
        c.close()
    return results


def update_publication_content(pub_id: int, content_md: str,
                               content_html: str = '', conn=None):
    """更新发布物内容"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        c.execute(
            """UPDATE publications SET
               content_md = ?, content_html = ?, updated_at = ?
               WHERE id = ?""",
            [content_md, content_html, now, pub_id]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def update_publication_status(pub_id: int, new_status: str,
                              reviewer: str = '', conn=None):
    """更新发布物状态"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        published_at = now if new_status == 'published' else ''
        c.execute(
            """UPDATE publications SET
               status = ?, reviewer = ?, updated_at = ?, published_at = COALESCE(?, published_at)
               WHERE id = ?""",
            [new_status, reviewer, now, published_at or None, pub_id]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def update_publication_quality(pub_id: int, quality_score: float,
                               quality_checks: dict, conn=None):
    """更新发布物质量评分"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        c.execute(
            """UPDATE publications SET
               quality_score = ?, quality_checks = ?, updated_at = ?
               WHERE id = ?""",
            [quality_score, json.dumps(quality_checks, ensure_ascii=False), now, pub_id]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def delete_publication(pub_id: int, conn=None):
    """删除发布物"""
    def _delete():
        c = conn or get_conn()
        c.execute("DELETE FROM publication_history WHERE pub_id = ?", [pub_id])
        c.execute("DELETE FROM publications WHERE id = ?", [pub_id])
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_delete)


def create_publication_history(pub_id: int, version: int, content_md: str,
                               status: str, changed_by: str = '',
                               change_note: str = '', conn=None):
    """创建发布物版本历史"""
    def _insert():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        c.execute(
            """INSERT INTO publication_history
               (pub_id, version, content_md, status, changed_by, change_note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [pub_id, version, content_md, status, changed_by, change_note, now]
        )
        # 更新版本号
        c.execute("UPDATE publications SET version = version + 1 WHERE id = ?", [pub_id])
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_insert)


def get_publication_history(pub_id: int, conn=None) -> list:
    """获取发布物版本历史"""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT * FROM publication_history WHERE pub_id = ? ORDER BY version DESC",
        [pub_id]
    ).fetchall()
    results = [dict(r) for r in rows]
    if conn is None:
        c.close()
    return results


# ═══════════════════════════════════════════════════════════════════
# 关键词监控操作
# ═══════════════════════════════════════════════════════════════════

def create_keyword_watch(keyword: str, sources: list,
                         interval: str = 'daily', conn=None) -> int:
    """创建关键词监控任务"""
    def _insert():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        cursor = c.execute(
            """INSERT INTO keyword_watches
               (keyword, sources, interval, enabled, created_at)
               VALUES (?, ?, ?, 1, ?)""",
            [keyword, json.dumps(sources, ensure_ascii=False), interval, now]
        )
        watch_id = cursor.lastrowid
        c.commit()
        if conn is None:
            c.close()
        return watch_id

    return _retry_on_locked(_insert)


def list_keyword_watches(enabled_only: bool = True, conn=None) -> list:
    """列出关键词监控任务"""
    c = conn or get_conn()
    sql = "SELECT * FROM keyword_watches"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id DESC"

    rows = c.execute(sql).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d['sources'] = json.loads(d.get('sources') or '["google_news"]')
        results.append(d)

    if conn is None:
        c.close()
    return results


def set_keyword_watch_enabled(watch_id: int, enabled: bool, conn=None):
    """启用/禁用关键词监控"""
    def _update():
        c = conn or get_conn()
        c.execute(
            "UPDATE keyword_watches SET enabled = ? WHERE id = ?",
            [1 if enabled else 0, watch_id]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


def delete_keyword_watch(watch_id: int, conn=None):
    """删除关键词监控"""
    def _delete():
        c = conn or get_conn()
        c.execute("DELETE FROM keyword_watches WHERE id = ?", [watch_id])
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_delete)


def update_keyword_watch_last_search(watch_id: int, conn=None):
    """更新关键词监控的最后搜索时间"""
    def _update():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        c.execute(
            "UPDATE keyword_watches SET last_search = ? WHERE id = ?",
            [now, watch_id]
        )
        c.commit()
        if conn is None:
            c.close()

    return _retry_on_locked(_update)


# ═══════════════════════════════════════════════════════════════════
# 外部搜索结果操作
# ═══════════════════════════════════════════════════════════════════

def upsert_external_articles(articles: list, keyword_match: str = '', conn=None) -> int:
    """批量写入外部搜索结果，url_hash 冲突忽略"""
    def _insert():
        c = conn or get_conn()
        now = datetime.now(TZ_BJ).isoformat()
        inserted = 0
        for a in articles:
            url_hash = hashlib.sha1(a.get('url', '').encode()).hexdigest()[:16]
            try:
                c.execute(
                    """INSERT OR IGNORE INTO external_articles
                       (url_hash, url, title, published, platform, summary, source_type, keyword_match, raw_metadata, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [url_hash, a.get('url', ''), a.get('title', ''),
                     a.get('published', now), a.get('platform', ''),
                     a.get('summary', ''), a.get('source_type', ''),
                     keyword_match, json.dumps(a.get('raw_metadata', {}), ensure_ascii=False), now]
                )
                if c.rowcount > 0:
                    inserted += 1
            except:
                pass
        c.commit()
        if conn is None:
            c.close()
        return inserted

    import hashlib
    return _retry_on_locked(_insert)


def query_external_articles(keyword: str = None, source_type: str = None,
                            start: str = None, limit: int = 50, conn=None) -> list:
    """查询外部搜索结果"""
    c = conn or get_conn()
    sql = "SELECT * FROM external_articles WHERE 1=1"
    params = []
    if keyword:
        sql += " AND keyword_match LIKE ?"
        params.append(f"%{keyword}%")
    if source_type:
        sql += " AND source_type = ?"
        params.append(source_type)
    if start:
        sql += " AND published >= ?"
        params.append(start)
    sql += " ORDER BY published DESC LIMIT ?"
    params.append(limit)

    rows = c.execute(sql, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d['raw_metadata'] = json.loads(d.get('raw_metadata') or '{}')
        results.append(d)

    if conn is None:
        c.close()
    return results
