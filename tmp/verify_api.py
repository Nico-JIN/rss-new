import requests
try:
    url = "http://localhost:5001/api/v2/hotspot?hours=12&keyword=中国"
    print(f"Requesting: {url}")
    r = requests.get(url)
    data = r.json()
    print(f"Status Code: {r.status_code}")
    print(f"Hotspots found: {data.get('count')}")
    for i, e in enumerate(data.get('events', []), 1):
        print(f"#{i}: {e.get('title')}")
        for idx, a in enumerate(e.get('articles', []), 1):
            s = a.get('summary', 'MISSING')[:50] + "..." if a.get('summary') else "EMPTY"
            print(f"  - Article {idx}: {a.get('title')}")
            print(f"    Summary: {s}")
except Exception as e:
    print(f"Error: {e}")
