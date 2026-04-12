#!/usr/bin/env python3
import yaml
import sqlite3
import os
from pathlib import Path

BASE = Path(__file__).parent.parent
DB_PATH = BASE / "data" / "news.db"
FEEDS_PATH = BASE / "config" / "feeds.yaml"

def migrate():
    if not FEEDS_PATH.exists():
        print(f"[ERROR] Feeds file not found: {FEEDS_PATH}")
        return

    if not DB_PATH.exists():
        print(f"[ERROR] DB file not found: {DB_PATH}")
        return

    print("[INFO] Loading feeds configuration...")
    with open(FEEDS_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    
    feeds = cfg.get("feeds", [])
    s_tier_platforms = [f["platform"] for f in feeds if f.get("is_s_tier")]
    
    print(f"[INFO] Found {len(s_tier_platforms)} S-tier feeds in config.")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    try:
        # 先全部重置为 B（保险起见，或者只更新匹配的）
        # c.execute("UPDATE articles SET media_tier = 'B'")
        
        updated_total = 0
        for platform in s_tier_platforms:
            c.execute("UPDATE articles SET media_tier = 'S' WHERE platform = ?", (platform,))
            count = c.rowcount
            if count > 0:
                print(f"  - Updated {count} articles for platform: {platform}")
                updated_total += count
        
        conn.commit()
        print(f"[SUCCESS] Migration complete. Total articles marked as S: {updated_total}")
        
        # 统计分布
        c.execute("SELECT media_tier, COUNT(*) FROM articles GROUP BY media_tier")
        stats = c.fetchall()
        print("[STATS] Media Tier distribution:")
        for tier, count in stats:
            print(f"  {tier}: {count}")
            
    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
