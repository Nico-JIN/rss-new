#!/usr/bin/env python3
"""
外媒新闻聚合测试脚本

功能：
1. Google News RSS搜索（免费）
2. 主流国际媒体RSS订阅（BBC/CNN/Reuters/NHK等）
3. Twitter搜索（browser-use）
4. 立场差异分析（LLM）

运行方式：
    python test_foreign_media.py
"""

import feedparser
import requests
import json
import re
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import sys
import os
import time
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

try:
    from llm_tagger import _call_deepseek, load_llm_config
    from intelligence import clean_jina_content
except ImportError:
    print("[WARN] 无法导入项目模块，部分功能受限")
    _call_deepseek = None
    load_llm_config = None


# ═══════════════════════════════════════════════════════════════════
# 配置：主流国际媒体RSS源
# ═══════════════════════════════════════════════════════════════════

RSS_SOURCES = {
    # 英美主流媒体
    'bbc_world': {
        'url': 'https://feeds.bbci.co.uk/news/world/rss.xml',
        'country': 'UK',
        'lang': 'en'
    },
    'cnn_world': {
        'url': 'http://rss.cnn.com/rss/edition_world.rss',
        'country': 'US',
        'lang': 'en'
    },
    'reuters_world': {
        'url': 'https://www.reutersagency.com/feed/world/',
        'country': 'US',
        'lang': 'en'
    },
    'nytimes_world': {
        'url': 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
        'country': 'US',
        'lang': 'en'
    },
    'wsj_world': {
        'url': 'https://feeds.a.dbl.com/wsj/world/rss.xml',
        'country': 'US',
        'lang': 'en'
    },
    'guardian_world': {
        'url': 'https://www.theguardian.com/world/rss',
        'country': 'UK',
        'lang': 'en'
    },

    # 日本媒体
    'nhk_world': {
        'url': 'https://www3.nhk.or.jp/nhkworld/en/rss/news.xml',
        'country': 'Japan',
        'lang': 'en'
    },
    'nhk_japan': {
        'url': 'https://www3.nhk.or.jp/rss/news/cat0.xml',
        'country': 'Japan',
        'lang': 'ja'
    },

    # 欧洲媒体
    'aljazeera': {
        'url': 'https://www.aljazeera.com/xml/rss/all.xml',
        'country': 'Qatar',
        'lang': 'en'
    },
    'dw_world': {
        'url': 'https://rss.dw.com/rss/rss-en-all',
        'country': 'Germany',
        'lang': 'en'
    },
    'france24': {
        'url': 'https://www.france24.com/en/rss',
        'country': 'France',
        'lang': 'en'
    },

    # 印度/东南亚
    'timesofindia': {
        'url': 'https://timesofindia.indiatimes.com/rssfeeds/12215.cms',
        'country': 'India',
        'lang': 'en'
    },
    'straitstimes': {
        'url': 'https://www.straitstimes.com/rss.xml',
        'country': 'Singapore',
        'lang': 'en'
    },

    # 澳大利亚
    'abc_au': {
        'url': 'https://www.abc.net.au/news/feed/5112/rss.xml',
        'country': 'Australia',
        'lang': 'en'
    },

    # 韩国
    'koreaherald': {
        'url': 'https://www.koreaherald.com/common/rss.php',
        'country': 'South Korea',
        'lang': 'en'
    },
}


# ═══════════════════════════════════════════════════════════════════
# Ollama 本地模型配置与调用
# ═══════════════════════════════════════════════════════════════════

def load_ollama_config() -> dict:
    """从 feeds.yaml 加载 Ollama 配置"""
    feeds_file = Path(__file__).parent / 'config' / 'feeds.yaml'
    if not feeds_file.exists():
        return {'enabled': False, 'base_url': 'http://localhost:11434', 'model': 'qwen2.5:7b'}

    try:
        import yaml
        with open(feeds_file, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        ollama_cfg = cfg.get('ollama', {})
        return {
            'enabled': ollama_cfg.get('enabled', False),
            'base_url': ollama_cfg.get('base_url', 'http://localhost:11434'),
            'model': ollama_cfg.get('model', 'qwen2.5:7b'),
            'models': ollama_cfg.get('models', []),
            'timeout': ollama_cfg.get('timeout', 60)
        }
    except Exception as e:
        print(f"[WARN] 加载 Ollama 配置失败: {e}")
        return {'enabled': False, 'base_url': 'http://localhost:11434', 'model': 'qwen2.5:7b'}


def check_ollama_available() -> bool:
    """检查 Ollama 服务是否可用"""
    cfg = load_ollama_config()
    if not cfg.get('enabled'):
        return False

    try:
        resp = requests.get(f"{cfg['base_url']}/api/tags", timeout=5)
        if resp.status_code == 200:
            print(f"[INFO] Ollama 服务可用: {cfg['base_url']}")
            return True
    except Exception as e:
        print(f"[WARN] Ollama 服务不可用: {e}")

    return False


def get_ollama_models() -> list:
    """获取本地 Ollama 已安装的模型列表"""
    cfg = load_ollama_config()
    if not cfg.get('enabled'):
        return []

    try:
        resp = requests.get(f"{cfg['base_url']}/api/tags", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            models = []
            for m in data.get('models', []):
                models.append({
                    'name': m.get('name', ''),
                    'size': m.get('size', 0),
                    'modified_at': m.get('modified_at', '')
                })
            return models
    except Exception as e:
        print(f"[WARN] 获取 Ollama 模型列表失败: {e}")

    return []


def call_ollama(prompt: str, model: str = None, system_prompt: str = None) -> str:
    """
    调用本地 Ollama 模型

    Args:
        prompt: 用户输入
        model: 模型名称 (可选，默认使用配置中的模型)
        system_prompt: 系统提示词 (可选)

    Returns:
        模型返回的文本
    """
    cfg = load_ollama_config()

    if not cfg.get('enabled'):
        print("[WARN] Ollama 未启用")
        return ''

    model = model or cfg.get('model', 'qwen2.5:7b')
    base_url = cfg.get('base_url', 'http://localhost:11434')
    timeout = cfg.get('timeout', 60)

    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': prompt})

    print(f"[INFO] 调用 Ollama: {model} @ {base_url}")

    try:
        resp = requests.post(
            f"{base_url}/api/chat",
            json={
                'model': model,
                'messages': messages,
                'stream': False
            },
            timeout=timeout
        )

        if resp.status_code == 200:
            data = resp.json()
            content = data.get('message', {}).get('content', '')
            print(f"[INFO] Ollama 返回: {len(content)} 字符")
            return content
        else:
            print(f"[ERROR] Ollama 返回错误: {resp.status_code} - {resp.text[:100]}")

    except requests.exceptions.Timeout:
        print(f"[ERROR] Ollama 请求超时 (timeout={timeout}s)")
    except Exception as e:
        print(f"[ERROR] Ollama 调用失败: {e}")

    return ''


def call_ollama_stream(prompt: str, model: str = None, system_prompt: str = None):
    """
    流式调用 Ollama 模型 (返回生成器)

    Args:
        prompt: 用户输入
        model: 模型名称
        system_prompt: 系统提示词

    Yields:
        每次生成的文本片段
    """
    cfg = load_ollama_config()

    if not cfg.get('enabled'):
        print("[WARN] Ollama 未启用")
        return

    model = model or cfg.get('model', 'qwen2.5:7b')
    base_url = cfg.get('base_url', 'http://localhost:11434')
    timeout = cfg.get('timeout', 120)

    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': prompt})

    print(f"[INFO] 流式调用 Ollama: {model}")

    try:
        resp = requests.post(
            f"{base_url}/api/chat",
            json={
                'model': model,
                'messages': messages,
                'stream': True
            },
            timeout=timeout,
            stream=True
        )

        if resp.status_code == 200:
            for line in resp.iter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        content = data.get('message', {}).get('content', '')
                        if content:
                            yield content
                        if data.get('done', False):
                            break
                    except json.JSONDecodeError:
                        continue
        else:
            print(f"[ERROR] Ollama 流式返回错误: {resp.status_code}")

    except Exception as e:
        print(f"[ERROR] Ollama 流式调用失败: {e}")


def ollama_translate_keywords(keywords_cn: str, model: str = None) -> list:
    """
    使用 Ollama 将中文关键词翻译为英文搜索词

    Args:
        keywords_cn: 中文关键词字符串
        model: 使用的模型 (可选)

    Returns:
        英文关键词列表
    """
    prompt = f"""请将以下中国新闻关键词翻译为英文，用于在Google News和Twitter搜索外媒报道。

中文关键词: {keywords_cn}

翻译原则：
1. 准确翻译原文含义，不要添加原文没有的内容
2. 使用外媒常用表述，不是直译
3. 提取核心事件和主体，去除无关修饰词
4. 输出2-3个最有效的英文搜索词

输出格式(JSON数组): ["keyword1", "keyword2", "keyword3"]"""

    result = call_ollama(prompt, model=model)

    if result:
        try:
            # 清理可能的 markdown 格式
            cleaned = re.sub(r'```json\n?|\n?```', '', result).strip()
            keywords_en = json.loads(cleaned)
            if isinstance(keywords_en, list):
                print(f"[INFO] Ollama 翻译结果: {keywords_cn} → {keywords_en}")
                return keywords_en
        except json.JSONDecodeError:
            print(f"[WARN] Ollama 返回非 JSON 格式: {result[:100]}")

    return simple_translate([keywords_cn])


def ollama_analyze_stance(articles: list, model: str = None) -> dict:
    """
    使用 Ollama 分析各国媒体立场

    Args:
        articles: 新闻列表
        model: 使用的模型

    Returns:
        立场分析结果
    """
    if not articles:
        return {'error': '无文章数据'}

    # 按国家分组
    by_country = defaultdict(list)
    for art in articles:
        country = art.get('country', 'Unknown')
        by_country[country].append(art)

    # 构建分析提示
    country_summary = ""
    for country, arts in by_country.items():
        titles = [a.get('title', '')[:80] for a in arts[:5]]
        country_summary += f"\n【{country}】({len(arts)}条):\n" + "\n".join(f"  - {t}" for t in titles)

    prompt = f"""请分析以下各国媒体对同一事件的报道立场差异。

新闻标题摘要:
{country_summary}

请从以下角度分析：
1. 各国媒体的整体立场倾向（支持/中立/批评）
2. 报道语调特点（客观/情绪化/煽动性）
3. 关注焦点差异
4. 整体对比结论

输出JSON格式:
{
  "country_analysis": {
    "US": {"stance": "...", "tone": "...", "key_focus": "..."},
    "UK": {"stance": "...", "tone": "...", "key_focus": "..."}
  },
  "comparison": {"differences": "..."}
}"""

    result = call_ollama(prompt, model=model)

    if result:
        try:
            cleaned = re.sub(r'```json\n?|\n?```', '', result).strip()
            analysis = json.loads(cleaned)
            print(f"[INFO] Ollama 立场分析完成")
            return analysis
        except json.JSONDecodeError:
            print(f"[WARN] Ollama 分析返回非 JSON: {result[:100]}")
            return {'raw_response': result}

    return {'error': '分析失败'}


# ═══════════════════════════════════════════════════════════════════
# Google News RSS 搜索
# ═══════════════════════════════════════════════════════════════════

def search_google_news(query: str, lang: str = 'en', country: str = 'US') -> list:
    """
    通过 Google News RSS 搜索新闻

    Args:
        query: 搜索关键词（英文）
        lang: 语言代码
        country: 国家代码

    Returns:
        新闻列表
    """
    # 构建RSS URL
    encoded_query = requests.utils.quote(query)
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl={lang}&gl={country}&ceid={country}:{lang}"

    print(f"[INFO] Google News RSS: {rss_url}")

    try:
        resp = requests.get(rss_url, timeout=15)
        feed = feedparser.parse(resp.content)

        articles = []
        for entry in feed.entries:
            # 解析来源
            source_name = 'Unknown'
            if 'source' in entry:
                source_name = entry.source.title if hasattr(entry.source, 'title') else str(entry.source)

            # 清理标题（去除来源后缀）
            title = entry.title
            if ' - ' in title and source_name in title:
                title = title.split(' - ')[0]

            articles.append({
                'title': title,
                'url': entry.link,
                'published': entry.published if 'published' in entry else '',
                'source': source_name,
                'summary': entry.summary if 'summary' in entry else '',
                'search_engine': 'google_news'
            })

        print(f"[INFO] Google News找到 {len(articles)} 条结果")
        return articles

    except Exception as e:
        print(f"[ERROR] Google News搜索失败: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════
# 主流媒体RSS订阅匹配
# ═══════════════════════════════════════════════════════════════════

def fetch_rss_feed(source_name: str, source_config: dict) -> list:
    """抓取单个RSS源（带重试）"""
    url = source_config['url']

    for attempt in range(3):
        try:
            if attempt > 0:
                time.sleep(1 + attempt)  # 递增延迟

            resp = requests.get(url, timeout=15)
            feed = feedparser.parse(resp.content)

            articles = []
            for entry in feed.entries[:30]:  # 每个源最多30条
                articles.append({
                    'title': entry.title if hasattr(entry, 'title') else '',
                    'url': entry.link if hasattr(entry, 'link') else '',
                    'published': entry.published if 'published' in entry else '',
                    'source': source_name,
                    'country': source_config['country'],
                    'lang': source_config['lang'],
                    'summary': entry.summary if 'summary' in entry else '',
                    'type': 'rss'
                })

            return articles

        except Exception as e:
            if attempt == 2:  # 最后一次尝试才打印警告
                # 只显示简短错误信息
                err_msg = str(e)
                if len(err_msg) > 60:
                    err_msg = err_msg[:60] + "..."
                print(f"[WARN] {source_name} 抓取失败: {err_msg}")

    return []


def search_rss_sources(keywords: list) -> list:
    """
    从主流媒体RSS中搜索关键词匹配的文章

    Args:
        keywords: 搜索关键词列表

    Returns:
        匹配的文章列表
    """
    print(f"[INFO] 从 {len(RSS_SOURCES)} 个RSS源中搜索...")

    all_articles = []
    keyword_pattern = '|'.join(keywords).lower()

    # 并行抓取所有RSS
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fetch_rss_feed, name, config): name
            for name, config in RSS_SOURCES.items()
        }

        for future in futures:
            articles = future.result()
            all_articles.extend(articles)

    print(f"[INFO] 共抓取 {len(all_articles)} 条RSS新闻")

    # 关键词匹配
    matched = []
    for art in all_articles:
        text = (art['title'] + ' ' + art.get('summary', '')).lower()
        if re.search(keyword_pattern, text):
            matched.append(art)

    print(f"[INFO] 关键词匹配到 {len(matched)} 条")
    return matched


# ═══════════════════════════════════════════════════════════════════
# Twitter 搜索（twitterapi.io）
# ═══════════════════════════════════════════════════════════════════

# TwitterAPI.io 配置
# 获取API Key: https://twitterapi.io (免费500次/月)
TWITTER_API_KEY = None  # 在这里填入你的API Key，或从环境变量读取

def get_api_key_from_feeds_yaml(source_name: str) -> str:
    """从 feeds.yaml 的 external_sources 配置读取 API Key"""
    feeds_file = Path(__file__).parent / 'config' / 'feeds.yaml'
    if not feeds_file.exists():
        return ''
    try:
        import yaml
        with open(feeds_file, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        sources = cfg.get('external_sources', [])
        for src in sources:
            if src.get('name') == source_name and src.get('enabled'):
                api_key_ref = src.get('config', {}).get('api_key_ref', '')
                if api_key_ref:
                    # 判断是键名引用还是直接的API Key值
                    if api_key_ref.endswith('_key') or len(api_key_ref) < 20:
                        # 是键名引用，从 api_keys.json 读取
                        keys_file = Path(__file__).parent / 'config' / 'api_keys.json'
                        if keys_file.exists():
                            with open(keys_file, 'r', encoding='utf-8') as f:
                                keys_cfg = json.load(f)
                            return keys_cfg.get(api_key_ref, '')
                    else:
                        # 直接是API Key值
                        return api_key_ref
    except Exception as e:
        print(f"[WARN] 读取 feeds.yaml 失败: {e}")
    return ''

def get_twitter_api_key():
    """获取Twitter API Key - 从前端配置(feeds.yaml)或api_keys.json读取"""
    global TWITTER_API_KEY

    if TWITTER_API_KEY:
        return TWITTER_API_KEY

    # 1. 从环境变量读取
    key = os.environ.get('TWITTERAPI_KEY', '')
    if key:
        return key

    # 2. 从 feeds.yaml 的前端配置读取 (Twitter)
    key = get_api_key_from_feeds_yaml('Twitter')
    if key:
        return key

    # 3. 从 api_keys.json 直接读取
    config_file = Path(__file__).parent / 'config' / 'api_keys.json'
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                return config.get('twitterapi_key', '')
        except:
            pass

    return ''


def search_twitter(query: str, max_results: int = 20, sort_order: str = 'recency') -> list:
    """
    通过 twitterapi.io 搜索 Twitter

    Args:
        query: 搜索关键词
        max_results: 返回数量（每页最多20条，可通过分页获取更多）
        sort_order: 'recency'(最新) 或 'relevancy'(相关度)

    Returns:
        推文列表
    """
    api_key = get_twitter_api_key()

    if not api_key:
        print("[WARN] TwitterAPI Key未配置，跳过Twitter搜索")
        print("[提示] 获取API Key: https://twitterapi.io")
        print("[提示] 配置方式: 设置环境变量 TWITTERAPI_KEY 或在 config/api_keys.json 中配置")
        return []

    print(f"[INFO] 正在通过twitterapi.io搜索Twitter: {query}")

    # TwitterAPI.io 正确的 endpoint
    endpoint = "https://api.twitterapi.io/twitter/tweet/advanced_search"

    # 参数格式：queryType 为 Latest 或 Top
    query_type = "Latest" if sort_order == 'recency' else "Top"

    params = {
        'query': query,
        'queryType': query_type
    }

    headers = {
        'X-API-Key': api_key,
        'Accept': 'application/json'
    }

    # 调试：打印完整请求URL
    print(f"[DEBUG] Twitter API Request: {endpoint}?query={query}&queryType={query_type}")

    results = []
    cursor = ''
    pages_needed = (max_results + 19) // 20  # 每页最多20条

    try:
        for page in range(pages_needed):
            if cursor:
                params['cursor'] = cursor

            resp = requests.get(endpoint, params=params, headers=headers, timeout=30)

            # 调试：打印响应状态和内容
            print(f"[DEBUG] Twitter API Response: {resp.status_code}")
            if resp.status_code != 200:
                print(f"[DEBUG] Response body: {resp.text[:500]}")

            if resp.status_code == 200:
                data = resp.json()
                tweets = data.get('tweets', [])

                for tweet in tweets:
                    # 解析作者信息 (UserInfo 对象)
                    author = tweet.get('author', {})
                    author_name = author.get('userName') or author.get('name') or 'Unknown'

                    results.append({
                        'id': tweet.get('id'),
                        'text': tweet.get('text'),
                        'author_id': author.get('id'),
                        'author_name': author_name,
                        'created_at': tweet.get('createdAt'),
                        'lang': tweet.get('lang'),
                        'like_count': tweet.get('likeCount', 0),
                        'retweet_count': tweet.get('retweetCount', 0),
                        'view_count': tweet.get('viewCount', 0),
                        'reply_count': tweet.get('replyCount', 0),
                        'quote_count': tweet.get('quoteCount', 0),
                        'url': tweet.get('url') or f"https://twitter.com/i/web/status/{tweet.get('id')}",
                        'source': author_name,  # 显示作者名
                        'country': 'Social Media',
                        'type': 'tweet',
                        'published': tweet.get('createdAt', '')[:10] if tweet.get('createdAt') else ''
                    })

                # 检查是否有下一页
                if not data.get('has_next_page'):
                    break
                cursor = data.get('next_cursor', '')

                # 已获取足够数量
                if len(results) >= max_results:
                    break

            elif resp.status_code == 401:
                print("[ERROR] TwitterAPI Key无效或已过期")
                break
            elif resp.status_code == 429:
                print("[ERROR] TwitterAPI 请求频率超限")
                break
            else:
                print(f"[ERROR] TwitterAPI请求失败: {resp.status_code} - {resp.text[:100]}")
                break

        print(f"[INFO] Twitter找到 {len(results)} 条推文")
        return results[:max_results]

    except Exception as e:
        print(f"[ERROR] Twitter搜索异常: {e}")

    return []


# ═══════════════════════════════════════════════════════════════════
# NewsAPI (newsapi.org) 搜索
# ═══════════════════════════════════════════════════════════════════

def get_newsapi_key():
    """获取NewsAPI Key - 从前端配置(feeds.yaml)或api_keys.json读取"""
    # 1. 从环境变量读取
    key = os.environ.get('NEWSAPI_KEY', '')
    if key:
        return key

    # 2. 从 feeds.yaml 的前端配置读取
    key = get_api_key_from_feeds_yaml('NewsAPI')
    if key:
        return key

    # 3. 从 api_keys.json 直接读取
    config_file = Path(__file__).parent / 'config' / 'api_keys.json'
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                key = config.get('newsapi_key', '')
                if key:
                    return key
        except:
            pass

    return ''

def search_newsapi(query: str, max_results: int = 20, sort_by: str = 'publishedAt') -> list:
    """
    通过 NewsAPI (newsapi.org) 搜索新闻
    
    Args:
        query: 搜索关键词
        max_results: 返回数量
        sort_by: 排序方式 ('relevancy', 'popularity', 'publishedAt')

    Returns:
        新闻列表
    """
    api_key = get_newsapi_key()

    if not api_key:
        print("[WARN] NewsAPI Key未配置，跳过NewsAPI搜索")
        return []

    print(f"[INFO] 正在通过NewsAPI搜索: {query}")

    endpoint = "https://newsapi.org/v2/everything"
    
    params = {
        'q': query,
        'sortBy': sort_by,
        'pageSize': min(max_results, 100),
        'language': 'en',
    }

    headers = {
        'X-Api-Key': api_key
    }

    results = []

    try:
        resp = requests.get(endpoint, params=params, headers=headers, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            articles = data.get('articles', [])
            
            for art in articles:
                source_name = art.get('source', {}).get('name', 'Unknown')
                
                results.append({
                    'title': art.get('title', ''),
                    'url': art.get('url', ''),
                    'published': art.get('publishedAt', ''),
                    'source': source_name,
                    'summary': art.get('description', ''),
                    'image': art.get('urlToImage', ''),
                    'country': 'Unknown',  # NewsAPI /everything endpoint doesn't return country
                    'type': 'newsapi'
                })
                
        elif resp.status_code == 401:
            print("[ERROR] NewsAPI Key无效或已过期")
        elif resp.status_code == 429:
            print("[ERROR] NewsAPI 请求频率超限")
        else:
            print(f"[ERROR] NewsAPI请求失败: {resp.status_code} - {resp.text[:100]}")

        print(f"[INFO] NewsAPI找到 {len(results)} 条新闻")
        return results[:max_results]

    except Exception as e:
        print(f"[ERROR] NewsAPI搜索异常: {e}")

    return []




# ═══════════════════════════════════════════════════════════════════
# AI 搜索引擎搜索 (Perplexity, Tavily, Brave)
# ═══════════════════════════════════════════════════════════════════

def get_api_key_for(service: str) -> str:
    """获取指定服务的API Key - 从前端配置(feeds.yaml)或api_keys.json读取"""
    # 服务名映射到 feeds.yaml 中的 source name
    name_map = {
        'tavily': 'Tavily',
        'brave': 'Brave Search',
        'perplexity': 'Perplexity',
        'serpapi': 'YouTube',  # YouTube 使用 SerpApi
        'youtube': 'YouTube',
        'reddit': 'Reddit'  # Reddit 也使用 SerpApi
    }
    source_name = name_map.get(service, service.capitalize())

    # 1. 从环境变量读取
    key = os.environ.get(f'{service.upper()}_KEY', '')
    if key:
        return key

    # 2. 从 feeds.yaml 的前端配置读取
    key = get_api_key_from_feeds_yaml(source_name)
    if key:
        return key

    # 3. 从 api_keys.json 直接读取
    config_file = Path(__file__).parent / 'config' / 'api_keys.json'
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f).get(f'{service}_key', '')
        except: pass
    return ''

def search_tavily(query: str, max_results: int = 10) -> dict:
    """
    通过 Tavily AI 搜索引擎搜索

    Returns:
        dict: {
            'articles': 新闻条目列表 (每个条目包含 content 摘要),
            'ai_answer': AI 生成的详细分析答案
        }
    """
    api_key = get_api_key_for('tavily')
    if not api_key:
        return {'articles': [], 'ai_answer': ''}

    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",      # 高级搜索获取更多内容
                "include_answer": "advanced",    # 获取详细的 AI 分析答案
                "include_raw_content": False,
                "include_images": False,
                "max_results": max_results
            },
            timeout=30
        )

        if resp.status_code == 200:
            data = resp.json()

            # 提取新闻条目（每个条目的 content 是与查询相关的摘要）
            articles = []
            for r in data.get('results', []):
                articles.append({
                    'title': r.get('title', ''),
                    'url': r.get('url', ''),
                    'published': r.get('published_date', ''),
                    'source': r.get('favicon', '') or 'Tavily',
                    'summary': r.get('content', ''),  # 查询相关的内容片段
                    'score': r.get('score', 0),
                    'type': 'tavily',
                    'country': 'AI'
                })

            # 提取 AI 分析答案（advanced 模式更详细）
            ai_answer = data.get('answer', '')

            return {
                'articles': articles,
                'ai_answer': ai_answer
            }

    except Exception as e:
        print(f"[ERROR] Tavily异常: {e}")

    return {'articles': [], 'ai_answer': ''}

def search_brave(query: str, max_results: int = 10) -> list:
    api_key = get_api_key_for('brave')
    if not api_key: return []
    try:
        resp = requests.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": min(max_results, 20)}, headers={"Accept": "application/json", "X-Subscription-Token": api_key}, timeout=15)
        if resp.status_code == 200:
            results = []
            for r in resp.json().get('web', {}).get('results', []):
                results.append({'title': r.get('title', ''), 'url': r.get('url', ''), 'published': r.get('age', ''), 'source': r.get('meta_url', {}).get('hostname', 'Brave'), 'summary': r.get('description', ''), 'type': 'brave', 'country': 'AI'})
            return results
    except Exception as e: print(f"[ERROR] Brave异常: {e}")
    return []

def search_perplexity(query: str, max_results: int = 10) -> dict:
    """
    通过 Perplexity AI 搜索引擎搜索

    Returns:
        dict: {
            'articles': 基于 citations 生成的新闻条目列表,
            'ai_report': AI 分析报告全文 (str)
        }
    """
    api_key = get_api_key_for('perplexity')
    if not api_key:
        return {'articles': [], 'ai_report': ''}

    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            json={
                "model": "sonar-pro",
                "messages": [
                    {"role": "system", "content": "You are a news aggregator. Search for the query and provide the latest news with citations. Return structured news analysis with sources."},
                    {"role": "user", "content": query}
                ]
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json"
            },
            timeout=45
        )

        if resp.status_code == 200:
            data = resp.json()

            # 提取 AI 分析报告
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')

            # 提取 citations 作为新闻条目的来源链接
            citations = data.get('citations', [])

            # 将 citations 转换为新闻条目格式
            articles = []
            for i, url in enumerate(citations[:max_results]):
                articles.append({
                    'title': f'Source #{i+1} - {query}',
                    'url': url,
                    'published': '',
                    'source': 'Perplexity Citation',
                    'summary': '',
                    'type': 'perplexity',
                    'country': 'AI'
                })

            return {
                'articles': articles,
                'ai_report': content
            }

    except Exception as e:
        print(f"[ERROR] Perplexity异常: {e}")

    return {'articles': [], 'ai_report': ''}


def search_youtube(query: str, max_results: int = 10) -> list:
    """
    通过 SerpApi YouTube Search API 搜索视频

    Returns:
        list: 视频条目列表，每个条目包含 title, url, published, source, summary, type, country
    """
    api_key = get_api_key_for('serpapi')
    if not api_key:
        print("[WARN] 未配置 SerpApi API Key，跳过 YouTube 搜索")
        return []

    try:
        resp = requests.get(
            "https://serpapi.com/search",
            params={
                "engine": "youtube",
                "search_query": query,
                "api_key": api_key,
                "hl": "en",
                "gl": "us"
            },
            timeout=20
        )

        if resp.status_code == 200:
            data = resp.json()
            results = []

            video_results = data.get('video_results', [])
            for v in video_results[:max_results]:
                # 提取频道信息
                channel = v.get('channel', {})
                channel_name = channel.get('name', 'YouTube') if isinstance(channel, dict) else str(channel)

                # 提取发布时间
                published = v.get('published_date', '')
                if not published:
                    # 有些视频用 rich_snippet 里的日期
                    published = v.get('rich_snippet', {}).get('published', '')

                results.append({
                    'title': v.get('title', ''),
                    'url': v.get('link', ''),
                    'published': published,
                    'source': channel_name,
                    'summary': v.get('description', '')[:300] if v.get('description') else '',
                    'views': v.get('views', 0),
                    'length': v.get('length', ''),
                    'thumbnail': v.get('thumbnail', {}).get('static', '') if v.get('thumbnail') else '',
                    'type': 'youtube',
                    'country': 'Video'
                })

            print(f"[INFO] YouTube搜索: {query} → {len(results)} 条结果")
            return results

    except Exception as e:
        print(f"[ERROR] YouTube搜索异常: {e}")

    return []


# ═══════════════════════════════════════════════════════════════════
# Reddit 官方 API 搜索
# ═══════════════════════════════════════════════════════════════════

# Reddit OAuth token 缓存
REDDIT_ACCESS_TOKEN = None
REDDIT_TOKEN_EXPIRES = 0


def get_reddit_credentials():
    """获取 Reddit API 凭证 - 从 feeds.yaml 或 api_keys.json 读取"""
    # 1. 从 feeds.yaml 读取
    try:
        cfg = load_feeds_config()
        sources = cfg.get('external_sources', [])
        for src in sources:
            if src.get('name') == 'Reddit' and src.get('enabled'):
                config = src.get('config', {})
                client_id = config.get('client_id', '')
                client_secret = config.get('client_secret', '')
                if client_id and client_secret:
                    return client_id, client_secret
    except Exception as e:
        print(f"[WARN] 读取 feeds.yaml 失败: {e}")

    # 2. 从 api_keys.json 读取
    config_file = Path(__file__).parent / 'config' / 'api_keys.json'
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                client_id = config.get('reddit_client_id', '')
                client_secret = config.get('reddit_client_secret', '')
                if client_id and client_secret:
                    return client_id, client_secret
        except:
            pass

    return '', ''


def get_reddit_access_token():
    """获取 Reddit OAuth access token（自动缓存和刷新）"""
    global REDDIT_ACCESS_TOKEN, REDDIT_TOKEN_EXPIRES

    # 检查缓存的 token 是否还有效（提前5分钟刷新）
    if REDDIT_ACCESS_TOKEN and REDDIT_TOKEN_EXPIRES > time.time() + 300:
        return REDDIT_ACCESS_TOKEN

    client_id, client_secret = get_reddit_credentials()
    if not client_id or not client_secret:
        print("[WARN] Reddit API 凭证未配置，跳过 Reddit 搜索")
        print("[提示] 配置方式: 在 config/api_keys.json 中添加 reddit_client_id 和 reddit_client_secret")
        print("[提示] 获取凭证: https://www.reddit.com/prefs/apps 创建 script 类型应用")
        return None

    # 获取新的 access token
    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            data={
                "grant_type": "client_credentials"
            },
            headers={
                "User-Agent": "RSS-News-Aggregator/1.0"
            },
            auth=(client_id, client_secret),
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            REDDIT_ACCESS_TOKEN = data.get('access_token')
            # expires_in 是秒数，通常3600秒（1小时）
            REDDIT_TOKEN_EXPIRES = time.time() + data.get('expires_in', 3600)
            print(f"[INFO] Reddit OAuth token 获取成功，有效期 {data.get('expires_in', 3600)}秒")
            return REDDIT_ACCESS_TOKEN
        else:
            print(f"[ERROR] Reddit OAuth 失败: {resp.status_code} - {resp.text}")

    except Exception as e:
        print(f"[ERROR] Reddit OAuth 异常: {e}")

    return None


def search_reddit(query: str, max_results: int = 15, subreddit: str = None) -> list:
    """
    通过 Reddit 官方 API 搜索帖子

    Args:
        query: 搜索关键词
        max_results: 返回数量（最多100）
        subreddit: 可选，限定在某个 subreddit 内搜索

    Returns:
        list: 帖子列表，包含 title, url, published, source, summary, upvotes, comments 等
    """
    access_token = get_reddit_access_token()
    if not access_token:
        return []

    try:
        # 构建 API URL
        if subreddit:
            # 在特定 subreddit 内搜索
            url = f"https://oauth.reddit.com/r/{subreddit}/search"
        else:
            # 全站搜索
            url = "https://oauth.reddit.com/search"

        params = {
            "q": query,
            "limit": min(max_results, 100),
            "sort": "relevance",  # 可选: relevance, hot, top, new, comments
            "type": "link",       # 只搜索帖子（不搜索评论）
            "t": "all",           # 时间范围: all, day, hour, month, week, year
            "raw_json": 1         # 返回原始 JSON（避免 HTML 编码）
        }

        resp = requests.get(
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "RSS-News-Aggregator/1.0"
            },
            timeout=20
        )

        if resp.status_code == 200:
            data = resp.json()
            results = []

            # Reddit API 返回格式: {"kind": "Listing", "data": {"children": [...]}
            children = data.get('data', {}).get('children', [])

            for child in children[:max_results]:
                post_data = child.get('data', {})

                # 提取帖子信息
                title = post_data.get('title', '')
                permalink = post_data.get('permalink', '')
                url = post_data.get('url', '')  # 帖子链接的内容URL

                # 如果是自帖（纯文字帖子），使用 permalink
                if post_data.get('is_self', False):
                    post_url = f"https://www.reddit.com{permalink}"
                else:
                    post_url = url

                # 提取 subreddit
                subreddit_name = post_data.get('subreddit', '')

                # 提取发布时间（Unix timestamp）
                created_utc = post_data.get('created_utc', 0)
                if created_utc:
                    published = datetime.utcfromtimestamp(created_utc).strftime('%Y-%m-%d %H:%M')
                else:
                    published = ''

                # 提取元数据
                upvotes = post_data.get('ups', 0) or post_data.get('score', 0)
                comments = post_data.get('num_comments', 0)
                selftext = post_data.get('selftext', '')[:300] if post_data.get('selftext') else ''

                results.append({
                    'title': title,
                    'url': post_url,
                    'published': published,
                    'source': f"r/{subreddit_name}" if subreddit_name else 'Reddit',
                    'summary': selftext,
                    'upvotes': upvotes,
                    'comments': comments,
                    'type': 'reddit',
                    'country': 'Social'
                })

            print(f"[INFO] Reddit搜索: {query} → {len(results)} 条结果")
            return results

        elif resp.status_code == 401:
            # Token 过期，清除缓存并重新获取
            print("[WARN] Reddit token 过期，重新获取...")
            REDDIT_ACCESS_TOKEN = None
            return search_reddit(query, max_results, subreddit)
        else:
            print(f"[ERROR] Reddit搜索失败: {resp.status_code} - {resp.text}")

    except Exception as e:
        print(f"[ERROR] Reddit搜索异常: {e}")

    return []


# ═══════════════════════════════════════════════════════════════════
# 关键词翻译（中文→适合外媒搜索的英文）
# ═══════════════════════════════════════════════════════════════════

def translate_keywords_for_foreign_search(keywords_cn: list) -> list:
    """
    将中文关键词翻译为适合外媒搜索的英文

    使用LLM翻译，确保符合外媒常用表述
    """
    if not _call_deepseek:
        # 无LLM时的简单fallback
        print("[WARN] 无LLM，使用简单翻译")
        return simple_translate(keywords_cn)

    cfg = load_llm_config()

    prompt = f"""请将以下中国新闻关键词翻译为英文，用于在Google News和Twitter搜索外媒报道。

中文关键词: {keywords_cn}

翻译原则：
1. 准确翻译原文含义，不要添加原文没有的内容
2. 使用外媒常用表述，不是直译
3. 提取核心事件和主体，去除无关修饰词
4. 输出2-3个最有效的英文搜索词

输出格式(JSON数组): ["keyword1", "keyword2", "keyword3"]"""

    try:
        raw = _call_deepseek([{"role": "user", "content": prompt}], cfg)
        if raw:
            # 解析JSON
            cleaned = re.sub(r'```json\n?|\n?```', '', raw).strip()
            keywords_en = json.loads(cleaned)
            if isinstance(keywords_en, list):
                print(f"[INFO] 翻译结果: {keywords_cn} → {keywords_en}")
                return keywords_en
    except Exception as e:
        print(f"[WARN] LLM翻译失败: {e}")

    return simple_translate(keywords_cn)


def simple_translate(keywords_cn: list) -> list:
    """简单翻译映射表（无LLM时的fallback）"""
    mapping = {
        '中国': 'China',
        '美国': 'US America',
        '台湾': 'Taiwan',
        '关税': 'tariffs',
        '贸易战': 'trade war',
        '南海': 'South China Sea',
        '朝鲜': 'North Korea',
        '日本': 'Japan',
        '俄罗斯': 'Russia',
        '乌克兰': 'Ukraine',
        '芯片': 'semiconductor chips',
        '人工智能': 'AI artificial intelligence',
        '华为': 'Huawei',
        '字节跳动': 'ByteDance TikTok',
        '疫情': 'COVID pandemic',
        '经济': 'economy',
    }

    result = []
    for kw in keywords_cn:
        for cn, en in mapping.items():
            if cn in kw:
                result.append(en)

    return result if result else ['China news']


# ═══════════════════════════════════════════════════════════════════
# 内容获取（Jina Reader）
# ═══════════════════════════════════════════════════════════════════

def fetch_article_content(url: str, timeout: int = 20, retries: int = 2) -> str:
    """通过Jina Reader获取文章全文"""

    # Google News RSS的特殊URL需要解析真实链接
    if 'news.google.com/rss/articles/' in url:
        real_url = resolve_google_news_url(url)
        if real_url:
            url = real_url
        else:
            # 解析失败，返回空（网络问题导致）
            return ''

    jina_url = f"https://r.jina.ai/{url}"

    for attempt in range(retries):
        try:
            if attempt > 0:
                time.sleep(2)

            resp = requests.get(
                jina_url,
                headers={'Accept': 'text/plain'},
                timeout=timeout
            )

            if resp.status_code == 200:
                content = resp.text
                if content and len(content) > 100:
                    # 清理内容
                    content = re.sub(r'\s+', ' ', content)
                    return content[:2000]  # 截取前2000字

        except Exception as e:
            pass  # 静默失败，不打印过多警告

    return ''


def resolve_google_news_url(google_url: str) -> str:
    """
    解析Google News RSS中的特殊URL，获取真实文章链接
    """
    try:
        # 方法1: 直接访问获取重定向
        resp = requests.get(google_url, timeout=10, allow_redirects=True)
        final_url = resp.url
        if final_url and final_url != google_url and not final_url.startswith('https://news.google.com'):
            return final_url

    except Exception:
        pass

    return ''


def enrich_articles_content(articles: list, max_workers: int = 5) -> list:
    """批量获取文章全文"""
    print(f"[INFO] 正在获取 {len(articles)} 篇文章全文...")

    enriched = {}

    def fetch_task(art):
        content = fetch_article_content(art['url'])
        return art['url'], content

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_task, art) for art in articles]

        for future in futures:
            try:
                url, content = future.result(timeout=30)
                if content:
                    enriched[url] = content
            except:
                pass

    # 更新文章
    for art in articles:
        if art['url'] in enriched:
            art['content'] = enriched[art['url']]

    success_count = sum(1 for a in articles if a.get('content'))
    print(f"[INFO] 成功获取 {success_count}/{len(articles)} 篇全文")

    return articles


# ═══════════════════════════════════════════════════════════════════
# 立场差异分析
# ═══════════════════════════════════════════════════════════════════

def analyze_media_stances(articles: list, event_keywords: list) -> dict:
    """
    LLM分析各国媒体报道立场差异

    Args:
        articles: 新闻文章列表
        event_keywords: 事件关键词

    Returns:
        立场分析结果
    """
    if not _call_deepseek:
        print("[WARN] 无LLM，无法进行立场分析")
        return {"error": "LLM未配置"}

    # 按国家分组
    by_country = defaultdict(list)
    for art in articles:
        country = art.get('country', 'Unknown')
        if art.get('content'):
            by_country[country].append(art)

    # 构建分析上下文
    context_parts = []
    for country, arts in by_country.items():
        if arts:
            sample = arts[0]
            content_preview = sample.get('content', '')[:500]
            context_parts.append(f"""
【{country}媒体 - {sample.get('source', 'Unknown')}】
标题: {sample['title']}
内容摘要: {content_preview}
""")

    context = "\n".join(context_parts)

    prompt = f"""你是国际舆情分析师。请分析以下各国媒体对同一事件的报道立场差异。

事件关键词: {event_keywords}

各国媒体报道:
{context}

请输出JSON格式的分析报告:
{
    "event_summary": "事件简要概述",
    "overall_tone": "positive/neutral/negative",
    "country_analysis": {
        "US": {
            "stance": "支持/中立/批评",
            "tone": "客观/情绪化/中立",
            "key_focus": "报道重点关注什么",
            "keyword_usage": ["使用了哪些关键词"]
        },
        "UK": { ... },
        "Japan": { ... }
    },
    "comparison": {
        "common_ground": "各国共识点",
        "differences": "各国报道差异",
        "notable_bias": "明显偏见或倾向"
    },
    "conclusion": "总体结论"
}"""

    cfg = load_llm_config()

    try:
        raw = _call_deepseek([{"role": "user", "content": prompt}], cfg)
        if raw:
            cleaned = re.sub(r'```json\n?|\n?```', '', raw).strip()
            analysis = json.loads(cleaned)
            return analysis
    except Exception as e:
        print(f"[ERROR] 立场分析失败: {e}")

    return {"error": "分析失败"}


# ═══════════════════════════════════════════════════════════════════
# 主测试流程
# ═══════════════════════════════════════════════════════════════════

def test_foreign_media_search():
    """主测试流程"""

    print("\n" + "="*60)
    print("外媒新闻聚合测试")
    print("="*60)

    # 测试事件（可修改）
    test_keywords_cn = ["台海", "台湾", "军演", "中国"]

    print(f"\n[测试关键词(中文)] {test_keywords_cn}")

    # Step 1: 翻译关键词
    print("\n--- Step 1: 翻译关键词 ---")
    keywords_en = translate_keywords_for_foreign_search(test_keywords_cn)
    print(f"英文关键词: {keywords_en}")

    # 构建不同格式的查询
    query_en = ' '.join(keywords_en)  # 完整查询
    query_short = keywords_en[0] if keywords_en else query_en  # 核心关键词
    query_or = ' OR '.join(keywords_en[:3]) if len(keywords_en) > 1 else query_short  # OR 查询

    print(f"[查询格式] full='{query_en}', short='{query_short}', or='{query_or}'")

    # Step 2: Google News搜索 (长查询)
    print("\n--- Step 2: Google News搜索 ---")
    google_results = search_google_news(query_en)

    # Step 3: RSS订阅源搜索 (关键词列表)
    print("\n--- Step 3: RSS订阅源搜索 ---")
    rss_results = search_rss_sources(keywords_en)

    # Step 4: Twitter搜索 (OR 查询)
    print("\n--- Step 4: Twitter搜索 ---")
    twitter_results = search_twitter(query_or, max_results=30)

    # Step 5: NewsAPI搜索 (长查询)
    print("\n--- Step 5: NewsAPI搜索 ---")
    newsapi_results = search_newsapi(query_en, max_results=20)

    # Step 6: AI 搜索引擎 (长查询)
    print("\n--- Step 6: AI 搜索引擎搜索 ---")
    tavily_results = search_tavily(query_en, max_results=10)
    brave_results = search_brave(query_en, max_results=10)
    perplexity_results = search_perplexity(query_en)

    # Step 7: YouTube 视频搜索 (短查询)
    print("\n--- Step 7: YouTube 视频搜索 ---")
    youtube_results = search_youtube(query_short, max_results=15)

    # 合并结果
    print("\n--- Step 8: 合并去重 ---")
    all_articles = google_results + rss_results + twitter_results + newsapi_results + tavily_results + brave_results + perplexity_results + youtube_results

    # URL去重
    seen_urls = set()
    unique_articles = []
    for art in all_articles:
        url = art.get('url', '')
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_articles.append(art)

    print(f"合并后共 {len(unique_articles)} 条不重复新闻")

    # 按国家分组统计
    by_country = defaultdict(int)
    for art in unique_articles:
        country = art.get('country', art.get('source', 'Unknown'))
        by_country[country] += 1

    print("\n按国家/来源统计:")
    for country, count in sorted(by_country.items(), key=lambda x: -x[1]):
        print(f"  {country}: {count}条")

    # Step 8: 获取全文（可选，耗时）
    print("\n--- Step 8: 获取全文 ---")
    print("[提示] 获取全文较慢，只取前10篇测试")
    test_articles = unique_articles[:10]
    enriched_articles = enrich_articles_content(test_articles)

    # Step 9: 立场分析
    print("\n--- Step 9: 立场分析 ---")
    analysis = None
    if any(a.get('content') for a in enriched_articles):
        analysis = analyze_media_stances(enriched_articles, keywords_en)
        print("\n立场分析结果:")
        print(json.dumps(analysis, indent=2, ensure_ascii=False))
    else:
        print("[WARN] 无全文内容，跳过立场分析")

    # 输出结果
    print("\n" + "="*60)
    print("搜索结果预览")
    print("="*60)

    for i, art in enumerate(unique_articles[:15], 1):
        source = art.get('source', 'Unknown')
        country = art.get('country', '')
        country_tag = f"[{country}]" if country else ""
        print(f"\n{i}. [{source}]{country_tag} {art['title'][:60]}...")
        print(f"   URL: {art['url'][:80]}")

    # 保存结果到JSON
    output_file = "foreign_media_test_result.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'keywords_cn': test_keywords_cn,
            'keywords_en': keywords_en,
            'total_count': len(unique_articles),
            'by_country': dict(by_country),
            'articles': unique_articles[:50],
            'stance_analysis': analysis
        }, f, ensure_ascii=False, indent=2)

    print(f"\n结果已保存到: {output_file}")


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("""
外媒新闻聚合测试脚本

测试内容:
1. Google News RSS搜索
2. 15+主流国际媒体RSS订阅
3. Twitter搜索（twitterapi.io）
4. 立场差异分析（需LLM）

TwitterAPI配置:
1. 获取API Key: https://twitterapi.io（免费500次/月）
2. 设置环境变量: export TWITTERAPI_KEY=your_key
3. 或创建 config/api_keys.json: {"twitterapi_key": "your_key"}

运行:
    python test_foreign_media.py
""")

    # 直接运行，不再需要asyncio（Twitter改用同步API）
    test_foreign_media_search()