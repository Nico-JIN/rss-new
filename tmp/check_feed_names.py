
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from store import get_conn

conn = get_conn()
row = conn.execute("SELECT details FROM fetch_logs WHERE id = 219").fetchone()
if row:
    details = json.loads(row['details'] or '{}')
    print(f"Log 219 keys: {list(details.keys())}")
conn.close()
