import sys
from pathlib import Path
from datetime import datetime

# Add the script directory to path
sys.path.append(str(Path(__file__).parent / "scripts"))

from fetch import scrape_nhk_news, TZ_BJ

try:
    print("[*] Testing NHK API scraping...")
    results = scrape_nhk_news(timeout=30)
    print(f"[*] Found {len(results)} items.")
    if results:
        item = results[0]
        print(f"[*] Sample Item:")
        print(f"    Title:     {item['title']}")
        print(f"    URL:       {item['url']}")
        print(f"    Time:      {item['published']}")
        print(f"    Raw DT:    {item['_dt']}")
        print(f"    Content Len: {len(item.get('content', ''))}")
        if len(item.get('content', '')) > 100:
            print(f"    Content Start: {item['content'][:100]}...")
except Exception as e:
    print(f"[!] Error: {e}")
    import traceback
    traceback.print_exc()
