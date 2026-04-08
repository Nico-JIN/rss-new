
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from store import get_conn

conn = get_conn()
logs = conn.execute("SELECT id, started_at, details FROM fetch_logs ORDER BY id DESC LIMIT 20").fetchall()
for log in logs:
    details = json.loads(log['details'] or '{}')
    for name, stats in details.items():
        if '美联社' in name:
            print(f"Log {log['id']} ({log['started_at']}) - {name}: {stats}")
conn.close()
