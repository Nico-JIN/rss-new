import requests
import json

BASE_URL = "http://127.0.0.1:5001"

def test_translation():
    print("\n--- Testing Translation API ---")
    url = f"{BASE_URL}/api/ollama/translate"
    payload = {
        "titles": ["Breaking News: New economic policy announced", "Weather forecast for tomorrow"],
        "model": "qwen2.5:7b"
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

def test_search_metadata():
    print("\n--- Testing Search Metadata ---")
    url = f"{BASE_URL}/api/foreign-media/search"
    payload = {
        "keywords": "中国",
        "sources": ["google"]
    }
    try:
        with requests.post(url, json=payload, stream=True, timeout=30) as r:
            for line in r.iter_lines():
                if line:
                    data = json.loads(line.decode('utf-8'))
                    if data.get('type') == 'source_result':
                        articles = data.get('articles', [])
                        if articles:
                            a = articles[0]
                            print(f"Sample Article Type: {a.get('type')}")
                            print(f"Sample Article Source: {a.get('source')}")
                            break
                    if data.get('type') == 'done':
                        print(f"Stance Analysis Type: {type(data.get('stance_analysis'))}")
                        print(f"Stance Analysis: {json.dumps(data.get('stance_analysis'), indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # test_translation() # Take too long if Ollama is slow/missing
    test_search_metadata()
