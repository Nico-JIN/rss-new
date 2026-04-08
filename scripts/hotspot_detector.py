#!/usr/bin/env python3
"""
热点事件检测引擎 — 轻量级本地算法，支持动态时间窗口

设计原则：
1. 纯本地算法，不依赖LLM，毫秒级响应
2. 时间窗口可配置（3h/6h/12h/24h等）
3. 多维度热度评分：跨媒体、连续报道、媒体权威度、时效性、中国相关
4. 独立模块，不影响现有功能

使用方式：
    from hotspot_detector import detect_hot_events

    events = detect_hot_events(hours=6)  # 获取近6小时热点
    events = detect_hot_events(hours=24) # 获取近24小时热点
"""

import re
import json
import sys
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from pathlib import Path

# 尝试导入项目模块
try:
    from store import get_conn, TZ_BJ, _retry_on_locked
    from llm_tagger import load_llm_config, is_english_text, batch_translate_to_chinese, translate_text
    from filter_utils import is_chinese_media
except ImportError:
    import sys
    sys.path.append(str(Path(__file__).parent))
    from store import get_conn, TZ_BJ, _retry_on_locked
    from llm_tagger import load_llm_config, is_english_text, batch_translate_to_chinese, translate_text
    from filter_utils import is_chinese_media


# ═══════════════════════════════════════════════════════════════════
# 配置区
# ═══════════════════════════════════════════════════════════════════

# 中国相关关键词
CHINA_KEYWORDS = {
    '中国', '北京', '中方', '外交部', '华', '南海', '台海', '台湾', '香港',
    '贸易战', '一带一路', '习近平', '李克强', '政治局', '国防部', '解放军',
    '制裁中国', '对华', '中美', '中欧', '中俄', '中日', '中韩', '中印'
}

# 核心媒体权威度权重（影响热度评分）
# Tier 1: 国际顶级通讯社/媒体
TIER1_MEDIA = {
    '路透社', 'reuters', '美联社', 'ap news', '法新社', 'afp',
    '纽约时报', 'new york times', 'nyt', '华尔街日报', 'wsj', 'wall street journal',
    '金融时报', 'financial times', 'ft', 'bbc', '卫报', 'the guardian',
    '经济学人', 'economist', '华盛顿邮报', 'washington post',
    'cnn', 'nbc', 'abc news', 'cbs news', 'bloomberg'
}

# Tier 2: 重要国际媒体
TIER2_MEDIA = {
    '半岛电视台', 'al jazeera', '德国之声', 'dw', '法国24', 'france 24',
    'nhk', '读卖新闻', '朝日新闻', '产经新闻',
    '联合早报', 'zaobao', '南华早报', 'scmp', 'south china morning post',
    '海峡时报', 'straits times', '印度时报', 'times of india',
    '塔斯社', 'tass', '今日俄罗斯', 'rt', '卫星通讯社', 'sputnik'
}

# Tier 3: 中文主流媒体
TIER3_MEDIA = {
    '新华社', '人民日报', '央视', '环球时报', '中国日报', 'chinadaily',
    '参考消息', '光明日报', '经济日报', '中新社', '中青报',
    '澎湃', '界面', '第一财经', '财新', '观察者网', '环球网'
}

def get_media_weight(platform: str) -> float:
    """获取媒体权威度权重"""
    if not platform:
        return 1.0

    platform_lower = platform.lower().strip()

    # 检查 Tier 1
    for m in TIER1_MEDIA:
        if m in platform_lower or platform_lower in m:
            return 1.5

    # 检查 Tier 2
    for m in TIER2_MEDIA:
        if m in platform_lower or platform_lower in m:
            return 1.3

    # 检查 Tier 3
    for m in TIER3_MEDIA:
        if m in platform_lower or platform_lower in m:
            return 1.2

    return 1.0


# ═══════════════════════════════════════════════════════════════════
# 核心算法
# ═══════════════════════════════════════════════════════════════════

def _tokenize(text: str) -> list:
    """分词：提取中文词组和英文单词"""
    if not text:
        return []
    # 中文连续字符（2字以上）+ 英文单词（3字母以上）
    words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', text)
    return [w.lower() for w in words if len(w) > 1]


def _extract_entities(text: str) -> set:
    """提取命名实体（人名、地名、机构名的简单识别）"""
    if not text:
        return set()

    entities = set()

    # 常见国际人物名（可扩展）
    person_patterns = [
        r'特朗普|拜登|普京|泽连斯基|习近平|马克龙|苏纳克|岸田文雄|尹锡悦|莫迪',
        r'金正恩|哈梅内伊|内塔尼亚胡|埃尔多安|朔尔茨|冯德莱恩|耶伦|布林肯',
        r'Trump|Biden|Putin|Zelensky|Xi Jinping|Macron|Modi|Kim Jong Un'
    ]

    # 常见国家/地名
    place_patterns = [
        r'美国|中国|俄罗斯|乌克兰|日本|韩国|朝鲜|伊朗|以色列|巴勒斯坦',
        r'台湾|香港|南海|中东|欧洲|欧盟|北约|东盟|非洲|拉美',
        r'US|USA|China|Russia|Ukraine|Japan|Korea|Iran|Israel|Taiwan'
    ]

    # 常见机构名
    org_patterns = [
        r'白宫|克里姆林宫|国防部|外交部|联合国|世卫组织|世贸组织',
        r'中央银行|美联储|欧央行|IMF|世界银行|OPEC|欧盟委员会',
        r'White House|Pentagon|UN|WHO|WTO|Fed|ECB|IMF'
    ]

    for pattern in person_patterns + place_patterns + org_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        entities.update(m.lower() for m in matches)

    return entities


def _calc_similarity(title1: str, title2: str) -> float:
    """
    计算两篇文章标题的相似度
    综合考虑：词重叠、实体重叠、字符级相似
    """
    if not title1 or not title2:
        return 0.0

    # 1. 词汇Jaccard相似度
    words1 = set(_tokenize(title1))
    words2 = set(_tokenize(title2))

    if words1 and words2:
        word_jaccard = len(words1 & words2) / len(words1 | words2)
    else:
        word_jaccard = 0.0

    # 2. 命名实体重叠（权重更高）
    entities1 = _extract_entities(title1)
    entities2 = _extract_entities(title2)

    if entities1 and entities2:
        entity_overlap = len(entities1 & entities2) / min(len(entities1), len(entities2))
        # 实体完全匹配时大幅提升相似度
        if entities1 == entities2:
            entity_score = 0.8
        else:
            entity_score = entity_overlap * 0.5
    else:
        entity_score = 0.0

    # 3. 字符级bigram相似度（处理标题改写情况）
    def bigram(s):
        s = re.sub(r'\s+', '', s.lower())
        return set(s[i:i+2] for i in range(len(s) - 1))

    bg1, bg2 = bigram(title1), bigram(title2)
    if bg1 and bg2:
        char_jaccard = len(bg1 & bg2) / len(bg1 | bg2)
    else:
        char_jaccard = 0.0

    # 综合评分
    final_score = (
        word_jaccard * 0.4 +    # 词汇相似度
        entity_score * 0.4 +     # 实体重合度
        char_jaccard * 0.2       # 字符相似度
    )

    return final_score


def _calc_continuous_coverage_score(articles: list) -> float:
    """
    计算连续报道加成分数
    如果同一事件在多个时间点被报道，说明事件持续发酵，热度更高
    """
    if len(articles) < 2:
        return 0.0

    # 按时间排序
    sorted_articles = sorted(articles, key=lambda x: x.get('published', ''))

    # 计算报道时间跨度
    try:
        times = [datetime.fromisoformat(a['published'].replace('Z', '+00:00'))
                 for a in sorted_articles if a.get('published')]
        if len(times) < 2:
            return 0.0

        time_span = (max(times) - min(times)).total_seconds() / 3600  # 小时

        # 划分时间段，统计每个时段是否有报道
        if time_span <= 0:
            return 0.0

        num_buckets = min(12, max(3, int(time_span / 2)))  # 每2小时一个桶，最多12个
        bucket_size = time_span / num_buckets

        buckets = set()
        for t in times:
            bucket_idx = int((t - min(times)).total_seconds() / 3600 / bucket_size)
            buckets.add(bucket_idx)

        coverage_ratio = len(buckets) / num_buckets

        # 连续报道加成：覆盖时段越多，加成越高
        # 3个时段以上才算"连续报道"
        if len(buckets) >= 3:
            return coverage_ratio * 15  # 最多15分
        elif len(buckets) >= 2:
            return coverage_ratio * 8   # 2个时段给较低加成
        else:
            return 0.0

    except Exception:
        return 0.0


def _cluster_articles(articles: list, threshold: float = 0.35) -> list:
    """
    文章聚类：将相似的文章归为同一事件簇

    使用增量聚类算法，避免两两比较的O(n²)复杂度
    """
    if not articles:
        return []

    clusters = []  # 每个元素: {'representative': article, 'items': [articles]}

    for article in articles:
        title = article.get('title', '')
        if not title:
            continue

        best_cluster_idx = -1
        best_score = 0.0

        # 与已有簇的代表文章比较
        for idx, cluster in enumerate(clusters):
            rep_title = cluster['representative'].get('title', '')
            score = _calc_similarity(title, rep_title)

            if score > threshold and score > best_score:
                best_score = score
                best_cluster_idx = idx

        if best_cluster_idx >= 0:
            # 加入已有簇
            clusters[best_cluster_idx]['items'].append(article)
        else:
            # 创建新簇
            clusters.append({
                'representative': article,
                'items': [article]
            })

    return clusters


def _calc_cluster_score(cluster: list, hours: int) -> dict:
    """
    计算事件簇的热度评分

    评分维度：
    1. 媒体数量（跨媒体报道）- 权重最高
    2. 文章数量
    3. 媒体权威度
    4. 时效性（最近报道时间）
    5. 连续报道加成
    6. 中国相关加成
    """
    if not cluster:
        return {'score': 0, 'details': {}}

    # 统计媒体（使用主媒体名称，而非具体频道）
    # 优先使用 media_group，否则从 platform 中提取主媒体名（取 | 前的部分）
    main_media_set = set()
    platforms = set()
    total_media_weight = 0.0

    for article in cluster:
        platform = article.get('platform', '')
        media_group = article.get('media_group', '')

        # 提取主媒体名称
        if media_group:
            main_media_set.add(media_group)
        elif platform:
            # 从 platform 中提取主媒体名（如 "路透社|China" -> "路透社"）
            main_media = platform.split('|')[0].strip()
            main_media_set.add(main_media)

        if platform:
            platforms.add(platform)
            total_media_weight += get_media_weight(platform)

    # 基础评分
    article_count = len(cluster)
    media_count = len(main_media_set)  # 使用主媒体数量

    # 只保留跨媒体报道的事件（至少2家媒体）
    if media_count < 2:
        return {'score': 0, 'details': {'reason': 'single_media', 'main_media': list(main_media_set)}}

    # 计算各维度分数
    # 1. 跨媒体分数（核心指标）
    cross_media_score = media_count * 8

    # 2. 文章数量分数
    article_score = min(article_count * 2, 20)  # 上限20分

    # 3. 媒体权威度平均分
    avg_media_weight = total_media_weight / media_count if media_count > 0 else 1.0
    authority_score = (avg_media_weight - 1.0) * 10  # 转换为分数

    # 4. 时效性分数
    freshness_score = 0.0
    try:
        now = datetime.now(TZ_BJ)
        latest_time = max(
            datetime.fromisoformat(a['published'].replace('Z', '+00:00'))
            for a in cluster if a.get('published')
        )
        hours_ago = (now - latest_time.replace(tzinfo=None)).total_seconds() / 3600

        if hours_ago <= 3:
            freshness_score = 10
        elif hours_ago <= 6:
            freshness_score = 7
        elif hours_ago <= 12:
            freshness_score = 4
        elif hours_ago <= 24:
            freshness_score = 2
    except Exception:
        pass

    # 5. 连续报道加成
    continuous_score = _calc_continuous_coverage_score(cluster)

    # 6. 中国相关加成
    china_score = 0.0
    all_titles = ' '.join(a.get('title', '') for a in cluster)
    if any(kw in all_titles for kw in CHINA_KEYWORDS):
        china_score = 12

    # 总分
    total_score = (
        cross_media_score +
        article_score +
        authority_score +
        freshness_score +
        continuous_score +
        china_score
    )

    return {
        'score': round(total_score, 1),
        'details': {
            'media_count': media_count,
            'article_count': article_count,
            'cross_media_score': round(cross_media_score, 1),
            'article_score': round(article_score, 1),
            'authority_score': round(authority_score, 1),
            'freshness_score': round(freshness_score, 1),
            'continuous_score': round(continuous_score, 1),
            'china_score': round(china_score, 1),
            'platforms': list(main_media_set),  # 返回主媒体列表
            'all_platforms': list(platforms),   # 保留完整平台信息供参考
        }
    }


def _translate_title_to_chinese(title: str) -> str:
    """如果标题已是中文，则直接返回；如果是外语则翻译"""
    if not title: return title
    if not is_english_text(title): return title
    cfg = load_llm_config()
    if not cfg: return title
    # 调用集中式的翻译工具
    translated = translate_text(title, cfg)
    if translated and translated != title:
        print(f"[INFO] 标题自动翻译: '{title[:30]}...' -> '{translated[:30]}...'")
    return translated or title



def _generate_event_title(cluster: list) -> str:
    """
    为事件簇生成标题
    策略：选择热度最高的媒体报道的标题，或提取共同实体组合
    无论原文是什么语言，标题都会被翻译成中文
    """
    if not cluster:
        return "未知事件"

    # 按媒体权威度排序
    sorted_articles = sorted(
        cluster,
        key=lambda a: get_media_weight(a.get('platform', '')),
        reverse=True
    )

    # 使用权威度最高媒体的标题
    best_title = sorted_articles[0].get('title', '')

    # 翻译成中文（如果是英文）
    best_title = _translate_title_to_chinese(best_title)

    # 如果标题过长，截取前60字符
    if len(best_title) > 60:
        # 尝试在句号/问号处截断
        for end_char in ['。', '？', '！', '.', '?', '!']:
            pos = best_title.find(end_char)
            if 20 < pos < 60:
                best_title = best_title[:pos + 1]
                break
        else:
            best_title = best_title[:57] + '...'

    return best_title


def _extract_keywords(cluster: list, top_n: int = 5) -> list:
    """提取事件关键词"""
    all_words = []
    for article in cluster:
        all_words.extend(_tokenize(article.get('title', '')))

    word_counts = Counter(all_words)
    # 过滤停用词
    stopwords = {'的', '了', '是', '在', '和', '与', '对', '称', '说', '表示', '报道', '据', '这', '那'}
    keywords = [w for w, _ in word_counts.most_common(top_n + 5) if w not in stopwords]

    return keywords[:top_n]


# ═══════════════════════════════════════════════════════════════════
# LLM 研判验证（针对前三名热点）
# ═══════════════════════════════════════════════════════════════════

def _llm_verify_hotspot_articles(event_title: str, articles: list, llm_cfg: dict) -> list:
    """
    使用 LLM 研判验证文章是否真正属于该热点事件

    Args:
        event_title: 热点事件标题
        articles: 待验证的文章列表
        llm_cfg: LLM 配置

    Returns:
        经过验证的文章列表（剔除不符合的）
    """
    if not llm_cfg or not llm_cfg.get('llm', {}).get('enabled'):
        return articles

    if not articles:
        return articles

    # 构建验证 prompt - 注意使用 {{}} 转义 JSON 示例中的大括号
    titles_text = "\n".join([
        f"{i+1}. {a.get('title', '无标题')}"
        for i, a in enumerate(articles)
    ])

    prompt = f"""你是一位新闻分析专家。请判断以下新闻标题是否与给定的热点事件相关。

热点事件主题：{event_title}

待验证的新闻标题列表：
{titles_text}

请分析每个标题，判断其是否在报道同一事件或高度相关的事件。
- 如果标题明确报道同一核心事件（人物、时间、地点、核心议题相同），标记为"符合"
- 如果标题报道的是相关但不同的事件（如同一主题的不同侧面、背景分析、评论），标记为"相关"
- 如果标题报道的是完全不同的事件，标记为"不符合"

输出格式（JSON数组，每项包含序号和判断结果）：
[
  {{ "index": 1, "result": "符合", "reason": "简短理由" }}
]

只输出JSON数组，不要有其他文字。"""

    try:
        # 调用 LLM - 使用 messages 格式
        from llm_tagger import _call_llm_api
        messages = [
            {"role": "system", "content": "你是一位新闻分析专家，擅长判断新闻标题是否属于同一事件。"},
            {"role": "user", "content": prompt}
        ]
        response = _call_llm_api(messages, llm_cfg)

        if not response:
            print(f"[WARN] LLM 研判返回空结果")
            return articles

        # 解析 JSON 结果
        import json
        # 提取 JSON 数组部分
        json_match = re.search(r'\[[\s\S]*\]', response)
        if not json_match:
            print(f"[WARN] LLM 研判返回格式错误: {response[:100]}")
            return articles

        results = json.loads(json_match.group())

        # 根据验证结果筛选文章
        verified_articles = []
        removed_count = 0

        for item in results:
            idx = item.get('index', 0) - 1  # 转为 0-indexed
            if idx < 0 or idx >= len(articles):
                continue

            result = item.get('result', '').strip()
            article = articles[idx]

            if result in ['符合', '相关']:
                verified_articles.append(article)
            else:
                removed_count += 1
                print(f"[LLM研判] 剔除: [{article.get('platform', '')}] {article.get('title', '')[:40]}... → {result}")

        if removed_count > 0:
            print(f"[INFO] LLM 研判完成: 保留 {len(verified_articles)} 篇, 剔除 {removed_count} 篇")
        else:
            print(f"[INFO] LLM 研判完成: 全部 {len(verified_articles)} 篇文章验证通过")

        return verified_articles if verified_articles else articles

    except Exception as e:
        print(f"[WARN] LLM 研判失败: {e}")
        return articles


def _recalculate_cluster_score(articles: list, hours: int) -> dict:
    """
    重新计算验证后的热度评分（剔除文章后需要重新统计）
    """
    return _calc_cluster_score(articles, hours)


# ═══════════════════════════════════════════════════════════════════
# 主入口函数
# ═══════════════════════════════════════════════════════════════════

def detect_hot_events(
    hours: int = 24,
    start_time: str = None,
    end_time: str = None,
    min_media_count: int = 2,
    min_articles: int = 2,
    max_results: int = 20,
    keyword: str = None,
    conn=None
) -> list:
    """
    检测指定时间窗口内的热点事件

    Args:
        hours: 时间窗口（小时），若提供了 start_time 则忽略此参数
        start_time: 起始时间 (ISO格式字符串)
        end_time: 截止时间 (ISO格式字符串)
        min_media_count: 最少媒体数量
        min_articles: 最少文章数量
        max_results: 返回结果数量上限
        keyword: 过滤关键字 (针对标题、摘要或标签)
        conn: 数据库连接
    """
    # 计算实际的时间范围
    now = datetime.now(TZ_BJ)
    
    if start_time:
        q_start = start_time
        q_end = end_time if end_time else now.isoformat()
    else:
        q_start = (now - timedelta(hours=hours)).isoformat()
        q_end = now.isoformat()

    # 查询数据库
    own_conn = False
    if conn is None:
        conn = get_conn()
        own_conn = True

    try:
        rows = conn.execute(
            "SELECT * FROM articles WHERE published >= ? AND published <= ? ORDER BY published DESC",
            [q_start, q_end]
        ).fetchall()

        articles = []
        for row in rows:
            a = dict(row)
            # 1. 严格过滤中国媒体 (强制要求)
            if is_chinese_media(a.get('platform', ''), a.get('url', '')):
                continue
            
            # 2. 如果提供关键字，进行过滤 (增强匹配逻辑)
            if keyword:
                kw = keyword.lower()
                title = a.get('title', '').lower()
                summary = a.get('summary', '').lower()
                tags = a.get('llm_tags', '').lower()
                
                # 标题匹配权重最高，标签次之，摘要最后
                if kw in title:
                    articles.append(a)
                elif kw in tags and len(kw) > 1: # 标签匹配要求关键字不只是一个字（除非是地名）
                    articles.append(a)
                elif kw in summary and (f" {kw} " in f" {summary} " or f"【{kw}】" in summary):
                    # 摘要匹配要求更精确的词边界，防止误伤
                    articles.append(a)
            else:
                articles.append(a)

        if keyword:
            print(f"[INFO] 关键字过滤: '{keyword}', 最终保留 {len(articles)}/{len(rows)} 篇文章")

        if not articles:
            return []

        print(f"[INFO] 热点检测: 时间窗口 {hours}h, 共 {len(articles)} 篇文章")

        # [AI Translation & Persistence] 2026-04-01 补丁：确保分析中的英语内容被翻译并存回数据库
        llm_cfg = load_llm_config()
        if llm_cfg and llm_cfg.get('llm', {}).get('enabled'):
            titles_to_translate = [a['title'] for a in articles if is_english_text(a['title'])]
            if titles_to_translate:
                print(f"[INFO] 分析过程中发现 {len(titles_to_translate)} 篇英文文章，开始翻译并入库...")
                title_map = batch_translate_to_chinese(titles_to_translate, llm_cfg)

                updated_count = 0
                for a in articles:
                    orig_title = a['title']
                    if orig_title in title_map and title_map[orig_title] != orig_title:
                        new_title = title_map[orig_title]
                        a['title'] = new_title

                        # 同步检查摘要与正文 (仅在该文章标题为英文时，顺便处理其内容)
                        new_summary = a.get('summary', '')
                        if is_english_text(new_summary):
                            new_summary = translate_text(new_summary, llm_cfg)
                            a['summary'] = new_summary

                        # 注意：正文翻译较慢且贵，此处仅在标题确定需要翻译时顺带检查
                        new_content = a.get('content', '')
                        if is_english_text(new_content[:800]):
                            new_content = translate_text(new_content, llm_cfg)
                            a['content'] = new_content

                        # 持久化回数据库 (使用重试机制)
                        def _do_update():
                            conn.execute("UPDATE articles SET title = ?, summary = ?, content = ? WHERE id = ?",
                                         [new_title, new_summary, new_content, a['id']])
                        _retry_on_locked(_do_update)
                        updated_count += 1

                if updated_count > 0:
                    conn.commit()
                    print(f"[INFO] 成功翻译并同步更新 {updated_count} 篇历史英文数据至数据库")


        # 过滤无效文章
        valid_articles = []
        for a in articles:
            if a.get('id') and a.get('title'):
                valid_articles.append(a)

        if not valid_articles:
            return []

        # 聚类
        clusters = _cluster_articles(valid_articles)

        print(f"[INFO] 聚类完成: {len(clusters)} 个事件簇")

        # 计算热度并筛选
        results = []
        for cluster in clusters:
            items = cluster['items']

            if len(items) < min_articles:
                continue

            score_info = _calc_cluster_score(items, hours)

            if score_info['score'] <= 0:
                continue

            if score_info['details'].get('media_count', 0) < min_media_count:
                continue

            # 生成事件标题（使用已翻译的第一篇文章标题）
            event_title = items[0].get('title', '') or _generate_event_title(items)

            event = {
                'title': event_title,
                'score': score_info['score'],
                'count': len(items),
                'platforms': score_info['details'].get('platforms', []),  # 主媒体名列表
                'all_platforms': score_info['details'].get('all_platforms', []),  # 完整平台信息
                'tags': _extract_keywords(items),
                'is_china_related': score_info['details'].get('china_score', 0) > 0,
                'score_details': score_info['details'],
                'items': items
            }

            # 3. 如果提供了关键字，进行二次校验 (确保事件整体主题相关)
            if keyword:
                kw = keyword.lower()
                event_title_lower = event_title.lower()
                tags_str = ' '.join(event['tags']).lower()
                
                # 如果标题和核心标签都不包含关键字，则该热点不属于目标分类
                if kw not in event_title_lower and kw not in tags_str:
                    # 允许地名缩写或其他同义词匹配（暂略，仅做基础匹配）
                    continue

            results.append(event)


        # 按热度排序
        results.sort(key=lambda x: x['score'], reverse=True)

        # ═════════════════════════════════════════════════════════════
        # LLM 研判验证：对前三名热点进行智能筛选
        # ═════════════════════════════════════════════════════════════
        llm_cfg = load_llm_config()
        if llm_cfg and llm_cfg.get('llm', {}).get('enabled') and len(results) >= 1:
            print(f"\n[INFO] 开始对前 3 名热点进行 LLM 研判验证...")

            verified_count = 0
            for idx in range(min(3, len(results))):
                event = results[idx]
                event_title = event['title']
                articles = event['items']

                if len(articles) < 3:
                    # 文章数量太少，跳过研判
                    continue

                print(f"\n[LLM研判] 热点 #{idx+1}: {event_title[:40]}...")
                print(f"[LLM研判] 待验证文章数: {len(articles)}")

                # 调用 LLM 研判
                verified_articles = _llm_verify_hotspot_articles(event_title, articles, llm_cfg)

                # 如果有文章被剔除，更新事件信息
                if len(verified_articles) != len(articles):
                    event['items'] = verified_articles
                    event['count'] = len(verified_articles)

                    # 重新计算热度评分
                    new_score_info = _recalculate_cluster_score(verified_articles, hours)

                    if new_score_info['score'] > 0:
                        event['score'] = new_score_info['score']
                        event['platforms'] = new_score_info['details'].get('platforms', [])
                        event['all_platforms'] = new_score_info['details'].get('all_platforms', [])
                        event['score_details'] = new_score_info['details']
                        event['tags'] = _extract_keywords(verified_articles)
                        verified_count += 1
                        print(f"[LLM研判] 更新后热度: {event['score']} | 媒体: {len(event['platforms'])} | 文章: {event['count']}")
                    else:
                        # 验证后热度不足，标记为需移除
                        event['_remove'] = True
                        print(f"[LLM研判] 热点 #{idx+1} 验证后热度不足，将被移除")

            # 移除热度不足的事件
            results = [e for e in results if not e.get('_remove')]

            # 重新排序（可能热度评分有变化）
            results.sort(key=lambda x: x['score'], reverse=True)

            if verified_count > 0:
                print(f"\n[INFO] LLM 研判完成: {verified_count} 个热点经过验证并更新")

        # 限制返回数量
        results = results[:max_results]

        print(f"[INFO] 热点检测完成: {len(results)} 个热点事件")

        return results

    finally:
        if own_conn:
            conn.close()


def get_hot_events_brief(hours: int = 24, max_results: int = 10) -> list:
    """
    获取热点事件简要列表（不含文章详情，适合API返回）
    """
    events = detect_hot_events(hours=hours, max_results=max_results)

    brief = []
    for e in events:
        brief.append({
            'title': e['title'],
            'score': e['score'],
            'media_count': len(e['platforms']),
            'article_count': e['count'],
            'platforms': e['platforms'][:5],  # 主媒体名列表
            'all_platforms': e.get('all_platforms', [])[:6],  # 完整平台信息
            'tags': e['tags'],
            'is_china_related': e['is_china_related'],
            'articles': [
                {
                    'id': a.get('id'),
                    'title': a.get('title', ''),
                    'platform': a.get('platform', ''),
                    'published': a.get('published', ''),
                    'summary': a.get('summary', '')
                }
                for a in e['items']
            ]
        })

    return brief


# ═══════════════════════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='热点事件检测')
    parser.add_argument('--hours', type=int, default=24, help='时间窗口（小时）')
    parser.add_argument('--max', type=int, default=15, help='最大返回数量')
    parser.add_argument('--brief', action='store_true', help='简要模式')
    parser.add_argument('--json', action='store_true', help='JSON格式输出（适合Agent调用，数据完整不截断）')
    args = parser.parse_args()

    events = detect_hot_events(hours=args.hours, max_results=args.max)

    if args.json:
        # JSON 格式输出 - 数据完整，适合 Agent 调用
        output = {
            'query': {
                'hours': args.hours,
                'max_results': args.max,
                'generated_at': datetime.now(TZ_BJ).isoformat()
            },
            'count': len(events),
            'events': []
        }

        for e in events:
            # 构建完整的文章列表
            articles = []
            for item in e.get('items', []):
                articles.append({
                    'title': item.get('title', ''),
                    'url': item.get('url', ''),
                    'platform': item.get('platform', ''),
                    'media_group': item.get('media_group', ''),
                    'published': item.get('published', ''),
                    'summary': item.get('summary', ''),
                    'is_china_related': e.get('is_china_related', False)
                })

            output['events'].append({
                'rank': len(output['events']) + 1,
                'title': e['title'],
                'score': e['score'],
                'media_count': len(e.get('platforms', [])),
                'article_count': e['count'],
                'platforms': e.get('all_platforms', e.get('platforms', [])),
                'tags': e['tags'],
                'is_china_related': e.get('is_china_related', False),
                'articles': articles  # 完整文章列表，不截断
            })

        # 输出到 stdout（agent 可直接读取）
        sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2))
        sys.stdout.write('\n')

    else:
        # 传统文本格式输出（人类阅读）
        print(f"\n{'='*60}")
        print(f"热点事件检测报告 (最近 {args.hours} 小时)")
        print('='*60)

        if not events:
            print("\n暂无热点事件")
        else:
            for i, e in enumerate(events, 1):
                print(f"\n【{i}】{e['title']}")
                print(f"    热度: {e['score']} | 媒体: {e.get('media_count', len(e.get('platforms', [])))} | 文章: {e['count']}")
                # 显示完整平台信息（更有辨识度）
                all_pfs = e.get('all_platforms', e.get('platforms', []))
                if all_pfs:
                    print(f"    来源: {', '.join(all_pfs[:6])}")
                print(f"    标签: {', '.join(e['tags'])}")
                if e.get('is_china_related'):
                    print("    [中国相关]")

                # 显示文章条目详情
                items = e.get('items', [])
                if items:
                    print(f"    ── 相关文章 ({len(items)} 篇) ──")
                    for j, item in enumerate(items[:5], 1):  # 最多显示5篇
                        title = item.get('title', '无标题')[:50]
                        platform = item.get('platform', '')
                        url = item.get('url', '')
                        pub_time = item.get('published', '')[:16] if item.get('published') else ''
                        print(f"      {j}. [{platform}] {title}")
                        if url:
                            print(f"         链接: {url[:70]}{'...' if len(url) > 70 else ''}")
                        if pub_time:
                            print(f"         时间: {pub_time}")
                    if len(items) > 5:
                        print(f"      ... 还有 {len(items) - 5} 篇")

        print(f"\n{'='*60}")