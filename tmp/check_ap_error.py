
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from store import get_conn

conn = get_conn()
row = conn.execute("SELECT failed_feeds, details FROM fetch_logs WHERE id = 219").fetchone()
if row:
    print(f"Failed Feeds: {row['failed_feeds']}")
    # print(f"Details: {row['details']}")
    details = json.loads(row['details'])
    for k, v in details.items():
        if '美联社' in k or 'JKZbL' in k:
            print(f"Detail for AP: {v}")
conn.close()
