#!/usr/bin/env python3
"""叙事提取模块 — 从文章聚类中提取核心叙事

职责：
  1. 接收预聚类的文章簇
  2. 调用 LLM 提炼出核心叙事（标题 + 实体 + 摘要 + 分类）
  3. 将叙事映射到系统分类（china_related / us_news / japan_news 等）

设计原则：
  - 先用本地聚类做粗筛（毫秒级），再用 LLM 做语义提炼
  - 一次 LLM 调用处理所有显著聚类，最大化效率
  - 输出兼容现有 event 格式，前端无需改动
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter

# 确保能导入同级模块
sys.path.insert(0, str(Path(__file__).parent))

from llm_tagger import _call_llm_api, load_llm_config

TZ_BJ = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════════════════
# 分类区域映射 — 用于将叙事自动归类
# ═══════════════════════════════════════════════════════════════════

CATEGORY_ENTITY_MAP = {
    'china_related': {
        'entities': [
            # 国家和城市（排除港澳台）
            '中国', '北京', '上海', '广东', '深圳', '广州', '天津', '重庆', '武汉', '南京',
            '杭州', '苏州', '成都', '西安', '新疆', '西藏', '内蒙古', '辽宁', '山东',
            '大陆', '中国大陆', '内地',
            # 核心领导人（最新）
            '习近平', '李强', '王毅', '赵乐际', '丁薛祥', '何立峰', '张国清', '刘国中',
            '秦刚', '华春莹', '毛宁', '林剑', '郑栅洁', '马兴瑞', '王小洪', '吴谦',
            '蔡奇', '李鸿忠', '陈文清', '唐登杰', '许其亮', '张又侠',
            # 重要机构
            '外交部', '国务院', '中共中央', '中共', '解放军', '国防部', '国安部',
            '人大常委会', '政协', '中纪委', '发改委', '商务部', '工信部', '公安部',
            '中方', '中国政府', '中国官方', '中国外交部',
            # 经济科技企业
            '华为', '中兴', '比亚迪', '宁德时代', '大疆', '阿里巴巴', '腾讯', '百度',
            'TikTok', '抖音', '小红书', 'DeepSeek', '深度求索', '小红书',
            # 国际关系关键词
            '中美', '中欧', '中俄', '中印', '中朝', '中非', '中美洲',
            '一带一路', '对华', '制裁中国', '中国制裁', '贸易战', '关税',
            '南海', '钓鱼岛', '黄岩岛', '台海', '两岸', '港台', '收复台湾', '统一台湾',
            '中国梦', '两个一百年', '新发展格局', '双循环',
            # 英文关键词
            'China', 'Beijing', 'Xi Jinping', 'Huawei', 'BYD', 'CATL', 'DJI',
            'Chinese', 'PRC', 'CCP', 'PLA',
        ],
        'description': '涉及中国大陆的国际报道（不含港澳台）',
    },
    'us_news': {
        'entities': [
            # 国家和城市
            '美国', '华盛顿', '纽约', '洛杉矶', '芝加哥', '旧金山', '硅谷', '西雅图',
            '波士顿', '休斯顿', '达拉斯', '迈阿密', '亚特兰大', '凤凰城', '丹佛',
            'USA', 'US', 'America', 'American',
            # 核心领导人（最新 - 特朗普第二任期）
            '特朗普', '川普', 'Trump', '范斯', '万斯', 'Vance', '马斯克', 'Musk',
            '拜登', 'Biden', '哈里斯', '贺锦丽', 'Harris', '奥巴马', 'Obama',
            '彭斯', 'Pence', '佩洛西', 'Pelosi', '麦康奈尔', 'McConnell',
            '卢比奥', 'Rubio', '赫格塞斯', 'Hegseth', '沃尔茨', 'Waltz',
            '贝森特', 'Bessent', '莱特希泽', 'Lighthizer', '纳瓦罗', 'Navarro',
            '蓬佩奥', 'Pompeo', '布林肯', 'Blinken', '沙利文', 'Sullivan',
            '耶伦', 'Yellen', '鲍威尔', 'Powell', '雷蒙多', 'Raimondo',
            # 重要机构
            '白宫', 'White House', '五角大楼', 'Pentagon', '美国国防部', '国防部',
            '国会', 'Congress', '参议院', 'Senate', '众议院', 'House',
            '美联储', 'Fed', 'Federal Reserve', 'CIA', 'FBI', 'NSA',
            '国务院', 'State Department', '国家安全委员会', 'NSC',
            '美国海军', '美国空军', '美国陆军', '美军', 'US military',
            '最高法院', 'Supreme Court', '司法部', 'DOJ', '商务部',
            # 美国相关事件和议题
            '美国大选', '美国总统', '总统选举', '中期选举', '国会选举',
            '美中关系', '中美关系', '美俄关系', '美欧关系', '美日关系',
            '美国经济', '美元', '通胀', '利率', '加息', '降息',
            '边境', '移民', '关税', '贸易战', '芯片法案', '通胀削减法案',
            '堕胎', '枪支', '种族', '医保', '社保',
            # 美国科技公司
            '苹果', 'Apple', '微软', 'Microsoft', '谷歌', 'Google', '亚马逊', 'Amazon',
            'Meta', 'Facebook', 'OpenAI', 'ChatGPT', '英伟达', 'NVIDIA', 'AMD', 'Intel',
        ],
        'description': '美国内政、外交、军事、经济新闻',
    },
    'japan_news': {
        'entities': [
            # 国家和城市
            '日本', '东京', 'Tokyo', '大阪', 'Osaka', '京都', 'Kyoto', '横滨', '名古屋',
            '冲绳', 'Okinawa', '北海道', 'Hokkaido', '福冈', '广岛', 'Hiroshima', '长崎',
            '福岛', 'Fukushima', '那霸', 'Japan', 'Japanese', 'JP',
            # 核心领导人（最新 - 石破茂2024年10月上任）
            '石破茂', 'Ishiba', 'Ishiba Shigeru', '岸田', '岸田文雄', 'Kishida',
            '安倍', '安倍晋三', 'Abe', '菅义伟', 'Suga', '野田', '野田佳彦',
            '麻生太郎', 'Aso', '茂木敏充', 'Motegi', '林芳正', 'Hayashi',
            '河野太郎', 'Kono', '小泉进次郎', 'Koizumi', '高市早苗', 'Takaichi',
            '日本首相', 'Prime Minister of Japan', '内阁总理大臣',
            # 重要机构
            '自民党', 'LDP', '民主党', '民主党', '立宪民主党', '公明党',
            '日本国会', '国会', '众议院', '参议院', '众院', '参院',
            '外务省', '防卫省', '财务省', '经济产业省', '文部科学省',
            '日银', '日本央行', 'Bank of Japan', 'BOJ', '央行',
            '自卫队', '日本自卫队', 'JSDF', '海上自卫队', '陆上自卫队', '航空自卫队',
            # 日本相关事件和议题
            '日元', 'Yen', 'JPY', '日圆', '日经', '日经指数', 'Nikkei',
            '核污水', '核废水', '核处理水', '排海', '排放', '福岛核电站',
            '日中关系', '中日关系', '日美关系', '美日关系', '日韩关系', '日俄关系',
            '日台关系', '台湾与日本', '钓鱼岛', '尖阁诸岛', 'Senkaku',
            '冲绳美军', '美军基地', '驻日美军', '普天间', '嘉手纳',
            '日本经济', '日本GDP', '日本通胀', '日本利率',
            '日本签证', '日本旅游', '日本签证政策',
        ],
        'description': '日本政治、经济、军事、外交新闻',
    },
    'middle_east': {
        'entities': [
            # 地区和国家
            '中东', 'Middle East', '伊朗', 'Iran', 'Iranian', '以色列', 'Israel', 'Israeli',
            '巴勒斯坦', 'Palestine', 'Palestinian', '沙特', 'Saudi', 'Saudi Arabia',
            '加沙', 'Gaza', '叙利亚', 'Syria', 'Syrian', '黎巴嫩', 'Lebanon', 'Lebanese',
            '也门', 'Yemen', 'Yemeni', '约旦', 'Jordan', '伊拉克', 'Iraq', 'Iraqi',
            '阿联酋', 'UAE', '迪拜', 'Dubai', '卡塔尔', 'Qatar', '巴林', 'Bahrain',
            '科威特', 'Kuwait', '阿曼', 'Oman', '埃及', 'Egypt', 'Egyptian',
            '土耳其', 'Turkey', 'Turkish', '土耳其', '德黑兰', 'Tehran',
            '耶路撒冷', 'Jerusalem', '拉马拉', 'Ramallah',
            # 核心领导人
            '内塔尼亚胡', 'Netanyahu', '哈梅内伊', 'Khamenei', '莱希', 'Raisi',
            '佩泽什基安', 'Pezeshkian', '穆罕默德', 'Mohammad', '阿卜杜拉',
            '阿萨德', 'Assad', '埃尔多安', 'Erdogan', '穆巴拉克', 'Mubarak',
            '纳斯鲁拉', 'Nasrallah', '哈尼亚', 'Haniyeh', '辛瓦尔', 'Sinwar',
            # 组织和武装
            '哈马斯', 'Hamas', '真主党', 'Hezbollah', '胡塞', 'Houthi', 'Houthis',
            'ISIS', '伊斯兰国', '塔利班', 'Taliban', 'Al Qaeda', '基地组织',
            '阿克萨烈士旅', 'PIJ', '杰哈德', '巴勒斯坦圣战组织',
            # 关键地点
            '霍尔木兹', 'Hormuz', '红海', 'Red Sea', '苏伊士', 'Suez',
            '戈兰高地', 'Golan Heights', '西岸', 'West Bank', '加沙地带',
            # 相关事件
            '巴以冲突', '以巴冲突', '以哈战争', '加沙战争', '加沙冲突',
            '中东冲突', '中东战争', '石油', '原油', 'OPEC', '石油价格',
            '伊朗核', '核协议', 'JCPOA', '制裁伊朗', '伊朗制裁',
            '红海危机', '航运袭击', '商船袭击', '胡塞袭击',
            '贝鲁特', 'Beirut', '大马士革', 'Damascus',
        ],
        'description': '中东地区冲突、石油、外交新闻',
    },
    'hk_tw_macau': {
        'entities': [
            # 地区（明确区分）
            '香港', 'Hong Kong', 'HK', '台湾', 'Taiwan', 'TW', '澳门', 'Macau', 'Macao',
            '港台', '港澳', '台港澳', '两岸三地', '港澳台',
            # 香港相关
            '香港特首', '香港行政长官', '李家超', 'Lee Ka-chiu', '林郑月娥', ' Carrie Lam',
            '港府', '香港政府', '港府', '香港立法会', '立法会', '区议会',
            '一国两制', '港人治港', '高度自治', '国安法', '香港国安法',
            '港独', '香港独立', '反送中', '占中', '雨伞运动', '黑暴',
            '国安处', '香港警方', '香港警察', '警队',
            '大湾区', '粤港澳大湾区', '深港', '港深',
            # 台湾相关（明确区分）
            '台湾总统', '台湾领导人', '台湾当局', '台当局', '台湾政府',
            '赖清德', 'Lai Ching-te', '蔡英文', 'Tsai Ing-wen', 'Tsai',
            '马英九', 'Ma Ying-jeou', '陈水扁', '韩国瑜', '柯文哲', '侯友宜',
            '朱立伦', '苏贞昌', '陈建仁', '卓荣泰', '萧美琴',
            '民进党', 'DPP', '国民党', 'KMT', '台湾国民党', '亲民党', '台联',
            '立法院', '台湾立法院', '台湾国会', '行政院', '司法院',
            '台独', '台湾独立', '独派', '统派', '九二共识', '一中各表',
            '台海', '台湾海峡', '两岸关系', '两岸统一', '和平统一', '武统',
            '台湾大选', '台湾选举', '总统大选', '县市选举',
            '台湾军方', '台军', '国军', '台湾国防部', '台湾军方',
            '美台', '台美', '台日', '日台', '台欧',
            # 澳门相关
            '澳门特首', '澳门行政长官', '岑浩辉', '贺一诚',
            '澳门政府', '澳府', '澳门立法会',
            # 英文
            'Hong Kong', 'Taiwan', 'Macau', 'Macao',
            'Hongkonger', 'Taiwanese', 'Macanese',
        ],
        'description': '香港、台湾、澳门政治与社会新闻（区别于中国大陆）',
    },
    'asia_neighbors': {
        'entities': [
            # 韩国和朝鲜（半岛）
            '韩国', 'Korea', 'Korean', 'South Korea', '首尔', 'Seoul',
            '尹锡悦', 'Yoon', 'Yoon Suk-yeol', '李在明', 'Lee Jae-myung',
            '朴槿惠', 'Park', '文在寅', 'Moon', '金大中', 'Kim Dae-jung',
            '韩国总统', '韩国国会', '大国家党', '民主党', '共同民主党',
            '朝鲜', 'North Korea', '北韩', '平壤', 'Pyongyang',
            '金正恩', 'Kim Jong-un', '金正恩', '金正日', 'Kim Jong-il',
            '金与正', 'Kim Yo-jong', '朝鲜劳动党', '朝鲜军方',
            '朝核', '朝鲜核', '核武器', '洲际导弹', '导弹试射',
            '韩朝', '南北韩', '朝韩', '半岛局势', '朝鲜半岛',
            # 蒙古
            '蒙古', 'Mongolia', '蒙古国', '乌兰巴托', 'Ulaanbaatar',
            # 东南亚 - 越南
            '越南', 'Vietnam', 'Vietnamese', '河内', 'Hanoi', '胡志明市',
            '阮富仲', 'Nguyen Phu Trong', '武文赏', '范明政',
            # 东南亚 - 泰国
            '泰国', 'Thailand', 'Thai', '曼谷', 'Bangkok',
            '泰王', '泰国王室', '泰国总理', '巴育', 'Prayut',
            # 东南亚 - 菲律宾
            '菲律宾', 'Philippines', 'Philippine', '马尼拉', 'Manila',
            '马科斯', 'Marcos', '小马科斯', '杜特尔特', 'Duterte',
            '菲律宾总统', '菲军方', '南海争端', '仁爱礁', '仙宾礁',
            # 东南亚 - 马来西亚
            '马来西亚', 'Malaysia', 'Malaysian', '吉隆坡', 'Kuala Lumpur',
            '安华', 'Anwar', '马哈迪', 'Mahathir', '纳吉', 'Najib',
            '马来西亚总理', '马国',
            # 东南亚 - 印尼
            '印尼', 'Indonesia', 'Indonesian', '印度尼西亚',
            '雅加达', 'Jakarta', '苏加诺', '苏哈托', '佐科', 'Jokowi',
            '普拉博沃', 'Prabowo', '印尼总统',
            # 东南亚 - 新加坡
            '新加坡', 'Singapore', 'Singaporean',
            '李显龙', 'Lee Hsien Loong', '黄循财', 'Lawrence Wong',
            '新加坡总理', '新加坡政府',
            # 东南亚 - 缅甸
            '缅甸', 'Myanmar', 'Burma', '仰光', 'Yangon', '奈比多', 'Naypyidaw',
            '缅甸军方', '缅甸军政府', '昂山素季', 'Aung San Suu Kyi',
            '敏昂莱', 'Min Aung Hlaing', '缅北', '缅军',
            # 东南亚 - 柬埔寨
            '柬埔寨', 'Cambodia', 'Cambodian', '金边', 'Phnom Penh',
            '洪森', 'Hun Sen', '洪马奈', 'Hun Manet',
            # 东南亚 - 老挝
            '老挝', 'Laos', 'Lao', '万象', 'Vientiane',
            # 东南亚 - 东帝汶
            '东帝汶', 'Timor-Leste', 'East Timor',
            # 南亚 - 印度
            '印度', 'India', 'Indian', '新德里', 'New Delhi', '德里', 'Delhi',
            '莫迪', 'Modi', 'Narendra Modi', '印度总理', '印度总统',
            '印度国会', '印度人民党', 'BJP', '印度国大党',
            '印度军方', '印军', '印度空军', '印度海军',
            '印中', '中印', '印巴', '印俄', '印美',
            '边境冲突', '拉达克', '阿鲁纳恰尔邦',
            # 南亚 - 巴基斯坦
            '巴基斯坦', 'Pakistan', 'Pakistani', '伊斯兰堡', 'Islamabad',
            '巴基斯坦总理', '巴基斯坦军方', '巴军',
            '印巴冲突', '克什米尔', 'Kashmir',
            # 南亚 - 孟加拉国
            '孟加拉', 'Bangladesh', 'Bangladeshi', '达卡', 'Dhaka',
            '孟加拉国', '孟加拉总理',
            # 南亚 - 斯里兰卡
            '斯里兰卡', 'Sri Lanka', 'Sri Lankan', '科伦坡', 'Colombo',
            # 南亚 - 尼泊尔
            '尼泊尔', 'Nepal', 'Nepali', '加德满都', 'Kathmandu',
            # 南亚 - 不丹
            '不丹', 'Bhutan', '廷布', 'Thimphu',
            # 中亚五国
            '哈萨克斯坦', 'Kazakhstan', 'Kazakh', '阿斯塔纳', 'Astana',
            '乌兹别克斯坦', 'Uzbekistan', 'Uzbek', '塔什干', 'Tashkent',
            '吉尔吉斯斯坦', 'Kyrgyzstan', 'Kyrgyz', '比什凯克', 'Bishkek',
            '塔吉克斯坦', 'Tajikistan', 'Tajik', '杜尚别', 'Dushanbe',
            '土库曼斯坦', 'Turkmenistan', 'Turkmen', '阿什哈巴德', 'Ashgabat',
            # 阿富汗
            '阿富汗', 'Afghanistan', 'Afghan', '喀布尔', 'Kabul',
            # 区域组织
            '东盟', 'ASEAN', '东南亚国家联盟',
            '亚太', 'Asia-Pacific', 'APEC', 'RCEP',
        ],
        'description': '亚洲周边国家新闻（不含中日港澳台）',
    },
}


def _classify_narrative(title: str, entities: list, summary: str = '') -> list:
    """
    根据叙事的标题、实体和摘要，判断它属于哪些分类

    Returns:
        匹配的 category_id 列表（一个叙事可能属于多个分类）
    """
    text = f"{title} {' '.join(entities)} {summary}".lower()
    matched = []

    # 分类优先级：
    # 1. hk_tw_macau 最高优先（港澳台不被中国大陆吞掉）
    # 2. asia_neighbors（亚洲周边不被其他分类吞掉）
    # 3. middle_east（中东冲突明确区分）
    # 4. japan_news（日本明确区分）
    # 5. us_news（美国明确区分）
    # 6. china_related 最后（中国大陆作为兜底）
    PRIORITY_ORDER = ['hk_tw_macau', 'asia_neighbors', 'middle_east', 'japan_news', 'us_news', 'china_related']

    for cat_id in PRIORITY_ORDER:
        if cat_id not in CATEGORY_ENTITY_MAP:
            continue
        cat_info = CATEGORY_ENTITY_MAP[cat_id]
        for entity in cat_info['entities']:
            if entity.lower() in text:
                matched.append(cat_id)
                break  # 一个实体匹配即可确认此分类

    # 如果匹配了 hk_tw_macau，移除 china_related（港澳台新闻不应同时归类为中国大陆）
    if 'hk_tw_macau' in matched and 'china_related' in matched:
        matched.remove('china_related')

    # 如果匹配了 asia_neighbors，移除 china_related（亚洲周边不归类为中国）
    if 'asia_neighbors' in matched and 'china_related' in matched:
        matched.remove('china_related')

    return matched if matched else ['uncategorized']


# ═══════════════════════════════════════════════════════════════════
# LLM 叙事提取
# ═══════════════════════════════════════════════════════════════════

NARRATIVE_EXTRACTION_PROMPT = """你是一位资深国际新闻编辑，擅长从大量新闻报道中提炼出正在发生的核心新闻事件。

以下是按相似度分组后的新闻标题集合。每组代表一个可能的新闻事件。
请分析这些分组，提炼出真正的重大新闻叙事。

## 要求
1. 为每个有效的新闻事件生成：
   - **title**：简洁的中文事件标题（15-25字）
   - **entities**：核心实体列表（人名、国名、机构名，3-6个）
   - **summary**：事件概要（30-50字）
   - **importance**：重要性等级（"high" / "medium" / "low"）
2. 合并实质上报道同一事件的分组
3. 忽略不构成重大新闻的琐碎分组（如体育花边、娱乐八卦）
4. 只保留确实有新闻价值的事件

## 输出格式
严格输出 JSON 数组，不要有任何其他文字：
[
  {
    "title": "事件标题",
    "entities": ["实体1", "实体2", "实体3"],
    "summary": "简要概括",
    "importance": "high",
    "group_indices": [0, 3]
  }
]

group_indices 是该叙事对应的输入分组序号（从0开始）。
如果多个分组是同一事件，合并它们的序号。"""


def _build_cluster_summary(clusters: list, max_clusters: int = 25) -> str:
    """
    将聚类结果构建为 LLM 可读的文本

    只取最显著的 top-N 聚类，按文章数量排序
    """
    # 按文章数量降序排序
    sorted_clusters = sorted(clusters, key=lambda c: len(c['items']), reverse=True)
    top_clusters = sorted_clusters[:max_clusters]

    lines = []
    for idx, cluster in enumerate(top_clusters):
        items = cluster['items']
        # 取 top-5 标题（按权威度排序，避免重复）
        seen_titles = set()
        titles = []
        for a in items:
            t = a.get('title', '')[:60] # 进一步缩短单条标题长度到60字
            if t and t not in seen_titles:
                seen_titles.add(t)
                platform = a.get('platform', '').split('|')[0]
                titles.append(f"[{platform}] {t}")
            if len(titles) >= 3: # 从每组 5 条降为 3 条代表性标题
                break

        # 统计媒体来源
        media_set = set()
        for a in items:
            mg = a.get('media_group', '') or a.get('platform', '').split('|')[0]
            if mg:
                media_set.add(mg)

        lines.append(f"--- 分组 {idx} ({len(items)}篇, {len(media_set)}家媒体) ---")
        lines.extend(titles)
        lines.append("")

    return "\n".join(lines)


def extract_narratives(clusters: list, llm_cfg: dict, provider: str = None,
                       max_clusters: int = 12, quiet: bool = False) -> list:
    """
    核心函数：从文章聚类中提取叙事

    Args:
        clusters: _cluster_articles() 的输出，每项含 {'representative': ..., 'items': [...]}
        llm_cfg: load_llm_config() 的完整配置
        provider: 指定 LLM 提供商（覆盖配置）
        max_clusters: 最多处理的聚类数量
        quiet: 静默模式（不输出调试信息）

    Returns:
        叙事列表，每项含：
        {
            'title': str,       # 叙事标题
            'entities': list,   # 核心实体
            'summary': str,     # 事件概要
            'importance': str,  # high/medium/low
            'categories': list, # 所属分类 ID 列表
            'cluster_indices': list,  # 对应的原始聚类索引
            'items': list,      # 关联的文章列表
            'score': float,     # 热度分数
        }
    """
    if not clusters:
        return []

    # 筛选显著聚类（至少 2 篇文章）
    significant = [c for c in clusters if len(c['items']) >= 2]
    if not significant:
        return []

    # 按文章数降序
    significant.sort(key=lambda c: len(c['items']), reverse=True)
    significant = significant[:max_clusters]

    if not quiet:
        print(f"[叙事提取] 输入 {len(significant)} 个显著聚类（共 {sum(len(c['items']) for c in significant)} 篇文章）")

    # 构建 LLM 输入
    cluster_text = _build_cluster_summary(significant)

    # 覆盖 provider
    cfg = dict(llm_cfg) if llm_cfg else {}
    if provider and cfg.get('llm'):
        cfg = {**cfg, 'llm': {**cfg['llm'], 'provider': provider}}

    messages = [
        {"role": "system", "content": NARRATIVE_EXTRACTION_PROMPT},
        {"role": "user", "content": f"请从以下 {len(significant)} 个新闻分组中提炼核心叙事：\n\n{cluster_text}"}
    ]

    if not quiet:
        print(f"\n[DEBUG 叙事提取 - 请求大模型] Provider: {cfg.get('llm', {}).get('provider', '默认')}")
        print(f"================== 大模型 Prompt ==================")
        print(f"System: {NARRATIVE_EXTRACTION_PROMPT[:100]}...")
        print(f"User (前 500 字): \n{cluster_text[:500]}...")
        print(f"===================================================")

    raw = _call_llm_api(messages, cfg)

    if not quiet:
        print(f"\n================== 大模型 返回原始内容 ==================")
        if raw:
            print(f"{raw[:1000]}{'...' if len(raw)>1000 else ''}")
        else:
            print("返回为空或发生错误！(请检查控制台错误日志)")
        print(f"=========================================================")

    if not raw:
        if not quiet:
            print("[WARN] LLM 叙事提取返回空结果，降级为本地模式")
        return _fallback_local_narratives(significant, quiet=quiet)

    # 解析 JSON
    narratives = _parse_narrative_response(raw, significant, quiet=quiet)
    if not quiet:
        print(f"[叙事提取] LLM 提炼出 {len(narratives)} 个叙事")

    return narratives


def _parse_narrative_response(raw: str, clusters: list, quiet: bool = False) -> list:
    """解析 LLM 返回的叙事 JSON"""
    try:
        # 提取 JSON 数组
        json_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', raw)
        if not json_match:
            if not quiet:
                print(f"[WARN] 叙事提取返回格式异常: {raw[:200]}")
            return _fallback_local_narratives(clusters, quiet=quiet)

        parsed = json.loads(json_match.group())
        if not isinstance(parsed, list):
            return _fallback_local_narratives(clusters, quiet=quiet)

        narratives = []
        for item in parsed:
            title = item.get('title', '')
            entities = item.get('entities', [])
            summary = item.get('summary', '')
            importance = item.get('importance', 'medium')
            group_indices = item.get('group_indices', [])

            if not title:
                continue

            # 收集关联的文章
            all_items = []
            for idx in group_indices:
                if 0 <= idx < len(clusters):
                    all_items.extend(clusters[idx]['items'])

            # 如果 LLM 没给 group_indices，尝试根据标题匹配
            if not all_items:
                all_items = _match_articles_to_narrative(title, entities, clusters)

            if not all_items:
                continue

            # 去重文章（by id）
            seen_ids = set()
            unique_items = []
            for a in all_items:
                aid = a.get('id') or a.get('url_hash', '')
                if aid and aid not in seen_ids:
                    seen_ids.add(aid)
                    unique_items.append(a)

            # 自动分类
            categories = _classify_narrative(title, entities, summary)

            # 计算热度分数
            score = _calc_narrative_score(unique_items, importance)

            narratives.append({
                'title': title,
                'entities': entities,
                'summary': summary,
                'importance': importance,
                'categories': categories,
                'cluster_indices': group_indices,
                'items': unique_items,
                'count': len(unique_items),
                'score': score,
            })

        # 按分数排序
        narratives.sort(key=lambda x: x['score'], reverse=True)
        return narratives

    except (json.JSONDecodeError, Exception) as e:
        if not quiet:
            print(f"[WARN] 叙事解析失败: {e}")
        return _fallback_local_narratives(clusters, quiet=quiet)


def _match_articles_to_narrative(title: str, entities: list, clusters: list) -> list:
    """当 LLM 没返回 group_indices 时，根据标题和实体模糊匹配聚类"""
    matched = []
    search_text = f"{title} {' '.join(entities)}".lower()

    for cluster in clusters:
        rep_title = cluster['representative'].get('title', '').lower()
        # 如果叙事标题包含聚类代表标题的关键部分
        overlap = sum(1 for e in entities if e.lower() in rep_title)
        if overlap >= 2 or any(e.lower() in rep_title for e in entities if len(e) >= 3):
            matched.extend(cluster['items'])

    return matched


def _calc_narrative_score(items: list, importance: str) -> float:
    """计算叙事热度分数"""
    if not items:
        return 0.0

    # 统计独立媒体
    media_set = set()
    for a in items:
        mg = a.get('media_group', '') or a.get('platform', '').split('|')[0].strip()
        if mg:
            media_set.add(mg)

    media_count = len(media_set)
    article_count = len(items)

    # 基础分
    cross_media_score = media_count * 8
    article_score = min(article_count * 2, 20)

    # 重要性加成
    importance_bonus = {'high': 15, 'medium': 5, 'low': 0}.get(importance, 0)

    # 时效性
    freshness_score = 0.0
    try:
        now = datetime.now(TZ_BJ)
        times = []
        for a in items:
            pub = a.get('published', '')
            if pub:
                try:
                    t = datetime.fromisoformat(pub.replace('Z', '+00:00'))
                    times.append(t)
                except:
                    pass
        if times:
            latest = max(times)
            hours_ago = (now - latest.replace(tzinfo=None)).total_seconds() / 3600
            if hours_ago <= 3:
                freshness_score = 10
            elif hours_ago <= 6:
                freshness_score = 7
            elif hours_ago <= 12:
                freshness_score = 4
            elif hours_ago <= 24:
                freshness_score = 2
    except:
        pass

    total = cross_media_score + article_score + importance_bonus + freshness_score
    return round(total, 1)


def _fallback_local_narratives(clusters: list, quiet: bool = False) -> list:
    """
    LLM 调用失败时的降级方案：用本地信息构建叙事

    直接使用聚类代表标题作为叙事标题，提取简单实体
    """
    if not quiet:
        print("[INFO] 使用本地降级模式生成叙事")
    narratives = []

    sorted_clusters = sorted(clusters, key=lambda c: len(c['items']), reverse=True)

    for idx, cluster in enumerate(sorted_clusters[:15]):
        items = cluster['items']
        if len(items) < 2:
            continue

        title = cluster['representative'].get('title', '未知事件')[:60]
        entities = _extract_entities_from_items(items)
        categories = _classify_narrative(title, entities)
        score = _calc_narrative_score(items, 'medium')

        narratives.append({
            'title': title,
            'entities': entities,
            'summary': '',
            'importance': 'medium',
            'categories': categories,
            'cluster_indices': [idx],
            'items': items,
            'count': len(items),
            'score': score,
        })

    narratives.sort(key=lambda x: x['score'], reverse=True)
    return narratives


def _extract_entities_from_items(items: list) -> list:
    """从文章列表中提取高频实体"""
    all_text = ' '.join(a.get('title', '') for a in items)

    # 合并所有分类的实体词典
    all_entities = set()
    for cat_info in CATEGORY_ENTITY_MAP.values():
        all_entities.update(cat_info['entities'])

    # 统计命中的实体
    found = Counter()
    for entity in all_entities:
        count = all_text.lower().count(entity.lower())
        if count > 0:
            found[entity] = count

    return [e for e, _ in found.most_common(6)]


# ═══════════════════════════════════════════════════════════════════
# 叙事 → Event 格式转换（兼容现有前端）
# ═══════════════════════════════════════════════════════════════════

def narrative_to_event(narrative: dict) -> dict:
    """
    将叙事转换为现有系统的 event 格式

    确保前端 hotspot.html 无需修改即可展示
    """
    items = narrative.get('items', [])

    # 计算时间信息
    published_times = [a.get('published', '') for a in items if a.get('published')]
    published_times.sort()
    latest_published = published_times[-1] if published_times else ''
    first_published = published_times[0] if published_times else ''

    # 统计平台
    main_media_set = set()
    all_platforms = set()
    for a in items:
        platform = a.get('platform', '')
        media_group = a.get('media_group', '')
        if media_group:
            main_media_set.add(media_group)
        elif platform:
            main_media_set.add(platform.split('|')[0].strip())
        if platform:
            all_platforms.add(platform)

    return {
        'title': narrative.get('title', ''),
        'score': narrative.get('score', 0),
        'count': len(items),
        'latest_published': latest_published,
        'first_published': first_published,
        'platforms': list(main_media_set),
        'all_platforms': list(all_platforms),
        'tags': narrative.get('entities', []),
        'is_china_related': 'china_related' in narrative.get('categories', []),
        'score_details': {
            'media_count': len(main_media_set) if len(main_media_set) > 0 else len(all_platforms),
            'article_count': len(items),
            'importance': narrative.get('importance', 'medium'),
            'narrative_summary': narrative.get('summary', ''),
        },
        'items': items,
    }


def filter_narratives_by_category(narratives: list, category_id: str) -> list:
    """按分类筛选叙事"""
    return [n for n in narratives if category_id in n.get('categories', [])]


# ═══════════════════════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("叙事提取模块测试")
    print("请通过 scheduled_hotspot.py 调用此模块")
