
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import fetch
import time
from datetime import datetime

url = "https://rss.app/feeds/JKZbL25cfHb9dngn.xml"
print(f"Fetching {url}")
content = fetch.curl_fetch(url)
feed = fetch.feedparser.parse(content)

print(f"Total entries: {len(feed.entries)}")

seen_urls = set()
seen_titles = set()
now = fetch.datetime.now(fetch.TZ_BJ)
cutoff = now - fetch.timedelta(hours=24)
timed_raw = []

for e in feed.entries:
    url_val = getattr(e, 'link', '') or ''
    title = fetch.strip_html(getattr(e, 'title', '') or '')
    if not url_val or not title: continue
    rss_dt, has_time = fetch.parse_time(e)
    if not rss_dt: rss_dt = now
    
    item = {
        'url': url_val,
        'uh': fetch.uhash(url_val),
        'th': fetch.thash(title),
        'title': title,
        'platform': '美联社',
        '_mg': '美联社',
        '_dt': rss_dt,
    }
    timed_raw.append(item)

print(f"Raw timed: {len(timed_raw)}")
timed_to_process = [i for i in timed_raw if i['_dt'] >= cutoff]
print(f"After time filter (>= {cutoff}): {len(timed_to_process)}")

timed_deduped, dup_intra_titles = fetch.dedup_within_group(timed_to_process)
print(f"After intra-group dedup: {len(timed_deduped)} (removed {len(dup_intra_titles)})")

seen_uh = set()
timed_final = []
global_dup_titles = []
for item in timed_deduped:
    if item['uh'] not in seen_uh:
        seen_uh.add(item['uh'])
        timed_final.append(item)
    else:
        global_dup_titles.append(f"[{item['platform']}] {item['title']}")
print(f"After global dedup: {len(timed_final)} (removed {len(global_dup_titles)})")

# Check state.json
state = fetch.load_state()
sys_seen_urls = set(state.get('seen_urls', []))
sys_seen_titles = set(state.get('seen_titles', []))

incremental_filtered = 0
actually_new = []
for i in timed_final:
    if i['uh'] not in sys_seen_urls and i['th'] not in sys_seen_titles:
        actually_new.append(i)
    else:
        incremental_filtered += 1
print(f"After incremental filter (vs state.json): {len(actually_new)} (removed {incremental_filtered})")

print("Finally new articles:")
for a in actually_new[:5]:
    print(f"  - {a['title']} ({a['_dt']})")
