import requests
import json

url = "http://127.0.0.1:5001/api/foreign-media/search"
payload = {
    "keywords": "中国",
    "sources": ["google"]
}

try:
    with requests.post(url, json=payload, stream=True, timeout=30) as r:
        print(f"Status: {r.status_code}")
        for line in r.iter_lines():
            if line:
                data = json.loads(line.decode('utf-8'))
                print(f"Type: {data.get('type')}")
                if data.get('type') == 'source_result':
                    print(f"Source: {data.get('source')}, Count: {data.get('count')}")
                if 'error' in data:
                    print(f"Error: {data['error']}")
                if data.get('type') == 'done':
                    print(f"Total: {data.get('total_count')}")
except Exception as e:
    print(f"Error: {e}")
