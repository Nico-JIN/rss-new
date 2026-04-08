
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from store import get_conn

conn = get_conn()
row = conn.execute("SELECT * FROM fetch_logs WHERE id = 219").fetchone()
if row:
    print(f"Log 219:")
    print(f"  duration: {row['duration_sec']}")
    print(f"  feeds_total: {row['feeds_total']}")
    print(f"  articles_new: {row['articles_new']}")
    print(f"  articles_total: {row['articles_total']}")
    
conn.close()
