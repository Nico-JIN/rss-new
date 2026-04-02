#!/usr/bin/env python3
"""情报聚合与 AI 分析引擎 — 负责热点聚类、全文获取、结构化 AI 写作
设计：
1. 聚类：基于标题重合度的轻量级算法。
2. 写作：引用文章全文，生成严谨的四段式专家分析报告。
"""

import json
import re
from datetime import datetime, timedelta
from collections import Counter
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# 导入本项目已有配置与存储工具
try:
    from store import get_conn, TZ_BJ, update_article_content
    from llm_tagger import _call_deepseek, load_llm_config, is_english_text, batch_translate_to_chinese, translate_text
except ImportError:
    # 兼容脚本直接运行
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent))
    from store import get_conn, TZ_BJ, update_article_content
    from llm_tagger import _call_deepseek, load_llm_config, is_english_text, batch_translate_to_chinese, translate_text

# --- 关键词定义 ---
CHINA_KEYWORDS = {'中国', '北京', '中方', '外交部', '华', '南海', '台海', '贸易战', '一带一路'}

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

def _tokenize(text):
    """简单的分词过滤（去除标点，保留关键词）"""
    if not text: return []
    # 提取中文字符和英文单词
    words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', text)
    return [w for w in words if len(w) > 1]

def cluster_articles(articles, threshold=0.35):
    """
    将文章列表进行聚类。
    返回: list[dict] 每个 dict 代表一个事件簇，包含标题、热度评分、文章列表。
    """
    if not articles: return []
    
    clusters = []
    
    for art in articles:
        tokens = set(_tokenize(art['title']))
        if not tokens: continue
        
        best_match = None
        max_overlap = 0
        
        for i, cluster in enumerate(clusters):
            # 取簇内第一篇作为特征
            cluster_tokens = set(_tokenize(cluster['representative_title']))
            if not cluster_tokens: continue
            
            intersection = tokens.intersection(cluster_tokens)
            union = tokens.union(cluster_tokens)
            jaccard = len(intersection) / len(union) if union else 0
            
            if jaccard > threshold and jaccard > max_overlap:
                max_overlap = jaccard
                best_match = i
                
        if best_match is not None:
            clusters[best_match]['items'].append(art)
        else:
            clusters.append({
                'representative_title': art['title'],
                'items': [art],
            })
            
    # 计算热度与中国相关性
    results = []

    # === 批量翻译优化 ===
    all_titles = set()
    for c in clusters:
        if c['representative_title'] and is_english_text(c['representative_title']):
            all_titles.add(c['representative_title'])
        for i in c['items']:
            title = i.get('title', '')
            if title and is_english_text(title):
                all_titles.add(title)

    translation_map = batch_translate_to_chinese(list(all_titles))

    for c in clusters:
        items = c['items']
        platforms = set(i.get('platform') for i in items)
        media_groups = set(i.get('media_group') for i in items)

        # 评分模型：文章数 + 跨平台权重 + 中国关键词
        base_score = len(items) * 2 + len(platforms) * 5 + len(media_groups) * 3

        china_bonus = 0
        all_text = " ".join([i['title'] for i in items])
        if any(kw in all_text for kw in CHINA_KEYWORDS):
            china_bonus = 15

        final_score = base_score + china_bonus

        # 提取关键词作为标签
        all_tokens = []
        for i in items: all_tokens.extend(_tokenize(i['title']))
        common_tags = [t for t, count in Counter(all_tokens).most_common(5)]

        # 翻译事件标题和文章标题
        translated_title = translation_map.get(c['representative_title'], c['representative_title'])
        translated_items = []
        for item in items:
            translated_item = dict(item)
            orig_title = item.get('title', '')
            translated_item['title'] = translation_map.get(orig_title, orig_title)
            translated_items.append(translated_item)

        results.append({
            'title': translated_title,
            'score': final_score,
            'count': len(items),
            'platforms': list(platforms),
            'tags': common_tags,
            'is_china_related': china_bonus > 0,
            'items': translated_items
        })

    # 按评分降序排列
    return sorted(results, key=lambda x: x['score'], reverse=True)

def llm_cluster_articles(articles, provider=None):
    """使用 LLM 对文章列表进行聚类识别"""
    if not articles: return []

    cfg = load_llm_config()
    if not cfg: return cluster_articles(articles) # 退回到简单算法

    # 如果手动指定了提供商，临时覆盖配置值
    if provider:
        cfg.setdefault('llm', {})['provider'] = provider

    # 关键修复：验证所有文章都有有效 ID，过滤掉无 ID 的文章
    valid_articles = []
    for a in articles:
        aid = a.get('id')
        if aid is None or aid == '' or str(aid).lower() in ('none', 'null', 'undefined'):
            print(f"[WARN] LLM 聚类跳过无 ID 文章: {a.get('title', '未知')[:50]}")
            continue
        valid_articles.append(a)

    if not valid_articles:
        print("[ERROR] 所有文章均无有效 ID，无法进行聚类")
        return []

    articles = valid_articles
    print(f"[INFO] LLM 聚类处理 {len(articles)} 篇有效文章")

    # 准备压缩后的数据发送给 LLM
    # 只发送 ID 和 标题，极致节省 Token
    input_data = []
    for a in articles:
        # 确保 ID 是整数
        input_data.append({"id": int(a['id']), "t": a['title']})
    
    system_prompt = """你是一个资深的新闻事件研究员或者分析人士。
你的任务是从提供的一组新闻标题中识别出“热点事件”。
“热点事件”的定义是：同一个事件被不同媒体多次报道、或在不同时间点连续更新报道。

## 输出要求：
1. 严格返回 JSON 对象，格式为：{"events": [{"title": "事件概括标题", "article_ids": [id1, id2, ...]}, ...]}
2. 只保留有 2 篇及以上文章关联的事件（即多处报道）。
3. 事件标题应简明扼要。
4. 按照事件的重要性和报道密度进行排序。
5. 不要输出任何解释文字，只输出 JSON。"""

    # 将批次规模从 100 降至 50，以提高响应成功率并减少业务超时
    batch_size = 50
    all_events = []
    
    for i in range(0, len(articles), batch_size):
        batch_input = input_data[i:i + batch_size]
        batch_full = articles[i:i + batch_size]
        
        user_prompt = f"请对以下新闻进行热点聚类：\n{json.dumps(batch_input, ensure_ascii=False)}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # 尝试调用 LLM
        raw = _call_deepseek(messages, cfg)
        
        if not raw:
            # FALLBACK: 如果 AI 失败（如触发安全过滤），退回到简单关键词聚类处理本批次
            print(f"[WARN] LLM 批量聚类调用失败 (可能是触发安全过滤)，该批次将退回到本地算法...")
            fallback_results = cluster_articles(batch_full)
            for fr in fallback_results:
                all_events.append({
                    "title": fr['title'],
                    "article_ids": [a['id'] for a in fr['items']]
                })
            continue
        
        try:
            # 清理可能的 Markdown 代码块包裹
            clean_json = re.sub(r'```json\n|\n```', '', raw).strip()
            data = json.loads(clean_json)
            batch_events = data.get('events', [])
            all_events.extend(batch_events)
        except Exception as e:
            print(f"[ERROR] LLM 聚类解析失败，尝试本地降级: {e}")
            fallback_results = cluster_articles(batch_full)
            for fr in fallback_results:
                all_events.append({
                    "title": fr['title'],
                    "article_ids": [a['id'] for a in fr['items']]
                })
            continue

    # 将识别出的事件与原始文章对象关联
    id_map = {int(a['id']): a for a in articles}
    results = []

    # === 批量翻译优化 ===
    # 收集所有需要翻译的标题
    all_titles = set()
    for e in all_events:
        # 翻译事件标题
        if e.get('title') and is_english_text(e['title']):
            all_titles.add(e['title'])
        # 收集文章标题
        event_ids = [int(aid) for aid in e.get('article_ids', [])]
        for aid in event_ids:
            if aid in id_map:
                art_title = id_map[aid].get('title', '')
                if art_title and is_english_text(art_title):
                    all_titles.add(art_title)

    # 执行批量翻译
    translation_map = batch_translate_to_chinese(list(all_titles))

    for e in all_events:
        # 确保来自 LLM 的 ID 被转为整数进行匹配
        event_ids = [int(aid) for aid in e.get('article_ids', [])]
        event_articles = [id_map[aid] for aid in event_ids if aid in id_map]

        if len(event_articles) < 2: continue # 过滤掉单条报道（根据用户要求：多处报道才算）

        # 基础评分逻辑
        platforms = set(i.get('platform') for i in event_articles)
        score = len(event_articles) * 3 + len(platforms) * 5

        china_bonus = 0
        event_title = translation_map.get(e['title'], e['title'])
        all_text = event_title + " " + " ".join([i['title'] for i in event_articles])
        if any(kw in all_text for kw in CHINA_KEYWORDS):
            china_bonus = 20

        # 提取关键词
        all_tokens = []
        for i in event_articles: all_tokens.extend(_tokenize(i['title']))
        common_tags = [t for t, count in Counter(all_tokens).most_common(5)]

        # 翻译文章标题
        translated_items = []
        for item in event_articles:
            translated_item = dict(item)
            orig_title = item.get('title', '')
            translated_item['title'] = translation_map.get(orig_title, orig_title)
            translated_items.append(translated_item)

        results.append({
            'title': event_title,  # 使用翻译后的标题
            'score': score + china_bonus,
            'count': len(event_articles),
            'platforms': list(platforms),
            'tags': common_tags,
            'is_china_related': china_bonus > 0,
            'items': translated_items  # 使用翻译后的文章列表
        })

    return sorted(results, key=lambda x: x['score'], reverse=True)

def generate_hot_events(period='day', provider=None):
    """获取指定周期的聚合热点"""
    now = datetime.now(TZ_BJ)
    if period == 'day':
        start = (now - timedelta(days=1)).isoformat()
    elif period == 'week':
        start = (now - timedelta(days=7)).isoformat()
    else:
        start = (now - timedelta(days=30)).isoformat()

    conn = get_conn()
    articles = conn.execute(
        "SELECT * FROM articles WHERE published >= ? ORDER BY published DESC",
        [start]
    ).fetchall()
    conn.close()

    # 将 Row 对象转为 dict，并验证每篇文章都有有效 ID
    article_dicts = []
    for a in articles:
        d = dict(a)
        # 关键修复：跳过没有有效 ID 的文章
        if d.get('id') is None or d.get('id') == '':
            print(f"[WARN] 跳过无 ID 文章: {d.get('title', '未知标题')[:50]}")
            continue
        article_dicts.append(d)

    print(f"[INFO] 热点分析: 从数据库获取 {len(article_dicts)} 篇有效文章")

    if period == 'day' and len(article_dicts) > 0:
        return llm_cluster_articles(article_dicts, provider=provider)
    else:
        return cluster_articles(article_dicts)

def build_analyst_prompt(selected_articles, custom_instruction=None):
    """构建新闻事件深度分析 Prompt"""

    # 1. 结构化上下文文章内容
    context_lines = []
    for idx, art in enumerate(selected_articles):
        # 优先使用 content (Jina 提供的全文), 否则使用 summary
        body = art.get('content') or art.get('summary') or "(内容暂不可用)"
        if len(body) > 1500: body = body[:1500] + "..."

        info = f"--- [素材 {idx+1}] ---\n"
        info += f"标题: {art['title']}\n"
        info += f"媒体: {art['platform']} ({art.get('media_group', 'N/A')})\n"
        info += f"时间: {art['published']}\n"
        info += f"链接: {art['url']}\n"
        info += f"正文: {body}\n"
        context_lines.append(info)

    articles_data = "\n".join(context_lines)

    system_prompt = """你是一位资深的国际情报分析师，拥有20年地缘政治、国际关系与战略研究经验。你的任务是根据提供的新闻素材，撰写一份专业的情报分析报告。

## 核心原则

新闻素材仅作为线索和参考，你需要：
- 以素材为切入点，结合你的专业知识进行深度分析
- 不要简单复述新闻，要提炼核心事实并延伸分析
- 对素材中没有但分析需要的关键信息，用占位符明确标注

---

## 报告结构（严格遵循）

### 一、标题
必须包含事件核心关键词，格式如：
- "XX事件发展态势及其对中国YY领域的影响分析"
- "关于XX问题的情报研判报告"

### 二、事实综述
开篇用200-300字陈述：
- 根据XX媒体、XX渠道获悉...
- 发生了什么事情（who, what, where, when）
- 当前进展状态
- 各方主要立场/反应

这一部分要客观、简洁，让读者快速掌握"发生了什么"。

### 三、事件时间线与细节分析

**必须按时间顺序分段叙述**，格式如下：

**【第一阶段】XXXX年XX月XX日 - XX月XX日**
- 发生了什么 [来源: 素材1]
- 关键细节...
- 各方反应...

**【第二阶段】...**

如果素材中的时间信息不完整，使用占位符：
- [待补充：XX时间节点前的背景信息]
- [待核实：具体时间/数据]

**多来源交叉验证**：同一事件有多家媒体报道时，注明各家说法的差异，如：
- 素材1称A，素材2称B，二者存在出入，需进一步核实

### 四、对中国的影响分析

分维度阐述，每个维度要有具体分析而非空泛表述：

**1. 政治外交层面**
- 具体影响是什么
- 涉及哪些国家/组织
- 对中国外交策略的启示

**2. 经济贸易层面**
- 涉及哪些产业/供应链
- 潜在的经济损失或机遇
- 对相关企业/行业的影响

**3. 安全层面**（如适用）
- 国家安全、能源安全、信息安全等
- 潜在风险点

**4. 舆论与社会层面**（如适用）
- 国内外舆论态势
- 对社会稳定的影响

如果某些层面的影响尚不明确，标注：
- [待评估：对中国XX领域的具体影响程度]

### 五、国际影响与连锁反应

- 对相关国家/地区的影响
- 对国际格局/地区局势的影响
- 可能引发的连锁反应
- 其他大国的可能应对

### 六、综合研判与对策建议

站在专家角度，提出：
1. **态势预判**：事件未来1-3个月可能的走向（列举2-3种情景）
2. **风险提示**：需要重点关注的风险点
3. **应对建议**：
   - 短期应对措施
   - 中长期战略调整建议
   - 信息收集建议（还需要补充哪些情报）

### 七、参考文献

按学术规范列出素材来源，格式：
1. [素材1] XX媒体，《文章标题》，发布时间，链接
2. [素材2] ...

---

## 写作规范

1. **语言风格**：专业、客观、严谨，避免情绪化表述
2. **引用标注**：引用素材中的具体内容时，使用 [素材X] 标注
3. **数据严谨**：引用数据要注明来源，没有数据不要编造
4. **占位符使用**：
   - [待补充：XXX] - 表示缺少该信息需要后续收集
   - [待核实：XXX] - 表示信息存在矛盾需要确认
   - [待评估：XXX] - 表示需要更深入的专业分析
5. **篇幅控制**：2000-3000字，重点突出，避免冗长

---

现在，请根据以上要求撰写情报分析报告。"""

    user_prompt = f"""以下是本次分析的新闻素材 (共 {len(selected_articles)} 篇)：

{articles_data}

{"额外关注点: " + custom_instruction if custom_instruction else ""}

请撰写专业的情报分析报告。"""

    return system_prompt, user_prompt

def write_intelligence_report(article_ids, custom_instruction=None, provider=None):
    """
    根据文章 ID 列表生成报告
    """
    # === 彻底修复：入口处第一时间过滤所有无效值 ===
    if article_ids is None:
        return "未选择任何文章。"

    # 如果传入的是单个值而非列表，转换为列表
    if not isinstance(article_ids, (list, tuple)):
        article_ids = [article_ids]

    # 第一步：剔除所有 None、空字符串、以及字符串化的 None
    article_ids = [
        aid for aid in article_ids
        if aid is not None
        and str(aid).strip() != ''
        and str(aid).strip().lower() not in ('none', 'null', 'undefined')
    ]

    if not article_ids:
        return "未选择任何有效文章。所选文章的 ID 均为无效值。"

    print(f"[INFO] 过滤后的有效 article_ids 数量: {len(article_ids)}")

    # 以下为原有的深度防御与格式兼容逻辑（已简化）
    # 1. 如果收到的不是列表而是字符串（由于某些 JSON 解析习惯），尝试切分
    if isinstance(article_ids, str):
        if article_ids.startswith('['):
            try: article_ids = json.loads(article_ids)
            except: article_ids = article_ids.strip('[]').split(',')
        else:
            article_ids = article_ids.split(',')

    # 2. 尝试转换为规范的整数列表
    valid_ids = []
    potential_hashes = []

    print(f"[DEBUG] 原始输入的 article_ids: {article_ids} (类型: {type(article_ids)})")
    
    for aid in article_ids:
        s_aid = str(aid).strip()
        if not s_aid: continue
        
        # 如果是纯数字，转为 int
        if s_aid.isdigit():
            valid_ids.append(int(s_aid))
        elif len(s_aid) >= 16: # 极有可能是 url_hash (MD5 32位或截断)
            potential_hashes.append(s_aid)
        else:
            # 最后的备选：尝试从字符串里提取数字
            match = re.search(r'\d+', s_aid)
            if match: valid_ids.append(int(match.group()))

    conn = get_conn()
    articles_found = []
    
    # 策略 A：按 ID 找
    if valid_ids:
        placeholders = ', '.join(['?'] * len(valid_ids))
        rows = conn.execute(
            f"SELECT * FROM articles WHERE id IN ({placeholders})", 
            valid_ids
        ).fetchall()
        articles_found.extend([dict(r) for r in rows])
        
    # 策略 B：如果 ID 没找齐，按 hash 补 (增加鲁棒性)
    if potential_hashes:
        placeholders = ', '.join(['?'] * len(potential_hashes))
        rows = conn.execute(
            f"SELECT * FROM articles WHERE url_hash IN ({placeholders})", 
            potential_hashes
        ).fetchall()
        articles_found.extend([dict(r) for r in rows])

    conn.close()
    
    # 去重 (通过 url_hash)
    seen = set()
    selected = []
    for a in articles_found:
        if a['url_hash'] not in seen:
            selected.append(a)
            seen.add(a['url_hash'])

    if not selected:
        print(f"[ERROR] 匹配失败：收到的所有输入 {article_ids} 均无法在数据库中找到对应记录。")
        return "未能识别所选文章。请尝试刷新页面并重新在左侧勾选素材后再点击撰写。"
    
    # 3. 核心改进：检查选中文章是否已有全文内容 ---
    # 逻辑：如果数据库已有内容（且长度 > 50），则直接复用，不再抓取。
    missing_content = [a for a in selected if not a.get('content') or len(a.get('content')) < 50]
    
    if missing_content:
        print(f"[INFO] 正在为 {len(missing_content)} 篇文章通过 Jina 获取全文原文...")
        
        def fetch_task(item):
            jina_url = f"https://r.jina.ai/{item['url']}"
            try:
                resp = requests.get(jina_url, headers={'Accept': 'application/json'}, timeout=20)
                if resp.status_code == 200:
                    data = resp.json().get('data', {}) if 'json' in resp.headers.get('Content-Type', '') else {}
                    raw_content = data.get('content') or data.get('text') or (resp.text if 'json' not in resp.headers.get('Content-Type', '') else '')
                    if raw_content and len(raw_content) > 50:
                        # 应用内容清洗，去除导航、广告等噪音
                        content = clean_jina_content(raw_content)
                        # 更新本地对象和数据库
                        item['content'] = content
                        update_article_content(item['id'], content)
                        return True
            except Exception as e:
                print(f"[WARN] 动态获取原文失败 ({item['id']}): {e}")
            return False

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_art = {executor.submit(fetch_task, art): art for art in missing_content}
            for future in as_completed(future_to_art):
                future.result() # 确保任务完成

    sys_p, user_p = build_analyst_prompt(selected, custom_instruction)
    
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": user_p}
    ]
    
    cfg = load_llm_config()
    if not cfg: return "错误：LLM 配置文件缺失或损坏。"
    
    # 如果手动指定了模型提供商
    if provider:        
        cfg.setdefault('llm', {})['provider'] = provider
    
    # 调用 LLM API (支持动态路由)
    content = _call_deepseek(messages, cfg)
    
    if not content:
        # FALLBACK: 安全救援模式 (当 AI 拒绝处理敏感内容时)
        print(f"[WARN] 报告撰写触发 AI 安全过滤或调用失败，正在生成本地结构化报告...")
        content = """# 深度分析报告 (本地安全回退模式)

> [!WARNING]
> **系统提醒**：由于选中的素材中包含 AI 审计敏感内容，深度智能分析已被拦截。系统已切换至本地结构化汇总模式。

## 1. 核心态势研判
根据目前收集到的素材，该事件涉及多方利益交叠。由于 AI 深度分析不可用，以下为您整理的事实清单。

## 2. 事实梳理与时间轴
"""
        # 本地提取摘要和时间线 (模拟研究员工作)
        for idx, art in enumerate(selected):
            sum_text = art.get('summary') or art.get('title')
            content += f"- **[{art['platform']}]** {art['title']}\n"
            content += f"  - 发布时间: {art['published']}\n"
            content += f"  - 核心摘要: {sum_text[:200]}...\n\n"
            
        content += """
## 3. 潜在关联影响
[建议手动评估] 由于 AI 实时评估模块被拦截，建议人工重点关注该事件对地缘安全及供应链的连锁反应。

---
"""

    # 追加素材链接附录（供读者直接访问原文）
    ref_list = "\n---\n\n### 附录：素材原文链接\n"
    for idx, art in enumerate(selected):
        ref_list += f"{idx+1}. [{art['platform']}] {art['title']}\n   链接: {art['url']}\n   时间: {art['published']}\n"

    return content + ref_list
