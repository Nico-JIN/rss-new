import re, sys
from pathlib import Path

# Mock fetch_html
def fetch_html(url):
    import requests
    try:
        r = requests.get(url, timeout=10)
        return r.text
    except:
        return ""

def strip_html(s):
    s = re.sub(r'<[^>]+>', '', s or '')
    return s.strip()

def test_zaobao_regex():
    url = "https://www.zaobao.com.sg/news/world"
    html = fetch_html(url)
    if not html:
        print("Failed to fetch Zaobao")
        return

    link_block_pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']*?story\d{8}-\d+)[^"\']*["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL
    )
    
    results = []
    seen_urls = set()
    for match in link_block_pattern.finditer(html):
        raw_url = match.group(1).strip()
        inner_content = match.group(2)
        title = strip_html(inner_content).strip()
        
        if not title or len(title) < 2:
            continue
            
        full_url = "https://www.zaobao.com.sg" + raw_url
        if full_url in seen_urls: continue
        seen_urls.add(full_url)
        
        results.append((full_url, title))
        
    print(f"Found {len(results)} articles")
    for u, t in results[:5]:
        print(f"- {t}: {u}")

if __name__ == "__main__":
    test_zaobao_regex()
