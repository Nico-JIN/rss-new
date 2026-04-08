import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(
    'https://r.jina.ai/https://www.zaobao.com.sg/news/china/story20260328-8801852',
    headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'X-Return-Format': 'markdown'
    }
)
try:
    with urllib.request.urlopen(req, context=ctx) as r:
        data = json.loads(r.read().decode('utf-8'))
        print('Title:', data['data']['title'])
        print('Published:', data['data'].get('publishedTime', 'NO_TIME'))
        print('Content length:', len(data['data'].get('content', '')))
        print('Content preview:', data['data'].get('content', '')[:200])
except Exception as e:
    print('Error:', e)
