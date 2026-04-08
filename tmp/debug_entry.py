import feedparser
import re
import json

url = "https://rss.app/feeds/NqwvqfwhwrKv9eya.xml" # 联合早报|国际新闻
print(f"Fetching {url}...")
import subprocess
cmd = ['curl', '-sL', url]
r = subprocess.run(cmd, capture_output=True)
feed = feedparser.parse(r.stdout)

if feed.entries:
    e = feed.entries[0]
    print(f"TITLE: {e.get('title')}")
    print(f"KEYS: {e.keys()}")
    if 'summary' in e:
        print(f"SUMMARY: {e['summary'][:200]}...")
    if 'media_thumbnail' in e:
        print(f"MEDIA_THUMBNAIL: {e['media_thumbnail']}")
    if 'links' in e:
        print(f"LINKS: {e['links']}")
