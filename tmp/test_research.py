import requests
import json
import sys

def test():
    url = "http://localhost:5001/api/v2/research"
    payload = {
        "keyword": "Israel",
        "mode": "deep_research"
    }
    print(f"Testing {url} with keyword: {payload['keyword']}")
    try:
        resp = requests.post(url, json=payload, timeout=180)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), ensure_ascii=False, indent=2))
        else:
            print(f"Error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    test()
