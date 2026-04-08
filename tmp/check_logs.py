
import sys
from pathlib import Path
# scripts 目录在 tmp 的同级目录下的 scripts
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from store import get_conn, get_fetch_logs

conn = get_conn()
logs = get_fetch_logs(limit=10, conn=conn)
for log in logs:
    print(f"ID: {log['id']}, Started: {log['started_at']}, Status: {log['status']}")
    if log['failed_feeds']:
        print(f"Failed Feeds: {log['failed_feeds']}")
    # print(f"Details: {log['details']}") # Details might be too large
    print("-" * 20)
conn.close()
