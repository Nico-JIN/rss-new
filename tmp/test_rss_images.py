import feedparser
import re
import sys
import os

# Add scripts to path to import fetch
sys.path.append(os.path.join(os.getcwd(), 'scripts'))

from fetch import get_image

url = "https://rss.app/feeds/NqwvqfwhwrKv9eya.xml" # 联合早报|国际新闻
print(f"Fetching {url}...")
import subprocess
cmd = ['curl', '-sL', url]
r = subprocess.run(cmd, capture_output=True)
feed = feedparser.parse(r.stdout)

for entry in feed.entries[:3]:
    print(f"Checking entry: {entry.get('title')}")
    img = get_image(entry)
    print(f"RESULT: {img}\n")
