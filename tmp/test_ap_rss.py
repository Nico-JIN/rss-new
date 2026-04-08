
import requests
import feedparser

url = "https://rss.app/feeds/JKZbL25cfHb9dngn.xml"
try:
    print(f"Fetching {url}...")
    resp = requests.get(url, timeout=30)
    print(f"Status code: {resp.status_code}")
    if resp.status_code == 200:
        feed = feedparser.parse(resp.content)
        print(f"Feed title: {feed.feed.get('title', 'N/A')}")
        print(f"Entries count: {len(feed.entries)}")
        for entry in feed.entries[:3]:
            print(f"- {entry.title} ({entry.published})")
    else:
        print(f"Body: {resp.text[:500]}")
except Exception as e:
    print(f"Error: {e}")
