#!/usr/bin/env python3
"""RSS聚合：curl拉取 → 同源宽松去重 → 时间段过滤 → SQLite入库 → JSON输出
依赖：pip install feedparser pyyaml
"""

import argparse, hashlib, json, re, subprocess, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import concurrent.futures
import urllib.request
import ssl

import feedparser, yaml
import requests  # 新增 requests 用于 Jina API
import threading
from llm_tagger import is_english_text, batch_translate_to_chinese, translate_text, load_llm_config, get_translation_config, extract_chinese_outline, batch_extract_outlines

progress_lock = threading.Lock()

TZ_BJ = timezone(timedelta(hours=8))

# --- Jina 内容清洗工具 ---
def clean_jina_content(content: str, max_length: int = 8000) -> str:
    """
    清洗 Jina Reader 抓取的内容，去除导航、广告等噪音，只保留正文。

    清洗策略：
    1. 剔除 Markdown 格式的导航链接和图片
    2. 剔除常见导航/菜单关键词段落
    3. 剔除广告、订阅、社交分享等无意义文字
    4. 去除过多连续空行
    5. 截取正文核心段落
    """
    if not content or len(content) < 100:
        return content or ''

    # 定义需要剔除的噪音关键词（导航、广告、页脚等）
    noise_keywords = [
        # 导航菜单
        'skip to content', 'skip to main', 'jump to', 'navigation', 'menu', 'sidebar',
        'home', 'about us', 'contact', 'login', 'sign up', 'subscribe', 'register',
        'search', 'sitemap', 'rss feed', 'follow us', 'back to top',
        # 广告与订阅
        'advertisement', 'advertising', 'sponsor', 'sponsored', 'promo', 'promotion',
        'newsletter', 'email newsletter', 'get our newsletter', 'sign up for',
        'subscribe to our', 'subscription', 'premium', 'paywall',
        # 社交分享
        'share this article', 'share on facebook', 'share on twitter', 'share on linkedin',
        'share via email', 'share on whatsapp', 'share on telegram', 'social media',
        'follow on twitter', 'follow on facebook', 'follow us on',
        # 页脚版权
        'copyright', '©', 'all rights reserved', 'terms of use', 'privacy policy',
        'cookie policy', 'disclaimer', 'terms and conditions', 'legal notice',
        'footer', 'footer menu', 'site footer',
        # 无意义段落
        'related articles', 'you may also like', 'recommended for you', 'trending',
        'most popular', 'latest news', 'breaking news', 'read more', 'click here',
        'view all', 'see also', 'also read', 'continue reading',
        # 多语言导航
        '首页', '导航', '菜单', '登录', '注册', '订阅', '联系我们', '关于我们',
        '版权所有', '广告', '推广', '分享到', '关注我们', '返回顶部',
        '相关文章', '推荐阅读', '热门新闻', '点击查看', '更多内容',
    ]

    # 导航频道关键词（通常是网站导航栏）
    nav_channel_keywords = [
        '早报俱乐部', '电子报', '新加坡股市', '新加坡财经', '全球财经', '中国财经',
        '投资理财', '房产', '美国股市', '中小企业', '起步创新', '财经人物',
        '东南亚', '言论', '社论', '评论', '交流站', '漫画',
        '娱乐', '明星', '影视', '音乐', '韩流', '送礼',
        '生活', '壮龄go', '特写', '美食', '旅行', '文化艺术', '人文史地',
        '专栏', '生态与环保', '时尚与美容', '设计与家居', '光影', '科玩', '科普',
        '汽车', '心事家事', '精选', '特辑', '早报校园', '热门', '生活贴士', '星座与生肖',
        '保健', '体育', '视频', '新闻', '系列节目', '直播', '播客', '互动新闻', '专题',
        'realtime', 'singapore', 'world', 'china', 'finance', 'sports', 'entertainment',
        'lifestyle', 'video', 'podcast', 'opinion', 'forum',
    ]

    lines = content.split('\n')
    cleaned_lines = []
    in_article_body = False  # 标记是否已进入正文区域

    for line in lines:
        original_line = line
        line_lower = line.lower().strip()

        # 跳过空行（但允许保留少量用于段落分隔）
        if not line_lower:
            continue

        # === 过滤 Markdown 格式的噪音 ===

        # 过滤图片链接 [![Image...](...)]](...)
        if re.match(r'^\[!\[Image', line):
            continue

        # 过滤纯链接行 [文字](URL)
        if re.match(r'^\[([^\]]+)\]\(https?://[^\)]+\)$', line):
            # 检查是否是导航频道链接
            link_text = re.search(r'\[([^\]]+)\]', line)
            if link_text:
                text = link_text.group(1).lower()
                # 如果链接文本是导航频道，跳过
                if any(kw in text for kw in nav_channel_keywords):
                    continue
                # 如果链接文本很短（通常是导航），跳过
                if len(text) < 15 and not any(c in text for c in ['：', ':', '。', '.', '！', '!']):
                    continue

        # 过滤 blob: URL（本地图片）
        if 'blob:http' in line_lower:
            continue

        # 过滤纯 URL 行
        if line_lower.startswith('http://') or line_lower.startswith('https://'):
            if len(line_lower) < 100 and not any(kw in line_lower for kw in ['source', '来源', 'reference']):
                continue

        # === 过滤导航相关内容 ===

        # 检测发布时间行，之后的内容才是正文
        if re.search(r'发布[/_]?\d{4}年\d{1,2}月\d{1,2}日', line):
            in_article_body = True
            cleaned_lines.append(original_line)
            continue

        # 在正文区域之前，跳过导航链接
        if not in_article_body:
            # 检查是否是导航密集行（包含多个链接）
            link_count = len(re.findall(r'\[([^\]]+)\]\(', line))
            if link_count >= 3:
                continue
            # 检查是否包含导航关键词
            if any(kw in line_lower for kw in nav_channel_keywords):
                continue

        # 跳过噪音关键词行
        is_noise = False
        for kw in noise_keywords:
            if kw in line_lower:
                if len(line_lower) < 150:
                    is_noise = True
                    break
                if line_lower.startswith(kw):
                    is_noise = True
                    break

        if is_noise:
            continue

        # 跳过过短的独立行（通常是按钮或标签）
        if len(line_lower) < 15 and not any(c in line_lower for c in ['：', ':', '。', '.', '！', '!', '？', '?']):
            continue

        # 保留有效行
        cleaned_lines.append(original_line)

    # 合并清洗后的内容
    result = '\n'.join(cleaned_lines)

    # 去除过多连续空行，保留最多 2 个连续换行
    result = re.sub(r'\n{3,}', '\n\n', result)

    # 截断过长的内容（保留核心正文）
    if len(result) > max_length:
        # 尝试在段落边界截断
        truncate_pos = result[:max_length].rfind('\n\n')
        if truncate_pos > max_length * 0.6:
            result = result[:truncate_pos] + '\n...(内容过长已截断)'
        else:
            result = result[:max_length] + '\n...(内容过长已截断)'

    return result.strip()


BASE  = Path(__file__).parent.parent
CFG   = BASE / "config/feeds.yaml"
STATE = BASE / "config/state.json"


def parse_datetime_guess(value: str) -> datetime | None:
    """Try multiple strategies to normalize arbitrary datetime strings into BJ timezone."""
    if not value:
        return None
    s = value.strip()
    if not s:
        return None

    # Normalize common Chinese/Japanese delimiters
    replacements = {
        '年': '-',
        '月': '-',
        '日': ' ',
        '号': ' ',
        '．': '.',
        '。': '.',
    }
    for src, dst in replacements.items():
        s = s.replace(src, dst)
    s = re.sub(r'(\d)(st|nd|rd|th)', r'\1', s, flags=re.IGNORECASE)
    s = s.replace('上午', 'AM').replace('下午', 'PM').replace('午夜', 'AM').replace('中午', 'PM')
    s = s.replace('，', ' ').replace('：', ':')
    s = re.sub(r'GMT[+-]\d{1,2}', '', s, flags=re.IGNORECASE)
    s = re.sub(r'UTC[+-]\d{1,2}', '', s, flags=re.IGNORECASE)

    try:
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        dt = datetime.fromisoformat(s)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=TZ_BJ)
        return dt.astimezone(TZ_BJ)
    except Exception:
        pass

    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt:
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(TZ_BJ)
    except Exception:
        pass

    date_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y %H:%M",
        "%b %d, %Y",
        "%d %b %Y %H:%M",
        "%d %b %Y",
    ]
    for fmt in date_formats:
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=TZ_BJ)
            return dt
        except Exception:
            continue
    return None

# ── curl 拉取 ─────────────────────────────────────────────

def curl_fetch(url: str, timeout: int = 30) -> bytes:
    cmd = [
        'curl', '-sL', '--max-time', str(timeout), '--compressed',
        '--retry', '2', '--retry-delay', '1',
        '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        '-H', 'Accept: application/rss+xml,application/xml,text/xml,*/*;q=0.8',
        '-H', 'Accept-Language: zh-CN,zh;q=0.9,en;q=0.8',
        '-H', 'Cache-Control: no-cache',
        url
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'curl 超时 ({timeout}s)')
    if r.returncode != 0:
        raise RuntimeError(f'curl 失败({r.returncode}): {r.stderr.decode(errors="replace")[:120]}')
    if not r.stdout:
        raise RuntimeError('curl 返回空内容')
    head = r.stdout[:300].lower()
    if b'<html' in head and b'<rss' not in head and b'<feed' not in head:
        raise RuntimeError('返回了 HTML 而非 RSS（可能被重定向或封锁）')
    return r.stdout

# ── 工具函数 ─────────────────────────────────────────────

def uhash(url: str) -> str:
    url = url.strip().lower()
    url = re.sub(r'[?&](utm_[^&]*|ref=[^&]*|source=[^&]*|campaign=[^&]*)', '', url)
    url = url.rstrip('/?&')
    return hashlib.sha1(url.encode()).hexdigest()[:12]

def thash(title: str) -> str:
    return hashlib.sha1(re.sub(r'[\s\W]+', '', title.lower()).encode()).hexdigest()[:12]

def bigram_sim(a: str, b: str) -> float:
    def bg(s):
        s = re.sub(r'\s+', '', s.lower())
        return set(s[i:i+2] for i in range(len(s) - 1))
    ba, bb = bg(a), bg(b)
    return len(ba & bb) / len(ba | bb) if ba and bb else 0.0

def strip_html(s: str) -> str:
    s = re.sub(r'<[^>]+>', '', s or '')
    for ent, ch in [('&nbsp;',' '),('&amp;','&'),('&lt;','<'),('&gt;','>'),('&quot;','"')]:
        s = s.replace(ent, ch)
    return re.sub(r'\s+', ' ', s).strip()

def parse_time(entry) -> tuple[datetime, bool]:
    """返回 (datetime_bj, has_time)。has_time=False 表示源没有时间字段。"""
    for f in ('published_parsed', 'updated_parsed'):
        t = getattr(entry, f, None)
        if t:
            try:
                dt = datetime(*t[:6], tzinfo=timezone.utc)
                return dt.astimezone(TZ_BJ), True
            except Exception:
                pass
    # 兜底：原始字符串解析失败时，返回 None 而不是当前时间，以便后续判断
    for f in ('published', 'updated'):
        raw = getattr(entry, f, None)
        if raw:
            try:
                import email.utils
                t = email.utils.parsedate_to_datetime(raw)
                return t.astimezone(TZ_BJ), True
            except Exception:
                pass
    return None, False

def fetch_html(url: str, timeout: int = 10) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as res:
            if res.status == 200:
                content = res.read()
                try: return content.decode('utf-8')
                except:
                    try: return content.decode('gbk')
                    except: return content.decode('utf-8', errors='ignore')
    except Exception:
        pass
    return ""

def extract_time_from_html(html: str) -> datetime:
    if not html: return None
    patterns = [
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"dateModified"\s*:\s*"([^"]+)"',
        r'<meta\s+(?:property|name)=["\'](?:article:published_time|pubdate|publishdate|og:updated_time|weibo:article:create_at)["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+(?:property|name)=["\'](?:article:published_time|pubdate|publishdate|og:updated_time|weibo:article:create_at)["\']',
        r'<time\s+[^>]*?datetime=["\']([^"\']+)["\']',
        r'<meta\s+name=["\'](publishdate|pubdate)["\']\s+content=["\']([^"\']+)["\']',
        r'pub[lL]ish[Tt]ime\s*(?:[=:>])\s*["\']([^"\']+)["\']'
    ]
    for p in patterns:
        m = re.search(p, html, re.IGNORECASE)
        if m:
            s = m.group(1).strip()
            dt = parse_datetime_guess(s)
            if dt:
                return dt
            
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.IGNORECASE|re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    
    # 针对联合早报等 Markdown 正文中的时间格式进行增强匹配
    body_patterns = [
        # 兼容: 2026年3月28日 10:09 PM / 10:09 AM
        r'(?:发布|更新|时间|发表于|日期)[\s/：:]*((?:20[12]\d)[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01])[日号]?\s+(?:[01]?\d|2[0-3])[:：][0-5]\d(?:[:：][0-5]\d)?(?:\s*[AP]M)?)',
        r'(?:发布|更新|时间|发表于|日期)[\s/：:]*((?:20[12]\d)[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01])[日号]?)',
        r'\b((?:20[12]\d)[-/年.](?:0?[1-9]|1[0-2])[-/月.](?:0?[1-9]|[12]\d|3[01])[日号]?\s+(?:[01]?\d|2[0-3])[:：][0-5]\d(?:[:：][0-5]\d)?)\b',
        r'(?:Published|Updated|Posted)\s*(?:on|:)?\s*([A-Za-z]{3,9}\.? \d{1,2}, \d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M)?)',
        r'(?:Published|Updated|Posted)\s*(?:on|:)?\s*((?:20[12]\d)[-/](?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])(?:\s+(?:[01]?\d|2[0-3])[:][0-5]\d(?:[:][0-5]\d)?)?)'
    ]
    for p in body_patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            date_str = m.group(1)
            dt = parse_datetime_guess(date_str)
            if dt:
                return dt
    return None

def get_image(entry) -> str:
    # 1. media_thumbnail
    for m in getattr(entry, 'media_thumbnail', []):
        if m.get('url'): return m['url']
        
    # 2. media_content (通用多媒体内容)
    for m in getattr(entry, 'media_content', []):
        if m.get('medium') == 'image' and m.get('url'):
            return m['url']
        if m.get('url') and not m.get('medium'):
            # 兜底：如果没标记 medium 但有 URL，尝试采纳
            return m['url']
            
    # 3. enclosures (附件)
    for e in getattr(entry, 'enclosures', []):
        if 'image' in e.get('type', ''): return e.get('href', '')
        
    # 4. summary / content (HTML 提取)
    for f in ('summary', 'content'):
        t = getattr(entry, f, '')
        if isinstance(t, list): t = t[0].get('value', '') if t else ''
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', t or '')
        if m: return m.group(1)
        
    return ''


# ── 联合早报网页直采（Playwright 渲染）──────────────────
# Zaobao 是纯 SPA（客户端渲染），curl 拿到的 HTML 里没有任何时间戳
# 必须用 Playwright 渲染页面才能提取真实时间

def _parse_relative_time(text: str, now: datetime) -> datetime:
    """将 '40分钟前', '2小时前', '1天前', '昨天 05:30' 等相对时间转为绝对时间"""
    text = text.strip()
    
    m = re.match(r'(\d+)\s*分钟前', text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    
    m = re.match(r'(\d+)\s*小时前', text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    
    m = re.match(r'(\d+)\s*天前', text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    
    m = re.match(r'昨天\s*(\d{1,2}):(\d{2})', text)
    if m:
        yesterday = now - timedelta(days=1)
        return yesterday.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    
    m = re.match(r'(\d{1,2}):(\d{2})', text)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    
    m = re.match(r'(\d+)\s*秒前', text)
    if m:
        return now - timedelta(seconds=int(m.group(1)))
    
    # 兜底：当天中午
    return now.replace(hour=12, minute=0, second=0, microsecond=0)


def scrape_zaobao_listing(page_url: str, timeout: int = 30, fetch_detail_time: bool = True) -> list[dict]:
    """抓取联合早报等网页列表，并可选抓取详情页来获取真实发布时间和图片。"""
    now = datetime.now(TZ_BJ)

    html = fetch_html(page_url, timeout=timeout)
    if not html:
        print(f'[WARN] 抓取 Zaobao 列表失败或为空: {page_url}', file=sys.stderr)
        return []

    results = []
    seen_urls = set()

    # 匹配核心文章链接模式：/news/.../storyYYYYMMDD-ID
    # 我们先找出所有符合特征的 <a> 标签块，再从中提取链接和可能作为标题的文本
    # 扩大匹配范围，捕获带有 storyID 的所有链接，并允许链接前后有更多属性
    link_block_pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']*?story\d{8}-\d+)[^"\']*["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL
    )

    import urllib.parse
    for match in link_block_pattern.finditer(html):
        raw_url = match.group(1).strip()
        # 提取 <a> 标签内部的所有内容，并去除 HTML 标签作为备选标题
        inner_content = match.group(2)
        title = strip_html(inner_content).strip()

        # 如果 <a> 标签内没有有效文字（可能只有图片），尝试通过 URL 里的 slug 或者是邻近元素（但这在正则里很难）
        # 这里我们做个妥协：如果标题太短或为空，我们从 HTML 中尝试找寻该链接对应的标题文本
        if not title or len(title) < 2:
            # 搜索链接附近的标题特征 (Zaobao 常用 <span class="video-title"> 或类似的)
            continue

        full_url = urllib.parse.urljoin('https://www.zaobao.com.sg', raw_url)

        # 仅针对故事链接进行处理
        if '/story' not in full_url:
            continue

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        # 尝试从 <a> 标签内提取图片
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', inner_content, re.IGNORECASE)
        image_url = img_match.group(1) if img_match else ''
        # 处理相对路径
        if image_url and not image_url.startswith('http'):
            image_url = urllib.parse.urljoin('https://www.zaobao.com.sg', image_url)

        results.append({
            'url': full_url,
            'title': title,
            'summary': '',
            'published': None,
            '_dt': None,
            'image': image_url,
            '_has_rss_time': False,
            '_fixed_html_time': False,
        })

    if fetch_detail_time and results:
        def enrich(item):
            html = fetch_html(item['url'], timeout=timeout)
            if html:
                dt = extract_time_from_html(html)
                if dt:
                    item['_dt'] = dt
                    item['published'] = dt.isoformat()
                    item['_has_rss_time'] = True
                    item['_fixed_html_time'] = True
                # 如果列表页没拿到图片，尝试从详情页提取
                if not item.get('image'):
                    img_patterns = [
                        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                        r'<img[^>]+class=["\'][^"\']*article[^"\']*["\'][^>]+src=["\']([^"\']+)["\']',
                        r'<figure[^>]*>.*?<img[^>]+src=["\']([^"\']+)["\']',
                    ]
                    for pattern in img_patterns:
                        img_match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                        if img_match:
                            item['image'] = img_match.group(1)
                            break
            return item

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(enrich, results))

    for item in results:
        if not item['_dt']:
            item['_dt'] = now
        if not item.get('published'):
            item['published'] = item['_dt'].isoformat()

    return results



def scrape_nhk_news(timeout: int = 30) -> list[dict]:
    """通过 NHK 官方 JSON API 获取中文新闻列表及详情。"""
    list_url = "https://api.nhkworld.jp/nwapi/rdnewsweb/v7b/zh/outline/list.json"
    detail_base_url = "https://api.nhkworld.jp/nwapi/rdnewsweb/v6b/zh/detail/{}.json"
    
    try:
        r = requests.get(list_url, timeout=timeout)
        if r.status_code != 200:
            return []
        data = r.json()
        items = data.get('data', [])
        if not items:
            return []
    except Exception as e:
        print(f"[WARN] NHK 列表获取失败: {e}", file=sys.stderr)
        return []

    results = []
    
    def fetch_detail(item):
        nid = item.get('id')
        if not nid: return None
        
        detail_url = detail_base_url.format(nid)
        try:
            dr = requests.get(detail_url, timeout=timeout)
            if dr.status_code == 200:
                ddata = dr.json().get('data', {})
                # NHK 时间戳是毫秒级的
                pub_ms = ddata.get('public_at')
                if pub_ms:
                    # NHK API 可能返回字符串形式的时间戳，显式转换为 float
                    # 经过核实，API 返回的是标准 UTC，直接转换为北京时间即为最准确值
                    dt = datetime.fromtimestamp(float(pub_ms) / 1000, tz=timezone.utc).astimezone(TZ_BJ)
                    
                    # 防御：即便不用补偿，也保留未来时间过滤的习惯
                    now_bj = datetime.now(TZ_BJ)
                    if dt > now_bj:
                        dt = now_bj
                else:
                    dt = datetime.now(TZ_BJ)
                
                # 构建相对 URL
                rel_url = item.get('page_url')
                if not rel_url:
                    rel_url = f"/nhkworld/zh/news/{nid}/"
                full_url = "https://www3.nhk.or.jp" + rel_url
                
                return {
                    'url': full_url,
                    'title': ddata.get('title') or item.get('title'),
                    'summary': ddata.get('description', '')[:200],
                    'content': ddata.get('detail', ''),  # 完整正文
                    'published': dt.isoformat(),
                    '_dt': dt,
                    'image': ddata.get('main_img') or item.get('image_url') or '',
                    '_has_rss_time': True,
                    '_fixed_html_time': True,
                }
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        enriched = list(executor.map(fetch_detail, items))
    
    # 过滤空结果
    results = [it for it in enriched if it is not None]
    return results


# ── 同源（media_group）内宽松去重 ────────────────────────
# 阈值 0.5：同源内标题高度相似才去重，宽松保留更多内容给模型判断

def dedup_within_group(items: list, sim: float = 0.5) -> tuple[list, list]:
    by_group = defaultdict(list)
    for item in items:
        by_group[item['_mg']].append(item)

    kept, removed_titles = [], []
    for group in by_group.values():
        group.sort(key=lambda x: x['_dt'])
        seen_titles = []
        for item in group:
            if any(bigram_sim(item['title'], t) >= sim for t in seen_titles):
                removed_titles.append(f"[{item['_mg']}] {item['title']}")
                continue
            seen_titles.append(item['title'])
            kept.append(item)
    return kept, removed_titles

# ── 状态加载 ─────────────────────────────────────────────

def load_state() -> dict:
    if not STATE.exists():
        return {'seen_urls': [], 'seen_titles': [], 'last_fetch_at': None}
    try:
        text = STATE.read_text('utf-8').strip()
        data = json.loads(text) if text else {}
        if 'seen_urls' not in data: data['seen_urls'] = []
        if 'seen_titles' not in data: data['seen_titles'] = []
        if 'last_fetch_at' not in data: data['last_fetch_at'] = None
        return data
    except Exception:
        return {'seen_urls': [], 'seen_titles': [], 'last_fetch_at': None}

# ── 主流程 ────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hours', type=float, default=None,
                    help='只保留最近 N 小时内的文章')
    ap.add_argument('--since', type=str, default=None,
                    help='从指定日期时间开始增量抓取 (ISO 格式)')
    ap.add_argument('--full', action='store_true', help='忽略历史状态，全量输出')
    args = ap.parse_args()

    cfg      = yaml.safe_load(CFG.read_text('utf-8'))
    settings = cfg.get('settings', {})
    TIMEOUT  = settings.get('timeout', 30)
    KEEP     = settings.get('keep_history', 2000)
    MAX      = settings.get('max_items', 1000)
    
    state       = load_state()
    seen_urls   = set(state.get('seen_urls', []))
    seen_titles = set(state.get('seen_titles', []))
    is_first    = not seen_urls

    now = datetime.now(TZ_BJ)
    last_fetch_at_str = state.get('last_fetch_at')
    
    if args.since:
        try:
            cutoff = datetime.fromisoformat(args.since)
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=TZ_BJ)
            print(f'[INFO] 使用指定起始时间: {cutoff.strftime("%Y-%m-%d %H:%M:%S")}', file=sys.stderr)
        except Exception:
            cutoff = now - timedelta(hours=48)
            print('[WARN] 指定起始时间解析失败，回退至48h', file=sys.stderr)
    elif args.hours is not None:
        cutoff = now - timedelta(hours=args.hours)
        print(f'[INFO] 使用指定时间窗口: {args.hours}h', file=sys.stderr)
    elif last_fetch_at_str:
        try:
            # 修复：不能严格使用 last_fetch_at 作为过滤基准
            # 因为像 rss.app 这样的第三方源常常有延迟，真实发布时间如果早于 last_fetch_at 就会被永久丢弃
            # 我们放宽到 72 小时，依赖 seen_urls 和 seen_titles 来做精确增量去重
            cutoff = now - timedelta(hours=72)
            print(f'[INCREMENTAL] 使用放宽的时间窗口 (近72h) 防止误杀延迟源', file=sys.stderr)
        except Exception:
            cutoff = now - timedelta(hours=72)
            print('[WARN] 上次拉取时间解析失败，回退至72h', file=sys.stderr)
    elif is_first and not args.full:
        cutoff = now - timedelta(hours=48)
        print('[INFO] 首次运行，默认保留近48h', file=sys.stderr)
    else:
        cutoff = None

    # print(f'[INFO] 时间窗口: {cutoff.isoformat() if cutoff else "增量模式"} → {now.isoformat()}',
    #       file=sys.stderr)

    # 1. 拉取，按是否有时间分两组
    timed_raw   = []  # 有时间字段，参与时间过滤
    no_time_raw = []  # 无时间字段，单独收集
    warn_feeds  = []
    
    global_missing_time_count = 0
    global_fixed_time_count = 0
    media_missing_time_dict = defaultdict(lambda: {'missing': 0, 'fixed': 0})

    feeds_to_process = cfg.get('feeds', [])
    total_feeds = len(feeds_to_process)

    for idx, fc in enumerate(feeds_to_process):
        platform = fc.get('platform', fc['url'])
        mg       = fc.get('media_group', platform).strip().lower()
        t        = fc.get('timeout', TIMEOUT)
        scrape_url = fc.get('scrape_url', '')  # 如有则走网页直采
        
        try:
            with progress_lock:
                print(f'[PROGRESS] ' + json.dumps({
                    'type': 'feed',
                    'feed_name': platform,
                    'feed_idx': idx + 1,
                    'feed_total': total_feeds,
                    'status': '连接节点与拉取数据中...'
                }, ensure_ascii=False), file=sys.stderr)
            
            # ── 网页直采分支（联合早报等 pubDate 假数据源）──
            if scrape_url:
                fetch_detail = fc.get('scrape_fetch_detail', True)
                scraped = scrape_zaobao_listing(scrape_url, timeout=t, fetch_detail_time=fetch_detail)
                if not scraped:
                    warn_feeds.append(f'{platform}(网页直采无结果)')
                    continue
                for a_idx, article in enumerate(scraped):
                    import time; time.sleep(0.015)

                    # 判断是否为新文章
                    article_is_new = False
                    article_uh = uhash(article['url'])
                    article_th = thash(article['title'])
                    if cutoff and article['_dt']:
                        if article['_dt'] >= cutoff:
                            if article_uh not in seen_urls and article_th not in seen_titles:
                                article_is_new = True

                    with progress_lock:
                        try:
                            print(f'[PROGRESS] ' + json.dumps({
                                'type': 'article',
                                'feed_name': platform,
                                'feed_idx': idx + 1,
                                'feed_total': total_feeds,
                                'article_idx': a_idx + 1,
                                'article_total': len(scraped),
                                'article_title': article['title'][:50] + ('...' if len(article['title']) > 50 else ''),
                                'article_url': article['url'],
                                'article_time': article['_dt'].strftime('%m-%d %H:%M') if article['_dt'] else '',
                                'image': article.get('image', ''),
                                'is_new': article_is_new
                            }, ensure_ascii=False), file=sys.stderr)
                        except: pass
                    item = {
                        'url':      article['url'],
                        'uh':       uhash(article['url']),
                        'th':       thash(article['title']),
                        'title':    article['title'],
                        'platform': platform,
                        'country':  fc.get('country', '').strip(),
                        '_mg':      mg,
                        '_dt':      article['_dt'],
                        '_has_rss_time': article.get('_has_rss_time', True),
                        '_fixed_html_time': article.get('_fixed_html_time', False),
                        'published': article['published'],
                        'summary':  article.get('summary', '')[:200],
                        'image':    article.get('image', ''),
                        '_fetch_jina': fc.get('fetch_jina', False),
                    }
                    timed_raw.append(item)
                continue  # 跳过 RSS 分支
            
            # ── NHK 官方 API 分支 ──
            if 'nhk.or.jp' in fc['url'] or fc.get('media_group', '').lower() == 'nhk':
                scraped = scrape_nhk_news(timeout=t)
                if not scraped:
                    warn_feeds.append(f'{platform}(API 无结果)')
                    continue
                for a_idx, article in enumerate(scraped):
                    import time; time.sleep(0.015)

                    # 判断是否为新文章
                    article_is_new = False
                    article_uh = uhash(article['url'])
                    article_th = thash(article['title'])
                    if cutoff and article['_dt']:
                        if article['_dt'] >= cutoff:
                            if article_uh not in seen_urls and article_th not in seen_titles:
                                article_is_new = True

                    with progress_lock:
                        try:
                            print(f'[PROGRESS] ' + json.dumps({
                                'type': 'article',
                                'feed_name': platform,
                                'feed_idx': idx + 1,
                                'feed_total': total_feeds,
                                'article_idx': a_idx + 1,
                                'article_total': len(scraped),
                                'article_title': article['title'][:50] + ('...' if len(article['title']) > 50 else ''),
                                'article_time': article['_dt'].strftime('%m-%d %H:%M') if article['_dt'] else '',
                                'image': article.get('image', ''),
                                'is_new': article_is_new
                            }, ensure_ascii=False), file=sys.stderr)
                        except: pass

                    item = {
                        'url':      article['url'],
                        'uh':       article_uh,
                        'th':       article_th,
                        'title':    article['title'],
                        'platform': platform,
                        'country':  fc.get('country', '').strip(),
                        '_mg':      mg,
                        '_dt':      article['_dt'],
                        '_has_rss_time': True,
                        '_fixed_html_time': True,
                        'published': article['published'],
                        'summary':  article['summary'],
                        'content':  article.get('content', ''),
                        'image':    article['image'],
                        '_fetch_jina': False, # NHK 已经拿到了详情，不需要再走 Jina
                    }
                    timed_raw.append(item)
                continue
            
            # ── 正常 RSS 分支 ──
            content = curl_fetch(fc['url'], timeout=t)
            feed    = feedparser.parse(content)
            if not feed.entries:
                warn_feeds.append(f'{platform}(无条目)')
                continue
            
            timed_count = no_time_count = 0
            
            total_entries = len(feed.entries)
            processed_count = [0]
            
            def process_entry(e):
                url = getattr(e, 'link', '') or ''
                title = strip_html(getattr(e, 'title', '') or '')
                if not url or not title:
                    return None
                
                # 1. RSS 时间
                rss_dt, has_time = parse_time(e)
                
                # 2. 如果 RSS 原文没有时间，才去目标链接抓取进行补救
                final_dt = rss_dt
                fixed_html = False
                if not has_time:
                    html = fetch_html(url)
                    html_dt = extract_time_from_html(html)
                    if html_dt:
                        final_dt = html_dt
                        fixed_html = True
                
                if not final_dt:
                    final_dt = now
                    
                # 3. 防御“未来时间”（修复第三方 RSS 时区双重叠加 Bug）
                # 很多第三方 RSS 生成器（如 rss.app）会把原站本就是北京时间的字面值（如 10:09），错误地打上 UTC/GMT 标签。
                # 导致我们在转换本地时间时，又被盲目加上了 8 小时，变成了 18:09 的“未来新闻”。
                if final_dt > now:
                    if final_dt > now + timedelta(hours=1):
                        # 如果超前远大于 1 小时，几乎确定是时区叠加错误，直接扣除 8 小时
                        final_dt -= timedelta(hours=8)
                        # 如果扣除后依然在未来（说明还有其他错位），则直接平摊为当前时间
                        if final_dt > now:
                            final_dt = now
                    else:
                        # 对于服务器时区误差造成的轻微超前（<1小时），自动修正为当前时间
                        final_dt = now
                
                is_new = False
                if final_dt and (not cutoff or final_dt >= cutoff):
                    if uhash(url) not in seen_urls and thash(title) not in seen_titles:
                        is_new = True

                with progress_lock:
                    processed_count[0] += 1
                    try:
                        import time; time.sleep(0.02)
                        print(f'[PROGRESS] ' + json.dumps({
                            'type': 'article',
                            'feed_name': platform,
                            'feed_idx': idx + 1,
                            'feed_total': total_feeds,
                            'article_idx': processed_count[0],
                            'article_total': total_entries,
                            'article_title': title[:50] + ('...' if len(title) > 50 else ''),
                            'article_url': url,
                            'article_time': final_dt.strftime('%m-%d %H:%M') if final_dt else '',
                            'image': get_image(e),
                            'is_new': is_new
                        }, ensure_ascii=False), file=sys.stderr)
                    except:
                        pass
                
                item = {
                    'url':      url,
                    'uh':       uhash(url),
                    'th':       thash(title),
                    'title':    title,
                    'platform': platform,
                    'country':  fc.get('country', '').strip(),
                    '_mg':      mg,
                    '_dt':      final_dt,
                    '_has_rss_time': has_time,
                    '_fixed_html_time': fixed_html,
                    'published': final_dt.isoformat(),
                    'summary':  strip_html(getattr(e, 'summary', '') or '')[:200],
                    'image':    get_image(e),
                    '_fetch_jina': fc.get('fetch_jina', False),
                }
                return item

            # 高并发处理每条目
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                processed_items = list(executor.map(process_entry, feed.entries))
                
            local_missing = 0
            local_fixed = 0
            for item in processed_items:
                if not item: continue
                
                if not item['_has_rss_time']:
                    global_missing_time_count += 1
                    local_missing += 1
                    media_missing_time_dict[platform]['missing'] += 1
                    if item['_fixed_html_time']:
                        global_fixed_time_count += 1
                        local_fixed += 1
                        media_missing_time_dict[platform]['fixed'] += 1
                
                # 现在所有条目都在 final_dt 下统一为了有时间，因此统统加入有时间队列
                timed_count += 1
                timed_raw.append(item)
                
            # msg = f'[OK] {platform} 提取记录:{timed_count}'
            # if local_missing > 0:
            #     msg += f' (无时间:{local_missing}, 原文修复:{local_fixed})'
            # print(msg, file=sys.stderr)
        except Exception as ex:
            warn_feeds.append(f'{platform}({ex})')

    # if warn_feeds:
    #     print(f'[WARN] 失败源: {", ".join(warn_feeds)}', file=sys.stderr)

    # 2. 有时间的条目：时间过滤 → 同源去重 → 增量过滤
    if cutoff:
        timed_to_process = [i for i in timed_raw if i['_dt'] >= cutoff]
    else:
        timed_to_process = timed_raw

    timed_deduped, dup_intra_titles = dedup_within_group(timed_to_process)

    # URL 精确去重（全局）
    seen_uh = set()
    timed_final = []
    global_dup_titles = []
    incremental_filtered = 0
    for item in timed_deduped:
        if item['uh'] not in seen_uh:
            seen_uh.add(item['uh'])
            timed_final.append(item)
        else:
            global_dup_titles.append(f"[{item['platform']}] {item['title']}")

    if not args.full:
        before_inc = len(timed_final)
        timed_final = [i for i in timed_final
                       if i['uh'] not in seen_urls and i['th'] not in seen_titles]
        incremental_filtered = before_inc - len(timed_final)

    timed_final.sort(key=lambda x: x['_dt'], reverse=True)
    timed_final = timed_final[:MAX]

    # print(f'[结果统计] 原始{len(timed_raw)} → 过滤时间-{len(timed_raw)-len(timed_to_process)} → 同源合并-{len(dup_intra_titles)} → 全局合并-{len(global_dup_titles)} → 历史增量剔除-{incremental_filtered} → 最终保留{len(timed_final)}',
    #       file=sys.stderr)

    # 3. 无时间的条目：只做同源去重 + 增量过滤，不做时间过滤
    no_time_deduped, _ = dedup_within_group(no_time_raw)
    seen_uh2 = set()
    no_time_final = []
    for item in no_time_deduped:
        if item['uh'] not in seen_uh2 and item['uh'] not in seen_uh:
            seen_uh2.add(item['uh'])
            no_time_final.append(item)

    if not args.full:
        no_time_final = [i for i in no_time_final
                         if i['uh'] not in seen_urls and i['th'] not in seen_titles]

    # print(f'[无时间] 原始{len(no_time_raw)} → 去重后{len(no_time_final)}（不参与时间过滤，时间不可信）',
    #       file=sys.stderr)

    # 4. Jina AI Reader 获取正文与矫正时间
    all_new = timed_final + no_time_final
    
    jina_tasks = [item for item in all_new if item.get('_fetch_jina', False)]
    if jina_tasks:
        with progress_lock:
            print(f'[PROGRESS] ' + json.dumps({
                'type': 'jina_start',
                'total': len(jina_tasks)
            }, ensure_ascii=False), file=sys.stderr)
        
        jina_processed = [0]
        def fetch_jina(item):
            with progress_lock:
                jina_processed[0] += 1
                try:
                    title_str = item.get('title', '')
                    print(f'[PROGRESS] ' + json.dumps({
                        'type': 'jina_article',
                        'jina_idx': jina_processed[0],
                        'jina_total': len(jina_tasks),
                        'article_title': title_str[:50] + ('...' if len(title_str) > 50 else '')
                    }, ensure_ascii=False), file=sys.stderr)
                except:
                    pass
            jina_url = f"https://r.jina.ai/{item['url']}"
            try:
                # 使用 requests 请求 Jina API，如 JSON 失败则回退到 Markdown 文本
                resp = requests.get(
                    jina_url,
                    headers={'Accept': 'application/json'},
                    timeout=30
                )
                if resp.status_code == 200:
                    content = ''
                    jina_time = None
                    ct = resp.headers.get('Content-Type', '')
                    if 'application/json' in ct:
                        try:
                            payload = resp.json()
                        except ValueError:
                            payload = {}
                        data = payload.get('data', payload if isinstance(payload, dict) else {})
                        content = data.get('content') or data.get('text') or ''
                        jina_time = data.get('publishedTime') or data.get('published_time')
                    else:
                        content = resp.text or ''
                        for line in content.splitlines()[:10]:
                            if ':' in line and ('Published' in line or '发布时间' in line):
                                candidate = line.split(':', 1)[1].strip()
                                if candidate:
                                    jina_time = candidate
                                    break

                    if content:
                        # 应用内容清洗，去除导航、广告等噪音
                        cleaned_content = clean_jina_content(content)
                        item['content'] = cleaned_content

                    if jina_time:
                        parsed = parse_datetime_guess(jina_time)
                        if parsed:
                            item['_dt'] = parsed
                            item['published'] = parsed.isoformat()

                    # ── 兜底改进：如果 metadata 没给时间，但正文里有时间文字（Zaobao 常见） ──
                    if not jina_time and content:
                        text_dt = extract_time_from_html(content)
                        if text_dt:
                            item['_dt'] = text_dt
                            item['published'] = text_dt.isoformat()
            except Exception:
                pass
            return item

        # 高并发处理 Jina 请求
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(fetch_jina, jina_tasks))

    # 4.5 [AI Outline Extraction] 全量大纲提取（替代翻译）
    all_new = timed_final + no_time_final
    if all_new:
        llm_cfg = load_llm_config()
        trans_cfg = get_translation_config(llm_cfg)

        # ── 保存原文 ──
        # 在翻译前保存原始标题和内容
        for item in all_new:
            item['original_title'] = item.get('title', '')
            item['original_content'] = item.get('content', '')

        if trans_cfg and trans_cfg.get('llm', {}).get('enabled'):
            # 1. 批量翻译标题（保留，用于显示）
            titles_to_check = [a['title'] for a in all_new]
            title_map = batch_translate_to_chinese(titles_to_check, trans_cfg)

            for item in all_new:
                item['title'] = title_map.get(item['title'], item['title'])

            # 2. 批量提取大纲（替代正文翻译）
            with progress_lock:
                print(f'[PROGRESS] ' + json.dumps({
                    'type': 'outline_start',
                    'total': len(all_new)
                }, ensure_ascii=False), file=sys.stderr)

            outline_processed = [0]
            def extract_outline_task(item):
                with progress_lock:
                    outline_processed[0] += 1
                    try:
                        print(f'[PROGRESS] ' + json.dumps({
                            'type': 'outline_article',
                            'outline_idx': outline_processed[0],
                            'outline_total': len(all_new),
                            'article_title': item['title'][:50]
                        }, ensure_ascii=False), file=sys.stderr)
                    except: pass

                # 根据是否有完整正文，选择不同策略
                content = item.get('content', '')
                title = item.get('title', '')
                summary = item.get('summary', '')

                if content and len(content) > 200:
                    # 有完整正文（Jina 抓取）
                    outline = extract_chinese_outline(content, trans_cfg, 'full_content')
                    if outline:
                        item['content'] = outline
                    else:
                        # 降级：使用 title + summary
                        combined = f"标题：{title}\n摘要：{summary}"
                        outline = extract_chinese_outline(combined, trans_cfg, 'title_summary')
                        item['content'] = outline or f"{title}\n{summary}"
                else:
                    # 无完整正文，使用 title + summary
                    combined = f"标题：{title}\n摘要：{summary}"
                    outline = extract_chinese_outline(combined, trans_cfg, 'title_summary')
                    item['content'] = outline or combined

            # 并行执行大纲提取（限制并发数，本地 LLM 资源有限）
            outline_cfg = trans_cfg.get('llm', {}).get('outline_extraction', {})
            max_workers = outline_cfg.get('max_workers', 2)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                list(executor.map(extract_outline_task, all_new))

            print(f'[INFO] 大纲提取完成: {len(all_new)} 篇', file=sys.stderr)

    # 4.5 SQLite 入库
    db_inserted = 0
    db_failed_items = []  # 记录入库失败的文章，供状态更新使用
    try:
        from store import init_db, get_conn, upsert_articles
        conn = get_conn()
        init_db(conn)
        db_items = []
        for item in all_new:
            db_items.append({
                'url_hash':   item['uh'],
                'title_hash': item['th'],
                'url':        item['url'],
                'title':      item['title'],
                'original_title': item.get('original_title', ''),
                'original_content': item.get('original_content', ''),
                'platform':   item['platform'],
                'media_group': item['_mg'],
                'country':    item.get('country', ''),
                'published':  item['published'],
                'summary':    item.get('summary', ''),
                'content':    item.get('content', ''),
                'image':      item.get('image', ''),
                'video':      item.get('video', ''),
                'llm_tags':   item.get('llm_tags', '[]'),
            })
        db_inserted = upsert_articles(db_items, conn=conn)
        conn.close()
        print(f'[INFO] SQLite 入库成功: {db_inserted} 条新记录 (共处理 {len(db_items)} 条)', file=sys.stderr)
    except Exception as e:
        db_inserted = -1
        print(f'[ERROR] SQLite 入库失败: {e}', file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)

    # 5. 更新状态 — 必须在入库成功后才写 state.json
    # 如果在入库之前就写入 state.json，一旦入库失败，这些文章将被永久丢失：
    # 它们既不在数据库里，也已被标记为「已见过」不会被再次拉取
    if db_inserted >= 0:  # 只有入库未发生异常才更新 seen 状态
        def update_seen(old, new_set, maxlen):
            res = list(old)
            old_set = set(old)
            for h in new_set:
                if h not in old_set:
                    res.append(h)
            return res[-maxlen:]

        STATE.write_text(json.dumps({
            'seen_urls':     update_seen(state.get('seen_urls', []),   {i['uh'] for i in all_new}, KEEP),
            'seen_titles':   update_seen(state.get('seen_titles', []), {i['th'] for i in all_new}, KEEP),
            'last_fetch_at': now.isoformat(),
        }, ensure_ascii=False), 'utf-8')
        print(f'[INFO] state.json 已更新，seen_urls={len(state.get("seen_urls", []))+len(all_new)} 条', file=sys.stderr)
    else:
        print(f'[WARN] 由于入库异常，state.json 未更新，下次抓取将重试这些文章', file=sys.stderr)


    # 4.6 LLM 语义打标（异步，不阻断入库）
    try:
        llm_cfg_path = BASE / "config" / "llm_topics.yaml"
        if llm_cfg_path.exists():
            llm_cfg = yaml.safe_load(llm_cfg_path.read_text('utf-8')) or {}
            if llm_cfg.get('llm', {}).get('enabled'):
                from llm_tagger import batch_tag_articles
                tag_conn = get_conn()
                init_db(tag_conn)
                batch_tag_articles(all_new, llm_cfg, tag_conn)
                tag_conn.close()
    except Exception as e:
        print(f'[WARN] LLM 打标失败(已降级): {e}', file=sys.stderr)

    # 5. 输出
    INTERNAL = {'uh', 'th', '_mg', '_dt', '_has_rss_time', '_fixed_html_time'}
    def clean(items):
        return [{k: v for k, v in i.items() if k not in INTERNAL and v not in ('', [])}
                for i in items]
                
    stats = {
        'total_feeds_configured': len(cfg.get('feeds', [])),
        'total_raw_count': len(timed_raw),
        'missing_time_count': global_missing_time_count,
        'fixed_time_count': global_fixed_time_count,
        'media_missing_details': dict(media_missing_time_dict),
        'time_filtered_out_count': len(timed_raw) - len(timed_to_process),
        'merged_intra_source_count': len(dup_intra_titles),
        'merged_global_count': len(global_dup_titles),
        'historical_filtered_count': incremental_filtered,
        'final_count': len(timed_final),
        'db_inserted': db_inserted,  # 新增：实际入库条数
        'dup_intra_titles': dup_intra_titles,
        'dup_global_titles': global_dup_titles
    }

    out = {
        'stats': stats,
        'raw': {
            'generated_at': now.isoformat(),
            'time_window':  {'from': cutoff.isoformat() if cutoff else None, 'to': now.isoformat()},
            'count': len(timed_raw) + len(no_time_raw),
            'items': clean(timed_raw),
            'no_time_items': clean(no_time_raw)
        },
        'time_filtered': {
            'generated_at': now.isoformat(),
            'time_window':  {'from': cutoff.isoformat() if cutoff else None, 'to': now.isoformat()},
            'count': len(timed_to_process) + len(no_time_raw),
            'items': clean(timed_to_process),
            'no_time_items': clean(no_time_raw)
        },
        'final': {
            'generated_at': now.isoformat(),
            'time_window':  {'from': cutoff.isoformat() if cutoff else None, 'to': now.isoformat()},
            'timed_count': len(timed_final),
            'no_time_count': len(no_time_final),
            'items': clean(timed_final),
            'no_time_items': clean(no_time_final)
        }
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False, indent=2).encode('utf-8'))
    print()

if __name__ == '__main__':
    main()
