import sys
#!/usr/bin/env python3
"""
统一外部搜索接口层

设计目的：
1. 统一不同搜索引擎的输出格式
2. 支持结果存储到 SQLite
3. 为定时关键词搜索提供基础

使用方式：
    from external_fetcher import ExternalFetcher, BingNewsFetcher

    fetcher = BingNewsFetcher(api_key)
    results = fetcher.search("China tariffs", max_results=20)
"""

import hashlib
import json
import re
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional
from abc import ABC, abstractmethod

TZ_BJ = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════════════════════════
# 中国大陆媒体过滤列表（不含港澳台）
# ═══════════════════════════════════════════════════════════════════

# 港澳台媒体白名单（这些媒体保留，不过滤）
HK_MACAU_TAIWAN_WHITELIST = [
    # 香港媒体
    r'scmp', r'scmp\.com', r'south china morning post', r'南华早报',
    r'hket', r'hong kong economic times', r'香港經濟日報',
    r'thestandard\.hk', r'the standard',
    r'epochtimes\.hk',  # 香港大纪元
    # 澳门媒体
    r'macaudaily', r'澳門日報', r'澳门日报',
    r'macaunews',
    # 台湾媒体（自由媒体）
    r'taipeitimes', r'taipei times',  # 台北时报
    r'chinapost', r'china post',  # 中国邮报(台湾)
    r'udn\.com', r'udn', r'联合报', r'聯合報', r'联合新闻网',
    r'ltn\.com\.tw', r'liberty times', r'自由时报', r'自由時報',
    r'appledaily\.tw', r'apple daily', r'蘋果日報', r'苹果日报',
    r'ettoday\.net', r'ettoday', r'东森新闻',
    r'cna\.com\.tw', r'central news agency', r'中央社',  # 台湾中央社
    r'tvbs\.com\.tw', r'tvbs',
    r'businessweekly\.com\.tw',
    r'cw\.com\.tw', r'commonwealth', r'天下杂志',
    r'newtalk\.tw',
]

# 大陆官方媒体
MAINLAND_OFFICIAL_MEDIA = [
    r'xinhua', r'新华社', r'新華社',
    r'people\.cn', r'people\.com', r'人民日报', r'人民日報',
    r'cctv', r'央视', r'央視', r'中央电视',
    r'china\.org\.cn', r'中国网', r'中國網',
    r'gmw\.cn', r'光明日报', r'光明日報',
    r'cnr\.cn', r'央广', r'央廣',
    r'china\.com\.cn', r'中国日报', r'中國日報',
    r'chinadaily', r'china daily',
    r'globaltimes', r'global times', r'环球时报', r'環球時報',
    r'huanqiu\.com', r'环球网',
    r'cankaoxiaoxi', r'参考消息',
    r'bjnews\.com\.cn', r'新京报',
    r'beijingreview', r'北京周报',
    r'cgtn', r'中国国际电视',
    r'chinanews', r'中新社',  # 大陆中新社
    r'youth\.cn', r'中国青年',
    r'mil\.cn', r'中国军网',
    r'dzwww\.com', r'大众网',
    r'ecns', r'ecns\.cn',  # 中国新闻网英文
    r'shine\.cn',  # 上海日报
]

# 大陆商业媒体/门户网站
MAINLAND_COMMERCIAL_MEDIA = [
    r'sina\.com\.cn', r'新浪',  # 注意：sina\.com 可能匹配香港，用\.cn 更精确
    r'qq\.com', r'tencent', r'腾讯', r'騰訊',
    r'sohu\.com', r'搜狐',
    r'163\.com', r'netease', r'网易', r'網易',
    r'ifeng\.com', r'凤凰', r'鳳凰',  # 凤凰网是大陆的
    r'eastday\.com', r'东方网', r'東方網',
    r'zhongguo\.com\.cn',
    r'bjd\.com\.cn',
    r'shnews\.net', r'shanghai',
    r'scuttle\.com',
    r'sznews\.com',
]

# 香港左派媒体（亲大陆，需要过滤）
HK_PRO_CHINA_MEDIA = [
    r'takungpao', r'ta\s*kung\s*pao', r'大公報', r'大公报',
    r'wenweipo', r'wen\s*wei\s*po', r'文匯報', r'文汇报',
    r'orientaldaily', r'oriental\s*daily', r'東方日報', r'东方日报',  # 香港东方日报（亲中）
    r'thetrue\.hk',
]

# 合并所有需要过滤的大陆媒体（含香港左派媒体）
CHINA_MEDIA_PATTERNS = MAINLAND_OFFICIAL_MEDIA + MAINLAND_COMMERCIAL_MEDIA + HK_PRO_CHINA_MEDIA


def is_hk_macau_taiwan_media(source_name: str, url: str = '') -> bool:
    """
    检测是否为港澳台自由媒体（白名单）
    这些媒体应该保留，不过滤
    """
    if not source_name and not url:
        return False

    combined = f"{source_name} {url}".lower()

    for pattern in HK_MACAU_TAIWAN_WHITELIST:
        if re.search(pattern, combined, re.IGNORECASE):
            return True

    return False


def is_mainland_china_media(source_name: str, url: str = '') -> bool:
    """
    检测是否为中国大陆媒体（不含港澳台）

    Args:
        source_name: 媒体名称
        url: 文章URL（可选，用于更精确判断）

    Returns:
        True 如果是大陆媒体
    """
    if not source_name and not url:
        return False

    combined = f"{source_name} {url}".lower()

    # 先检查是否在港澳台白名单中
    if is_hk_macau_taiwan_media(source_name, url):
        return False

    # 检查是否匹配大陆媒体
    for pattern in CHINA_MEDIA_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return True

    # 检查 .cn 域名（但排除明显是港澳台的）
    # 注意：台湾常用 .tw, .com.tw，香港常用 .hk
    if re.search(r'\.cn$', combined):
        # .cn 域名通常是大陆的，除非在白名单中
        return True

    return False


def is_china_media(source_name: str, url: str = '') -> bool:
    """
    检测是否为中国媒体（兼容旧接口，现在只过滤大陆媒体）

    Args:
        source_name: 媒体名称
        url: 文章URL（可选，用于更精确判断）

    Returns:
        True 如果是中国大陆媒体（港澳台媒体返回 False）
    """
    return is_mainland_china_media(source_name, url)


# ═══════════════════════════════════════════════════════════════════
# API Key 管理
# ═══════════════════════════════════════════════════════════════════

def load_api_keys() -> dict:
    """从配置文件加载 API Keys"""
    api_keys_file = Path(__file__).parent.parent / 'config' / 'api_keys.json'
    if not api_keys_file.exists():
        return {}

    try:
        with open(api_keys_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def load_feeds_config() -> dict:
    """从 feeds.yaml 加载配置"""
    import yaml
    feeds_file = Path(__file__).parent.parent / 'config' / 'feeds.yaml'
    if not feeds_file.exists():
        return {}

    try:
        with open(feeds_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception:
        return {}


def get_external_source_config(source_name: str) -> dict:
    """获取指定外部源的完整配置"""
    feeds_config = load_feeds_config()
    external_sources = feeds_config.get('external_sources', [])

    # 标准化名称：将下划线转为空格，便于匹配（如 google_news -> google news）
    normalized_query = source_name.lower().replace('_', ' ')

    # 查找匹配的源
    for source in external_sources:
        source_name_normalized = source.get('name', '').lower().replace('_', ' ')
        if source_name_normalized == normalized_query:
            return source

    return {}


def resolve_api_key(api_key_ref: str, api_keys: dict) -> Optional[str]:
    """
    解析 API Key 引用

    api_key_ref 可能是:
    1. 完整的 API Key (长字符串，如 YouTube 的 serpapi key)
    2. api_keys.json 中的 key 名称 (如 'brave_key', 'perplexity_key')
    3. 特殊格式 (如 Twitter 的 'new1_xxx')
    """
    if not api_key_ref:
        return None

    # 如果看起来像完整的 API Key (长度 > 20 且包含字母数字)
    if len(api_key_ref) > 20:
        return api_key_ref

    # 否则作为 key 名称查找
    return api_keys.get(api_key_ref) or api_keys.get(f"{api_key_ref}_key")


def get_api_key(source_name: str) -> Optional[str]:
    """获取指定源的 API Key（优先从 feeds.yaml 配置读取）"""
    api_keys = load_api_keys()

    # 先从 feeds.yaml external_sources 获取
    source_config = get_external_source_config(source_name)
    if source_config:
        config = source_config.get('config', {})
        api_key_ref = config.get('api_key_ref', '')

        # 解析 api_key_ref
        key = resolve_api_key(api_key_ref, api_keys)
        if key:
            return key

        # 检查 client_id/client_secret (Reddit)
        if source_name.lower() == 'reddit':
            return None  # Reddit 使用 client_id/secret

    # fallback: 从 api_keys.json 直接查找
    key_name = f"{source_name}_key"
    if api_keys.get(key_name):
        return api_keys[key_name]

    # 特殊映射
    mappings = {
        'bing': 'serpapi_key',
        'bing_news': 'serpapi_key',
        'google_custom': 'google_custom_key',
        'brave': 'brave_key',
        'tavily': 'tavily_key',
        'perplexity': 'perplexity_key',
        'newsapi': 'newsapi_key',
        'twitter': 'twitterapi_key',
        'twitterapi': 'twitterapi_key',
        'youtube': 'serpapi_key',
        'google_news': 'serpapi_key'
    }

    mapped_key = mappings.get(source_name.lower())
    if mapped_key and api_keys.get(mapped_key):
        return api_keys[mapped_key]

    # 最后的回退逻辑，如果是 serpapi 相关的引擎，尝试取 serpapi_key 字段
    if source_name.lower() in ('bing', 'bing_news', 'google_news', 'youtube'):
        return api_keys.get('serpapi_key')

    return None


# ═══════════════════════════════════════════════════════════════════
# 基类定义
# ═══════════════════════════════════════════════════════════════════

class ExternalFetcher(ABC):
    """外部搜索源基类"""

    SOURCE_TYPE: str = "unknown"

    def __init__(self, api_key: Optional[str] = None, config: Optional[dict] = None):
        self.api_key = api_key
        self.config = config or {}
        self.filter_china_media = True  # 默认过滤中国媒体

    @abstractmethod
    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        """
        执行搜索

        Args:
            query: 搜索关键词
            max_results: 最大结果数

        Returns:
            标准化的结果列表
        """
        pass

    def filter_results(self, results: List[Dict]) -> List[Dict]:
        """
        过滤结果（默认过滤中国媒体）

        Args:
            results: 原始结果列表

        Returns:
            过滤后的结果列表
        """
        if not self.filter_china_media:
            return results

        filtered = [r for r in results if not r.get('is_china_media', False)]
        if len(filtered) < len(results):
            print(f"[INFO] 已过滤 {len(results) - len(filtered)} 条中国媒体结果", file=sys.stderr)
        return filtered

    def normalize_result(self, raw: Dict) -> Dict:
        """
        将原始结果标准化为统一格式

        输出格式：
        {
            'title': str,          # 标题
            'url': str,            # 链接
            'published': str,      # ISO 时间（优先北京时间）
            'platform': str,       # 来源标识
            'summary': str,        # 摘要
            'source_type': str,    # 搜索源类型
            'is_china_media': bool, # 是否为中国媒体
            'raw_metadata': dict   # 原始元数据（可选）
        }
        """
        platform = raw.get('source', raw.get('platform', ''))
        url = raw.get('url', '')

        return {
            'title': raw.get('title', ''),
            'url': url,
            'published': self._normalize_time(raw.get('published', '')),
            'platform': platform,
            'summary': raw.get('summary', raw.get('content', ''))[:500] if raw.get('summary') or raw.get('content') else '',
            'source_type': self.SOURCE_TYPE,
            'is_china_media': is_china_media(platform, url),
            'raw_metadata': raw.get('raw_metadata', {})
        }

    def should_filter(self, result: Dict, filter_china_media: bool = True) -> bool:
        """
        判断结果是否应该被过滤

        Args:
            result: 标准化后的结果
            filter_china_media: 是否过滤中国媒体

        Returns:
            True 如果应该过滤掉
        """
        if filter_china_media and result.get('is_china_media', False):
            return True
        return False

    def _normalize_time(self, time_str: str) -> str:
        """标准化时间格式为北京时间 ISO 字符串"""
        if not time_str:
            return datetime.now(TZ_BJ).isoformat()

        time_str = str(time_str).strip()

        # 1. ISO 格式 (如 2026-04-02T22:00:57Z 或 2026-04-07T15:30:00+08:00)
        if 'T' in time_str:
            try:
                dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                return dt.astimezone(TZ_BJ).isoformat()
            except Exception:
                pass  # 继续尝试其他格式

        # 2. SerpApi Google News 格式 (如 "04/07/2026, 03:26 PM, +0000 UTC")
        if re.match(r'\d{2}/\d{2}/\d{4}', time_str):
            try:
                match = re.match(
                    r'(\d{2})/(\d{2})/(\d{4}),\s*(\d{2}):(\d{2})\s*(AM|PM),?\s*([+-]\d{4})?\s*UTC?',
                    time_str
                )
                if match:
                    month, day, year, hour, minute, ampm, tz_offset = match.groups()
                    hour = int(hour)
                    if ampm.upper() == 'PM' and hour != 12:
                        hour += 12
                    elif ampm.upper() == 'AM' and hour == 12:
                        hour = 0
                    dt = datetime(int(year), int(month), int(day), hour, int(minute))
                    return dt.astimezone(TZ_BJ).isoformat()
            except Exception:
                pass

            # 3. 相对时间格式 (如 "48m", "2 hours ago", "3 days ago")
        relative_match = re.match(r'(\d+)\s*(minute|min|hour|hr|day|week|month)s?\s*ago', time_str, re.I)
        if relative_match:
            value = int(relative_match.group(1))
            unit = relative_match.group(2).lower()

            if unit in ('minute', 'min'):
                return (datetime.now(TZ_BJ) - timedelta(minutes=value)).isoformat()
            elif unit in ('hour', 'hr'):
                return (datetime.now(TZ_BJ) - timedelta(hours=value)).isoformat()
            elif unit == 'day':
                return (datetime.now(TZ_BJ) - timedelta(days=value)).isoformat()
            elif unit == 'week':
                return (datetime.now(TZ_BJ) - timedelta(weeks=value)).isoformat()
            elif unit == 'month':
                return (datetime.now(TZ_BJ) - timedelta(days=value*30)).isoformat()

        # 4. 简短相对时间 (如 "48m", "2h", "3d")
        short_relative = re.match(r'^(\d+)(m|h|d)$', time_str)
        if short_relative:
            value = int(short_relative.group(1))
            unit = short_relative.group(2)
            if unit == 'm':
                return (datetime.now(TZ_BJ) - timedelta(minutes=value)).isoformat()
            elif unit == 'h':
                return (datetime.now(TZ_BJ) - timedelta(hours=value)).isoformat()
            elif unit == 'd':
                return (datetime.now(TZ_BJ) - timedelta(days=value)).isoformat()

        # 5. 其他常见格式
        # "Apr 7, 2026" 格式
        if re.match(r'^[A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4}', time_str):
            try:
                dt = datetime.strptime(time_str, '%b %d, %Y')
                return dt.astimezone(TZ_BJ).isoformat()
            except Exception:
                pass

        # 默认返回当前时间
        return datetime.now(TZ_BJ).isoformat()

    def generate_url_hash(self, url: str) -> str:
        """生成 URL 唯一哈希（用于去重）"""
        return hashlib.sha1(url.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════
# Bing News Search 实现 (SerpApi)
# ═══════════════════════════════════════════════════════════════════

class BingNewsFetcher(ExternalFetcher):
    """Bing News Search API (SerpApi)"""

    SOURCE_TYPE = "bing_news"
    ENDPOINT = "https://serpapi.com/search"

    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        if not self.api_key:
            print(f"[WARN] Bing News: 无 API Key")
            return []

        try:
            params = {
                'engine': 'bing_news',
                'q': query,
                'api_key': self.api_key
            }
            if self.config.get('market'):
                params['cc'] = self.config.get('market')

            resp = requests.get(self.ENDPOINT, params=params, timeout=15)

            if resp.status_code != 200:
                print(f"[WARN] Bing News 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            # SerpApi Bing News 返回结果在 organic_results 中
            for item in data.get('organic_results', []):
                raw = {
                    'title': item.get('title', ''),
                    'url': item.get('link', ''),
                    'published': item.get('date', ''),
                    'source': item.get('source', 'Bing News'),
                    'summary': item.get('snippet', ''),
                    'raw_metadata': {
                        'thumbnail': item.get('thumbnail', '')
                    }
                }
                results.append(self.normalize_result(raw))
                if len(results) >= max_results:
                    break

            print(f"[INFO] Bing News: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] Bing News 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# Google Custom Search 实现
# ═══════════════════════════════════════════════════════════════════

class GoogleCustomFetcher(ExternalFetcher):
    """Google Custom Search API"""

    SOURCE_TYPE = "google_custom"
    ENDPOINT = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: Optional[str] = None, config: Optional[dict] = None):
        super().__init__(api_key, config)
        self.cx = config.get('cx') or self._get_cx()

    def _get_cx(self) -> Optional[str]:
        """获取 Custom Search Engine ID"""
        keys = load_api_keys()
        return keys.get('google_custom_cx')

    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        if not self.api_key or not self.cx:
            print(f"[WARN] Google Custom: 无 API Key 或 CX")
            return []

        try:
            params = {
                'key': self.api_key,
                'cx': self.cx,
                'q': query,
                'num': max_results,
                'sort': 'date'  # 按日期排序
            }

            resp = requests.get(self.ENDPOINT, params=params, timeout=15)

            if resp.status_code != 200:
                print(f"[WARN] Google Custom 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            for item in data.get('items', []):
                raw = {
                    'title': item.get('title', ''),
                    'url': item.get('link', ''),
                    'published': item.get('pagemap', {}).get('metatags', [{}])[0].get('article:published_time', ''),
                    'source': item.get('displayLink', 'Google'),
                    'summary': item.get('snippet', ''),
                    'raw_metadata': {
                        'mime': item.get('mime', ''),
                        'fileFormat': item.get('fileFormat', '')
                    }
                }
                results.append(self.normalize_result(raw))

            print(f"[INFO] Google Custom: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] Google Custom 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# Brave Search 实现
# ═══════════════════════════════════════════════════════════════════

class BraveSearchFetcher(ExternalFetcher):
    """Brave Search API"""

    SOURCE_TYPE = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        if not self.api_key:
            print(f"[WARN] Brave: 无 API Key")
            return []

        try:
            headers = {
                'Accept': 'application/json',
                'X-Subscription-Token': self.api_key
            }
            params = {
                'q': query,
                'count': min(max_results, 20)
            }

            resp = requests.get(self.ENDPOINT, headers=headers, params=params, timeout=15)

            if resp.status_code != 200:
                print(f"[WARN] Brave 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            for item in data.get('web', {}).get('results', []):
                raw = {
                    'title': item.get('title', ''),
                    'url': item.get('url', ''),
                    'published': item.get('age', ''),
                    'source': item.get('meta_url', {}).get('hostname', 'Brave'),
                    'summary': item.get('description', ''),
                    'raw_metadata': {}
                }
                results.append(self.normalize_result(raw))

            print(f"[INFO] Brave: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] Brave 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# Tavily 实现
# ═══════════════════════════════════════════════════════════════════

class TavilyFetcher(ExternalFetcher):
    """Tavily AI 搜索引擎"""

    SOURCE_TYPE = "tavily"
    ENDPOINT = "https://api.tavily.com/search"

    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        if not self.api_key:
            print(f"[WARN] Tavily: 无 API Key")
            return []

        try:
            payload = {
                'api_key': self.api_key,
                'query': query,
                'search_depth': 'basic',
                'include_answer': False,
                'include_raw_content': False,
                'include_images': False,
                'max_results': max_results
            }

            resp = requests.post(self.ENDPOINT, json=payload, timeout=30)

            if resp.status_code != 200:
                print(f"[WARN] Tavily 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            for item in data.get('results', []):
                raw = {
                    'title': item.get('title', ''),
                    'url': item.get('url', ''),
                    'published': item.get('published_date', ''),
                    'source': 'Tavily',
                    'summary': item.get('content', ''),
                    'raw_metadata': {
                        'score': item.get('score', 0)
                    }
                }
                results.append(self.normalize_result(raw))

            # AI Answer 作为额外信息
            ai_answer = data.get('answer', '')
            if ai_answer:
                print(f"[INFO] Tavily AI Answer: {ai_answer[:100]}...", file=sys.stderr)

            print(f"[INFO] Tavily: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] Tavily 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# Twitter/X 搜索实现 (TwitterAPI.io)
# ═══════════════════════════════════════════════════════════════════

class TwitterFetcher(ExternalFetcher):
    """Twitter/X 搜索 (TwitterAPI.io)"""

    SOURCE_TYPE = "twitter"
    ENDPOINT = "https://api.twitterapi.io/twitter/tweet/advanced_search"

    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        if not self.api_key:
            print(f"[WARN] Twitter: 无 API Key")
            return []

        try:
            # TwitterAPI.io 使用 X-API-Key header，GET 请求
            headers = {
                'X-API-Key': self.api_key
            }
            params = {
                'query': query,
                'queryType': 'Latest'
            }

            resp = requests.get(self.ENDPOINT, headers=headers, params=params, timeout=30)

            if resp.status_code != 200:
                print(f"[WARN] Twitter 返回 {resp.status_code}: {resp.text[:100]}")
                return []

            data = resp.json()
            results = []

            tweets = data.get('tweets', []) or data.get('data', [])
            for tweet in tweets[:max_results]:
                raw = {
                    'title': tweet.get('text', '')[:100] + '...',
                    'url': f"https://twitter.com/user/status/{tweet.get('id', '')}",
                    'published': tweet.get('created_at', ''),
                    'source': tweet.get('user', {}).get('screen_name', 'Twitter'),
                    'summary': tweet.get('text', ''),
                    'raw_metadata': {
                        'likes': tweet.get('favorite_count', 0),
                        'retweets': tweet.get('retweet_count', 0),
                        'user_followers': tweet.get('user', {}).get('followers_count', 0)
                    }
                }
                results.append(self.normalize_result(raw))

            print(f"[INFO] Twitter: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] Twitter 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# YouTube 搜索实现 (SerpApi)
# ═══════════════════════════════════════════════════════════════════

class YouTubeFetcher(ExternalFetcher):
    """YouTube 视频搜索 (SerpApi)"""

    SOURCE_TYPE = "youtube"
    ENDPOINT = "https://serpapi.com/search"

    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        if not self.api_key:
            print(f"[WARN] YouTube: 无 API Key (SerpApi)")
            return []

        try:
            params = {
                'engine': 'youtube',
                'search_query': query,
                'api_key': self.api_key
            }

            resp = requests.get(self.ENDPOINT, params=params, timeout=30)

            if resp.status_code != 200:
                print(f"[WARN] YouTube 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            videos = data.get('video_results', [])
            for video in videos[:max_results]:
                raw = {
                    'title': video.get('title', ''),
                    'url': video.get('link', ''),
                    'published': video.get('published_date', ''),
                    'source': video.get('channel', {}).get('name', 'YouTube'),
                    'summary': video.get('description', '')[:500],
                    'raw_metadata': {
                        'views': video.get('views', 0),
                        'thumbnail': video.get('thumbnail', {}).get('static', ''),
                        'duration': video.get('duration', '')
                    }
                }
                results.append(self.normalize_result(raw))

            print(f"[INFO] YouTube: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] YouTube 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# Reddit 搜索实现 (官方 API)
# ═══════════════════════════════════════════════════════════════════

class RedditFetcher(ExternalFetcher):
    """Reddit 帖子搜索 (官方 API)"""

    SOURCE_TYPE = "reddit"
    ENDPOINT = "https://oauth.reddit.com/search"

    def __init__(self, api_key: Optional[str] = None, config: Optional[dict] = None):
        super().__init__(api_key, config)
        self.client_id = config.get('client_id', '') if config else ''
        self.client_secret = config.get('client_secret', '') if config else ''
        self._access_token = None

    def _get_access_token(self) -> Optional[str]:
        """获取 Reddit OAuth token"""
        if self._access_token:
            return self._access_token

        if not self.client_id or not self.client_secret:
            return None

        try:
            auth = requests.auth.HTTPBasicAuth(self.client_id, self.client_secret)
            resp = requests.post(
                'https://www.reddit.com/api/v1/access_token',
                auth=auth,
                data={'grant_type': 'client_credentials'},
                headers={'User-Agent': 'rss-news/1.0'},
                timeout=15
            )

            if resp.status_code == 200:
                self._access_token = resp.json().get('access_token')
                return self._access_token
        except Exception as e:
            print(f"[ERROR] Reddit 认证失败: {e}")

        return None

    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        token = self._get_access_token()
        if not token:
            print(f"[WARN] Reddit: 无有效认证")
            return []

        try:
            headers = {
                'Authorization': f'Bearer {token}',
                'User-Agent': 'rss-news/1.0'
            }
            params = {
                'q': query,
                'limit': min(max_results, 100),
                'sort': 'relevance',
                'type': 'link'
            }

            resp = requests.get(self.ENDPOINT, headers=headers, params=params, timeout=15)

            if resp.status_code != 200:
                print(f"[WARN] Reddit 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            posts = data.get('data', {}).get('children', [])
            for post in posts[:max_results]:
                p = post.get('data', {})
                raw = {
                    'title': p.get('title', ''),
                    'url': f"https://reddit.com{p.get('permalink', '')}",
                    'published': datetime.fromtimestamp(p.get('created_utc', 0), tz=TZ_BJ).isoformat(),
                    'source': f"r/{p.get('subreddit', 'reddit')}",
                    'summary': p.get('selftext', '')[:500] or p.get('title', ''),
                    'raw_metadata': {
                        'score': p.get('score', 0),
                        'comments': p.get('num_comments', 0),
                        'author': p.get('author', '')
                    }
                }
                results.append(self.normalize_result(raw))

            print(f"[INFO] Reddit: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] Reddit 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# NewsAPI 搜索实现
# ═══════════════════════════════════════════════════════════════════

class NewsAPIFetcher(ExternalFetcher):
    """NewsAPI.org 新闻聚合搜索"""

    SOURCE_TYPE = "newsapi"
    ENDPOINT = "https://newsapi.org/v2/everything"

    def search(self, query: str, max_results: int = 20, hours: int = None) -> List[Dict]:
        if not self.api_key:
            print(f"[WARN] NewsAPI: 无 API Key")
            return []

        try:
            params = {
                'q': query,
                'pageSize': min(max_results, 100),
                'sortBy': 'publishedAt',
                'language': 'en',
                'apiKey': self.api_key
            }

            # NewsAPI 支持时间参数
            if hours:
                now = datetime.now(TZ_BJ)
                start = now - timedelta(hours=hours)
                params['from'] = start.strftime('%Y-%m-%dT%H:%M:%S')
                params['to'] = now.strftime('%Y-%m-%dT%H:%M:%S')

            resp = requests.get(self.ENDPOINT, params=params, timeout=15)

            if resp.status_code != 200:
                print(f"[WARN] NewsAPI 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            for article in data.get('articles', [])[:max_results]:
                raw = {
                    'title': article.get('title', ''),
                    'url': article.get('url', ''),
                    'published': article.get('publishedAt', ''),
                    'source': article.get('source', {}).get('name', 'NewsAPI'),
                    'summary': article.get('description', '') or article.get('content', '')[:500],
                    'raw_metadata': {
                        'author': article.get('author', ''),
                        'image': article.get('urlToImage', '')
                    }
                }
                results.append(self.normalize_result(raw))

            print(f"[INFO] NewsAPI: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] NewsAPI 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# Perplexity AI 搜索实现
# ═══════════════════════════════════════════════════════════════════

class PerplexityFetcher(ExternalFetcher):
    """Perplexity AI 联网搜索"""

    SOURCE_TYPE = "perplexity"
    ENDPOINT = "https://api.perplexity.ai/chat/completions"

    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        if not self.api_key:
            print(f"[WARN] Perplexity: 无 API Key")
            return []

        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            payload = {
                'model': 'llama-3.1-sonar-small-128k-online',
                'messages': [
                    {
                        'role': 'system',
                        'content': '你是一个新闻搜索助手。请搜索并提供最新的相关新闻，包含标题、来源和链接。'
                    },
                    {
                        'role': 'user',
                        'content': f'搜索关于"{query}"的最新新闻，列出{max_results}条。格式：每条包含标题、来源、链接、时间。'
                    }
                ],
                'max_tokens': 2000
            }

            resp = requests.post(self.ENDPOINT, headers=headers, json=payload, timeout=60)

            if resp.status_code != 200:
                print(f"[WARN] Perplexity 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            # Perplexity 返回的是对话格式，解析内容
            content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            citations = data.get('citations', [])

            # 简单解析：将内容和引用组合
            if content:
                raw = {
                    'title': f'Perplexity AI: {query}',
                    'url': citations[0] if citations else '',
                    'published': datetime.now(TZ_BJ).isoformat(),
                    'source': 'Perplexity AI',
                    'summary': content[:1000],
                    'raw_metadata': {
                        'citations': citations,
                        'model': 'sonar'
                    }
                }
                results.append(self.normalize_result(raw))

            print(f"[INFO] Perplexity: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] Perplexity 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# Google News 实现 (SerpApi)
# ═══════════════════════════════════════════════════════════════════

class GoogleNewsFetcher(ExternalFetcher):
    """Google News API (SerpApi)"""

    SOURCE_TYPE = "google_news"
    ENDPOINT = "https://serpapi.com/search"

    def search(self, query: str, max_results: int = 20) -> List[Dict]:
        if not self.api_key:
            print(f"[WARN] Google News: 无 API Key")
            return []

        try:
            params = {
                'engine': 'google_news',
                'q': query,
                'api_key': self.api_key,
                'gl': self.config.get('country', 'US'),
                'hl': self.config.get('lang', 'en')
            }

            resp = requests.get(self.ENDPOINT, params=params, timeout=15)

            if resp.status_code != 200:
                print(f"[WARN] Google News 返回 {resp.status_code}")
                return []

            data = resp.json()
            results = []

            for item in data.get('news_results', []):
                # 过滤掉无效结果：
                # 1. 没有链接的（如聚合卡片 "News about...")
                # 2. 链接是 Google 内部页面的
                link = item.get('link', '')
                if not link:
                    continue
                if link.startswith('https://news.google.com/') or link.startswith('https://www.google.com/'):
                    # 跳过 Google 内部聚合页面
                    continue

                source_obj = item.get('source', {})
                source_name = source_obj.get('name', 'Google News') if isinstance(source_obj, dict) else str(source_obj)
                if not source_name:
                    source_name = 'Google News'

                raw = {
                    'title': item.get('title', ''),
                    'url': link,
                    'published': item.get('date', ''),
                    'source': source_name,
                    'summary': item.get('snippet', ''),
                    'raw_metadata': {
                        'thumbnail': item.get('thumbnail', '')
                    }
                }
                results.append(self.normalize_result(raw))
                if len(results) >= max_results:
                    break

            print(f"[INFO] Google News: 找到 {len(results)} 条结果", file=sys.stderr)
            return results

        except Exception as e:
            print(f"[ERROR] Google News 搜索失败: {e}")
            return []


# ═══════════════════════════════════════════════════════════════════
# 统一搜索接口
# ═══════════════════════════════════════════════════════════════════

class UnifiedSearcher:
    """统一搜索接口，支持多源并发搜索"""

    FETCHER_MAP = {
        'bing': BingNewsFetcher,
        'bing_news': BingNewsFetcher,
        'google_custom': GoogleCustomFetcher,
        'brave': BraveSearchFetcher,
        'brave search': BraveSearchFetcher,
        'tavily': TavilyFetcher,
        'google_news': GoogleNewsFetcher,
        'google news': GoogleNewsFetcher,
        'google': GoogleNewsFetcher,
        'rss': GoogleNewsFetcher,
        'twitter': TwitterFetcher,
        'twitterapi': TwitterFetcher,
        'youtube': YouTubeFetcher,
        'reddit': RedditFetcher,
        'newsapi': NewsAPIFetcher,
        'perplexity': PerplexityFetcher,
    }

    def __init__(self):
        self.api_keys = load_api_keys()

    def get_fetcher(self, source: str) -> Optional[ExternalFetcher]:
        """获取指定源的 Fetcher 实例（从 feeds.yaml 读取配置）"""
        fetcher_class = self.FETCHER_MAP.get(source.lower())
        if not fetcher_class:
            print(f"[WARN] 未知的搜索源: {source}")
            return None

        # 从 feeds.yaml external_sources 获取完整配置
        source_config = get_external_source_config(source)
        config = source_config.get('config', {}) if source_config else {}

        # 获取 API Key
        api_key = get_api_key(source)

        # 特殊配置处理
        if source.lower() in ['bing', 'bing_news']:
            config['market'] = config.get('country', 'us')
        elif source.lower() == 'google_custom':
            config['cx'] = self.api_keys.get('google_custom_cx')
        elif source.lower() == 'reddit':
            # Reddit 使用 client_id/client_secret
            config['client_id'] = config.get('client_id', '') or self.api_keys.get('reddit_client_id', '')
            config['client_secret'] = config.get('client_secret', '') or self.api_keys.get('reddit_client_secret', '')
        elif source.lower() in ['youtube', 'google_news', 'bing_news']:
            # 这些引擎使用 SerpApi，api_key_ref 是 serpapi key
            if not api_key:
                api_key = resolve_api_key(config.get('api_key_ref', ''), self.api_keys)


        return fetcher_class(api_key=api_key, config=config)

    def search(self, query: str, sources: List[str] = None, max_results: int = 20,
               hours: int = 24, optimize_keyword: bool = True) -> List[Dict]:
        """
        多源并发搜索

        Args:
            query: 搜索关键词
            sources: 搜索源列表（默认 ['google_news']）
            max_results: 每个源的最大结果数
            hours: 时间窗口（小时），用于关键字优化
            optimize_keyword: 是否智能优化关键字

        Returns:
            合并后的结果列表（已去重）
        """
        if not sources:
            sources = ['google_news']  # 默认使用免费的 Google News

        all_results = []
        url_hashes = set()

        for source in sources:
            fetcher = self.get_fetcher(source)
            if not fetcher:
                continue

            # 智能关键字优化
            search_query = query
            time_filter = None

            if optimize_keyword and hours:
                try:
                    from search_keyword_optimizer import optimize_search_query, get_engine_type
                    opt_result = optimize_search_query(
                        keyword=query,
                        hours=hours,
                        engine=source,
                        use_llm=False  # 暂时不用LLM，避免延迟
                    )
                    search_query = opt_result.get('optimized_keyword', query)
                    time_filter = opt_result.get('time_filter')
                    print(f"[INFO] {source}: 关键字优化 '{query}' -> '{search_query}'", file=sys.stderr)
                except Exception as e:
                    print(f"[WARN] 关键字优化失败: {e}")

            # 根据引擎类型决定是否传递 hours 参数
            engine_type = get_engine_type(source) if optimize_keyword else 'search_engine'
            if engine_type == 'news_aggregator' and hours:
                # NewsAPI 等支持时间参数的引擎
                results = fetcher.search(search_query, max_results, hours=hours)
            else:
                results = fetcher.search(search_query, max_results)

            # 去重
            for r in results:
                url_hash = hashlib.sha1(r.get('url', '').encode()).hexdigest()[:16]
                if url_hash not in url_hashes:
                    url_hashes.add(url_hash)
                    all_results.append(r)

        # 按时间排序
        all_results.sort(key=lambda x: x.get('published', ''), reverse=True)

        return all_results[:max_results * len(sources)]  # 限制总结果数


# ═══════════════════════════════════════════════════════════════════
# CLI 测试入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='统一外部搜索接口')
    parser.add_argument('--query', type=str, required=True, help='搜索关键词')
    parser.add_argument('--sources', type=str, default='google_news',
                        help='搜索源（逗号分隔）：bing,brave,tavily,google_news')
    parser.add_argument('--max', type=int, default=10, help='最大结果数')
    parser.add_argument('--json', action='store_true', help='JSON格式输出')

    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(',')]
    searcher = UnifiedSearcher()
    results = searcher.search(args.query, sources=sources, max_results=args.max)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"\n搜索: {args.query}")
        print(f"来源: {sources}")
        print(f"结果: {len(results)} 条")
        print("-" * 50)
        for i, r in enumerate(results, 1):
            print(f"{i}. [{r['source_type']}] {r['title'][:60]}")
            print(f"   时间: {r['published'][:16]}")
            print(f"   链接: {r['url'][:70]}")
            print()