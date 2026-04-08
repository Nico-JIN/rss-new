import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

# 中国媒体黑名单 - 域名
CHINESE_DOMAINS = {
    '.cn', '.com.cn', '.net.cn', '.org.cn', '.gov.cn',
    'xinhuanet.com', 'people.com.cn', 'chinadaily.com.cn', 'globaltimes.cn',
    'cgtn.com', 'cri.cn', 'bjreview.com', 'scmp.com',  # SCMP 虽然在香港，但常被归类为中文官方口径，可根据需要保留或移除
    'thepaper.cn', 'caixin.com', 'ifeng.com', 'sina.com.cn', 'sohu.com',
    '163.com', 'qq.com', 'zhihu.com', 'bilibili.com', 'weibo.com'
}

# 中国媒体黑名单 - 关键词 (标题或来源中包含)
CHINESE_KEYWORDS = {
    '新华', '人民网', '环球', '中新', '澎湃', '央视', 'CGTN', 
    'China Daily', 'Global Times', 'CCTV', '今日头条', '网易', 
    '搜狐', '腾讯', '新浪', '百度', '观察者网', '界面新闻'
}

# 高价值国际媒体 - 白名单 (用于评分参考)
TIER1_INTERNATIONAL_MEDIA = {
    'Reuters', 'Associated Press', 'AP', 'Bloomberg', 'BBC', 'CNN', 
    'The New York Times', 'NYT', 'The Guardian', 'The Washington Post', 
    'WSJ', 'Wall Street Journal', 'Financial Times', 'FT', 'Al Jazeera', 
    'Nikkei', 'NHK', 'France 24', 'Deutsche Welle', 'DW'
}

def is_chinese_media(source_name: str, url: str) -> bool:
    """
    检查是否是中国媒体条目。
    
    Args:
        source_name: 来源平台名称
        url: 文章链接
        
    Returns:
        True 如果来源被判定为中国媒体，否则 False。
    """
    if not source_name and not url:
        return False
        
    # 1. 检查域名后缀
    try:
        domain = urlparse(url).netloc.lower()
        if any(domain.endswith(ext) for ext in CHINESE_DOMAINS):
            return True
        # 精确匹配域名片段
        for b_domain in CHINESE_DOMAINS:
            if b_domain in domain:
                return True
    except Exception:
        pass
        
    # 2. 检查来源名称中的关键词
    source_name = (source_name or "").lower()
    for kw in CHINESE_KEYWORDS:
        if kw.lower() in source_name:
            return True
            
    return False

def is_high_value(item: Dict) -> bool:
    """
    判断条目是否具有高价值。
    
    规则：
    1. 属于 Tier-1 国际媒体
    2. 如果有 social engagement 数据，高于某个阈值
    3. 内容长度或摘要信息丰富
    """
    source = (item.get('platform', '') or item.get('source', '')).lower()
    
    # Tier-1 媒体直接判定为高价值
    for t1 in TIER1_INTERNATIONAL_MEDIA:
        if t1.lower() in source:
            return True
            
    # 如果有元数据中的评分 (来自搜素引擎)
    raw_meta = item.get('raw_metadata', {})
    if isinstance(raw_meta, dict):
        score = raw_meta.get('score', 0)
        if score > 0.5: # 假设 0-1 评分
            return True
            
    # 如果摘要很长且非空
    summary = item.get('summary', '')
    if len(summary) > 200:
        return True
        
    return False

def filter_chinese_results(items: List[Dict]) -> List[Dict]:
    """过滤掉列表中的中国媒体条目"""
    return [item for item in items if not is_chinese_media(
        item.get('platform', item.get('source', '')), 
        item.get('url', '')
    )]
