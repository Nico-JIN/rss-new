#!/usr/bin/env python3
"""
热点检测结果表迁移脚本
执行此脚本将在 news.db 中创建 hotspots 表

用法：
    python scripts/migrate_hotspots.py
"""

import sqlite3
from pathlib import Path

# 数据库路径
DB_PATH = Path(__file__).parent.parent / "data" / "news.db"


def migrate():
    print(f"[INFO] 数据库路径: {DB_PATH}")

    if not DB_PATH.exists():
        print(f"[ERROR] 数据库不存在: {DB_PATH}")
        return False

    conn = sqlite3.connect(DB_PATH)

    try:
        # 检查表是否已存在
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hotspots'"
        ).fetchone()

        if existing:
            print("[INFO] hotspots 表已存在，跳过创建")
            return True

        # 创建 hotspots 表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hotspots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                score REAL,
                article_count INTEGER,
                media_count INTEGER,
                category TEXT,
                cluster_indices TEXT,
                entities TEXT,
                summary TEXT,
                importance TEXT DEFAULT 'medium',
                first_published TEXT,
                latest_published TEXT,
                platforms TEXT,
                article_ids TEXT,
                is_s_tier_triggered INTEGER DEFAULT 0,
                detected_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建索引
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hotspots_category ON hotspots(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hotspots_detected ON hotspots(detected_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hotspots_score ON hotspots(score DESC)")

        conn.commit()
        print("[SUCCESS] hotspots 表创建成功")

        # 显示表结构
        print("\n表结构:")
        columns = conn.execute("PRAGMA table_info(hotspots)").fetchall()
        for col in columns:
            print(f"  {col[1]:20s} {col[2]:10s} {'NOT NULL' if col[3] else ''}")

        return True

    except Exception as e:
        print(f"[ERROR] 创建表失败: {e}")
        conn.rollback()
        return False

    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    if success:
        print("\n迁移完成！可以继续后续模块开发。")
    else:
        print("\n迁移失败，请检查错误信息。")