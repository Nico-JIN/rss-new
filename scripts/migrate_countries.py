#!/usr/bin/env python3
import sqlite3
import json
from pathlib import Path

BASE = Path(__file__).parent.parent
DB_PATH = BASE / "data" / "news.db"

RULES = {
    '新加坡': ['zaobao', '联合早报', 'singapore'],
    '美国': ['pbs', 'cnn', 'washingtonpost', '华盛顿邮报', '纽约时报', 'nytimes', 'bloomberg', '彭博', 'us', 'usa', 'america', '众议院'],
    '英国': ['reuters', '路透社', 'bbc', 'guardian', '卫报', 'ft', '金融时报', 'uk'],
    '日本': ['nhk', '共同社', 'kyodo', 'nikkei', '日经', 'japan', '共同网'],
    '韩国': ['韩联社', 'yonhap', 'korea'],
    '俄罗斯': ['sputnik', '俄罗斯卫星', 'russia'],
    '中国': ['cctv', '新华', 'xinhua', 'china'],
    '中国香港': ['scmp', '南华早报', 'hong kong', 'hk', '端传媒'],
    '中国台湾': ['中央社', 'cna', 'taiwan', 'tw'],
    '德国': ['dw', '德声', 'germany'],
    '法国': ['rfi', '法广', 'france'],
}

def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 找出所有 country 为空的文章
    rows = cursor.execute("SELECT id, platform, media_group FROM articles WHERE country = '' OR country IS NULL").fetchall()
    print(f"Total articles to fill: {len(rows)}")
    
    updated = 0
    for row in rows:
        platform = (row['platform'] or '').lower()
        media = (row['media_group'] or '').lower()
        
        target_country = ''
        found = False
        for country, keywords in RULES.items():
            for kw in keywords:
                if kw in platform or kw in media:
                    target_country = country
                    found = True
                    break
            if found: break
        
        if target_country:
            cursor.execute("UPDATE articles SET country = ? WHERE id = ?", (target_country, row['id']))
            updated += 1
            
    conn.commit()
    conn.close()
    print(f"Migration finished. Updated {updated} rows.")

if __name__ == '__main__':
    migrate()
