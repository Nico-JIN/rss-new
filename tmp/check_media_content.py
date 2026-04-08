import feedparser
import re

url = "https://rss.app/feeds/NqwvqfwhwrKv9eya.xml" # 联合早报|国际新闻
import subprocess
cmd = ['curl', '-sL', url]
r = subprocess.run(cmd, capture_output=True)
feed = feedparser.parse(r.stdout)

if feed.entries:
    e = feed.entries[0]
    print(f"TITLE: {e.get('title')}")
    if 'media_content' in e:
        print(f"MEDIA_CONTENT: {e['media_content']}")
    if 'media_thumbnail' in e:
        print(f"MEDIA_THUMBNAIL: {e['media_thumbnail']}")
    if 'summary' in e:
        print(f"SUMMARY: {e['summary'][:200]}...")
