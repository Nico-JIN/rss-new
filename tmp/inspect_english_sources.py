
import sqlite3
import re
import sys
import os
from pathlib import Path

# Add project root to path
ROOT = Path(r"c:\Users\76539\.openclaw\skills\rss-news")
sys.path.append(str(ROOT / "scripts"))

# Import get_conn
import sqlite3
DB_PATH = ROOT / "data" / "news.db"

def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def is_mostly_english(text):
    if not text:
        return False
    # Characters and Chinese
    eng_chars = len(re.findall(r'[a-zA-Z]', text))
    cjk_chars = len(re.findall(r'[\u4e00-\u9fa5]', text))
    
    # Heuristic: mostly English if more than double the Chinese
    if eng_chars > cjk_chars * 2 and eng_chars > 5:
        return True
    return False

def inspect():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = get_conn()
    try:
        rows = conn.execute("SELECT platform, title FROM articles ORDER BY published DESC LIMIT 5000").fetchall()
    except Exception as e:
        print(f"Query error: {e}")
        return
    finally:
        conn.close()
    
    platform_stats = {}
    
    for row in rows:
        platform = row['platform']
        title = row['title']
        
        if platform not in platform_stats:
            platform_stats[platform] = {'total': 0, 'english': 0}
            
        platform_stats[platform]['total'] += 1
        if is_mostly_english(String(title)):
            platform_stats[platform]['english'] += 1
            
    # Filter platforms that have a significant portion or number of English titles
    results = []
    for platform, stats in platform_stats.items():
        if stats['english'] > 0:
            ratio = stats['english'] / stats['total']
            results.append({
                'platform': str(platform),
                'english_count': stats['english'],
                'total_checked': stats['total'],
                'ratio': ratio
            })
            
    # Sort by ratio descending
    results.sort(key=lambda x: x['ratio'], reverse=True)
    
    print(f"{'Platform':<30} | {'Eng':<5} | {'Total':<5} | {'Ratio':<6}")
    print("-" * 55)
    for res in results:
        print(f"{res['platform']:<30} | {res['english_count']:<5} | {res['total_checked']:<5} | {res['ratio']:>6.1%}")

def String(val):
    if val is None: return ""
    return str(val)

if __name__ == "__main__":
    inspect()
