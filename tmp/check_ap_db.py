
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from store import get_conn

conn = get_conn()
rows = conn.execute("SELECT title, published FROM articles WHERE media_group = '美联社' OR platform = '美联社' ORDER BY published DESC LIMIT 5").fetchall()
print(f"Total articles from 美联社: {len(rows)}")
for r in rows:
    print(f"- {r['title']} ({r['published']})")
conn.close()
