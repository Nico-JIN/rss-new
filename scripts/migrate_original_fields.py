#!/usr/bin/env python3
"""数据库迁移：添加原文字段

为 articles 表添加：
- original_title: 原文标题
- original_content: 原文内容

使用方法：
    python scripts/migrate_original_fields.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "news.db"


def migrate():
    """执行数据库迁移"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    new_columns = [
        ("original_title", "TEXT DEFAULT ''"),
        ("original_content", "TEXT DEFAULT ''"),
    ]

    for col_name, col_def in new_columns:
        try:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col_name} {col_def}")
            print(f"[OK] 添加字段: {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"[SKIP] 字段已存在: {col_name}")
            else:
                raise

    # 添加索引（可选，用于搜索原文）
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_original_title ON articles(original_title)")
        print("[OK] 添加索引: idx_articles_original_title")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()
    print("[DONE] 迁移完成")


if __name__ == "__main__":
    migrate()
