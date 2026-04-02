#!/usr/bin/env python3
import yaml
from pathlib import Path

BASE = Path(__file__).parent.parent
CFG_PATH = BASE / "config" / "feeds.yaml"

# 映射规则：关键字 -> 国家
RULES = {
    '新加坡': ['zaobao', '联合早报', 'singapore'],
    '美国': ['pbs', 'cnn', 'washingtonpost', '华盛顿邮报', '纽约时报', 'nytimes', 'bloomberg', '彭博', 'us', 'usa', 'america', '众议院'],
    '英国': ['reuters', '路透社', 'bbc', 'guardian', '卫报', 'ft', '金融时报', 'uk'],
    '日本': ['nhk', '共同社', 'kyodo', 'nikkei', '日经', 'japan', '共同网'],
    '韩国': ['韩联社', 'yonhap', 'korea'],
    '俄罗斯': ['sputnik', '俄罗斯卫星', 'russia'],
    '中国': ['cctv', '新华', 'xinhua', 'china'],
    '中国香港': ['scmp', '南华早报', 'hong kong', 'hk', '端传媒'],
    '中国台湾': ['中央社', 'cna', 'taiwan', 'tw'],
    '德国': ['dw', '德声', 'germany'],
    '法国': ['rfi', '法广', 'france'],
}

def classify():
    if not CFG_PATH.exists():
        print("Config file not found.")
        return

    with open(CFG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    if not cfg or 'feeds' not in cfg:
        print("Invalid config structure.")
        return

    modified = False
    for feed in cfg['feeds']:
        platform = feed.get('platform', '').lower()
        url = feed.get('url', '').lower()
        media = feed.get('media_group', '').lower()
        current_country = feed.get('country', '').strip()

        # 如果没有国家，或者需要规范化（比如 "HK" -> "中国香港"）
        target_country = current_country
        
        # 简化版自动识别
        found = False
        for country, keywords in RULES.items():
            for kw in keywords:
                if kw in platform or kw in url or kw in media:
                    target_country = country
                    found = True
                    break
            if found: break
        
        if target_country != current_country:
            print(f"Classification: {feed.get('platform')} -> {target_country}")
            feed['country'] = target_country
            modified = True

    if modified:
        with open(CFG_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print("feeds.yaml updated successfully.")
    else:
        print("No changes needed for feeds.yaml.")

if __name__ == '__main__':
    classify()
