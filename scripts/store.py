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
    """创建所有表和索引，并处理潜在的数据迁移"""
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
    """)
    
    # 动态迁移检测
    cursor = c.execute("PRAGMA table_info(articles)")
    columns = [row['name'] for row in cursor.fetchall()]
    if 'content' not in columns:
        c.execute("ALTER TABLE articles ADD COLUMN content TEXT DEFAULT ''")
        c.commit()
    if 'llm_tags' not in columns:
        c.execute("ALTER TABLE articles ADD COLUMN llm_tags TEXT DEFAULT '[]'")
        c.commit()
    if 'country' not in columns:
        c.execute("ALTER TABLE articles ADD COLUMN country TEXT DEFAULT ''")
        c.commit()
        
    if conn is None:
        c.close()
    return c


# ── 文章操作 ──────────────────────────────────────────────


def upsert_articles(items: list[dict], conn=None):
    """批量写入文章，url_hash 冲突忽略。返回实际新增条数。"""
    def _insert():
        c = conn or get_conn()

        # Ensure all items have a 'content' field defaulting to empty string
        for item in items:
            if 'content' not in item:
                item['content'] = ''

        for item in items:
            if 'llm_tags' not in item:
                item['llm_tags'] = '[]'

        sql = """
            INSERT OR IGNORE INTO articles
                (url_hash, title_hash, url, title, platform, media_group, country, published, summary, content, image, llm_tags)
            VALUES
                (:url_hash, :title_hash, :url, :title, :platform, :media_group, :country, :published, :summary, :content, :image, :llm_tags)
        """
        cursor = c.executemany(sql, items)
        inserted = cursor.rowcount
        c.commit()
        if conn is None:
            c.close()
        return inserted

    return _retry_on_locked(_insert)


def query_by_time(start: str, end: str, media_group=None, platform=None, country=None, limit=200, offset=0, conn=None):
    """按时间范围查询"""
    c = conn or get_conn()
    sql = "SELECT * FROM articles WHERE published >= ? AND published <= ?"
    params = [start, end]
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
    """按关键字模糊搜索（title + summary）"""
    c = conn or get_conn()
    like = f"%{keyword}%"
    sql = "SELECT * FROM articles WHERE (title LIKE ? OR summary LIKE ?)"
    params = [like, like]
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
