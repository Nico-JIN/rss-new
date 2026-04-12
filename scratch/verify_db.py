import sqlite3, json
conn = sqlite3.connect('data/news.db')
row = conn.execute("SELECT events FROM scheduled_hotspots WHERE category_id = 'us_news' ORDER BY executed_at DESC LIMIT 1").fetchone()
if row:
    events = json.loads(row[0])
    print(f"Total events in latest record: {len(events)}")
    for e in events[:1]:
        print(json.dumps(e, ensure_ascii=False, indent=2))
conn.close()
