#!/usr/bin/env python3
"""
单独测试 TwitterAPI.io 接口
"""

import requests
import json
from pathlib import Path

def get_twitter_api_key():
    """从配置文件读取 API Key"""
    config_file = Path(__file__).parent / 'config' / 'api_keys.json'
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config.get('twitterapi_key', '')
    return ''

def test_twitter_api():
    """测试 TwitterAPI.io 接口"""

    api_key = get_twitter_api_key()

    print("=" * 50)
    print("TwitterAPI.io 接口测试")
    print("=" * 50)

    if not api_key:
        print("[ERROR] 未找到 API Key")
        return

    print(f"[INFO] API Key: {api_key[:10]}...{api_key[-10:]}")

    # TwitterAPI.io 正确的 endpoint
    endpoint = "https://api.twitterapi.io/twitter/tweet/advanced_search"

    # 使用简单关键词测试
    query = "NASA"

    # 参数格式：queryType 为 Latest 或 Top
    params = {
        'query': query,
        'queryType': 'Latest'
    }

    headers = {
        'x-api-key': api_key,  # TwitterAPI.io 使用 x-api-key 头认证
        'Accept': 'application/json'
    }

    print(f"\n[INFO] 请求参数:")
    print(f"  URL: {endpoint}")
    print(f"  Query: {query}")
    print(f"  QueryType: Latest")

    try:
        print("\n[INFO] 发送请求...")
        resp = requests.get(endpoint, params=params, headers=headers, timeout=30)

        print(f"\n[INFO] 响应状态码: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            print(f"\n[SUCCESS] 请求成功!")

            tweets = data.get('tweets', [])
            print(f"[INFO] 返回推文数量: {len(tweets)}")

            if tweets:
                print("\n--- 前3条推文预览 ---")
                for i, tweet in enumerate(tweets[:3], 1):
                    author = tweet.get('author', {})
                    print(f"\n{i}. Tweet ID: {tweet.get('id')}")
                    print(f"   作者: {author.get('userName', 'Unknown')}")
                    print(f"   文本: {tweet.get('text', '')[:100]}...")
                    print(f"   语言: {tweet.get('lang')}")
                    print(f"   点赞: {tweet.get('likeCount', 0)}")
                    print(f"   时间: {tweet.get('createdAt')}")
            else:
                print("[WARN] 没有返回推文数据")
                print(f"[INFO] 完整响应: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")

        elif resp.status_code == 401:
            print("[ERROR] API Key 无效或已过期")
            print(f"[INFO] 响应内容: {resp.text[:200]}")

        elif resp.status_code == 429:
            print("[ERROR] 请求频率超限")
            print(f"[INFO] 响应内容: {resp.text[:200]}")

        elif resp.status_code == 402:
            print("[ERROR] 余额不足/需要付费")
            print(f"[INFO] 响应内容: {resp.text[:200]}")

        else:
            print(f"[ERROR] 其他错误: {resp.status_code}")
            print(f"[INFO] 响应内容: {resp.text[:500]}")

    except requests.exceptions.Timeout:
        print("[ERROR] 请求超时")

    except requests.exceptions.ConnectionError as e:
        print(f"[ERROR] 连接错误: {e}")

    except Exception as e:
        print(f"[ERROR] 异常: {type(e).__name__}: {e}")

if __name__ == '__main__':
    test_twitter_api()