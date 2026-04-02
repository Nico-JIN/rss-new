#!/usr/bin/env python3
import sqlite3
import os
from pathlib import Path

# 获取数据库路径
BASE = Path(__file__).parent.parent
DB_PATH = BASE / "data" / "news.db"

def migrate():
    if not DB_PATH.exists():
        print(f"数据库文件不存在: {DB_PATH}")
        return

    print(f"正在连接数据库: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    try:
        # 1. 检查 'articles' 表中的列
        cursor.execute("PRAGMA table_info(articles)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'country' not in columns:
            print("正在添加 'country' 列到 'articles' 表...")
            cursor.execute("ALTER TABLE articles ADD COLUMN country TEXT DEFAULT ''")
            print("列 'country' 添加成功。")
        else:
            print("列 'country' 已存在，跳过。")

        # 2. 检查并添加索引
        print("正在检查索引...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_country ON articles(country)")
        print("索引 'idx_articles_country' 已确保存在。")

        conn.commit()
        print("\n数据库迁移/升级完成！")

    except Exception as e:
        print(f"迁移过程中发生错误: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
