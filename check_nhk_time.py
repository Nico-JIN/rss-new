import requests
import datetime
from datetime import timezone, timedelta

r = requests.get('https://api.nhkworld.jp/nwapi/rdnewsweb/v7b/zh/outline/list.json')
latest = r.json()['data'][0]
print(f"Title: {latest['title']}")
print(f"Public_at: {latest['public_at']}")

ts = float(latest['public_at']) / 1000
dt_utc = datetime.datetime.fromtimestamp(ts, tz=timezone.utc)
dt_bj = dt_utc.astimezone(timezone(timedelta(hours=8)))

print(f"UTC: {dt_utc}")
print(f"BJ (Accurate):  {dt_bj}")
print(f"Current BJ: {datetime.datetime.now(timezone(timedelta(hours=8)))}")
