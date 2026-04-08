
import requests
import feedparser

url = "https://rss.app/feeds/JKZbL25cfHb9dngn.xml"
try:
    resp = requests.get(url, timeout=30)
    if resp.status_code == 200:
        feed = feedparser.parse(resp.content)
        print(f"Entries count: {len(feed.entries)}")
        for entry in feed.entries[:10]:
            print(f"- {entry.title} ({entry.published})")
except Exception as e:
    print(f"Error: {e}")
