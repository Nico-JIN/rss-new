#!/usr/bin/env python3
"""
发布物质量验证模块

功能：
1. 源多样性检查（跨媒体、跨国家）
2. 事实覆盖度检查
3. 内容完整性检查
4. 引用质量检查
5. 综合质量评分

使用方式：
    from quality_checker import QualityChecker

    checker = QualityChecker()
    result = checker.check(publication)
    print(f"质量评分: {result['overall_score']}")
"""

from typing import List, Dict, Optional
from collections import Counter
from datetime import datetime, timezone, timedelta
import re

TZ_BJ = timezone(timedelta(hours=8))


class QualityChecker:
    """发布物质量检查器"""

    # 权重配置（提高引用质量权重）
    WEIGHTS = {
        'source_diversity': 0.25,      # 源多样性
        'fact_coverage': 0.20,         # 事实覆盖度
        'content_completeness': 0.20,  # 内容完整性
        'citation_quality': 0.35       # 引用质量（提高到35%）
    }

    # 媒体层级定义
    TIER1_MEDIA = {
        '路透社', 'reuters', '美联社', 'ap', '纽约时报', 'nyt',
        '华尔街日报', 'wsj', 'bbc', 'cnn', '彭博社', 'bloomberg',
        '金融时报', 'ft', '经济学人', 'economist'
    }

    TIER2_MEDIA = {
        '联合早报', 'zaobao', '南华早报', 'scmp', 'nhk',
        '半岛电视台', 'al jazeera', '德国之声', 'dw', '法国24', 'france24'
    }

    def __init__(self, config: Dict = None):
        """
        Args:
            config: 配置选项，可覆盖默认权重
        """
        self.config = config or {}
        self.weights = {**self.WEIGHTS, **self.config.get('weights', {})}

    def check(self, publication: Dict,
              articles: List[Dict] = None,
              hotspots: List[Dict] = None) -> Dict:
        """
        执行完整质量检查

        Args:
            publication: 发布物数据
            articles: 关联的文章列表
            hotspots: 关联的热点列表

        Returns:
            检查结果 {
                'overall_score': float,  # 综合评分 0-100
                'checks': {...},         # 各项检查结果
                'passed': bool,          # 是否通过（>=60分）
                'recommendations': [...]  # 改进建议
            }
        """
        articles = articles or []
        hotspots = hotspots or publication.get('source_hotspots', [])

        # 执行各项检查
        source_result = self.check_source_diversity(articles, hotspots)
        fact_result = self.check_fact_coverage(publication, articles)
        completeness_result = self.check_content_completeness(publication)
        citation_result = self.check_citation_quality(publication, articles)

        # 计算加权总分
        overall_score = (
            source_result['score'] * self.weights['source_diversity'] +
            fact_result['score'] * self.weights['fact_coverage'] +
            completeness_result['score'] * self.weights['content_completeness'] +
            citation_result['score'] * self.weights['citation_quality']
        )

        # 生成改进建议
        recommendations = self._generate_recommendations(
            source_result, fact_result, completeness_result, citation_result
        )

        return {
            'overall_score': round(overall_score, 1),
            'checks': {
                'source_diversity': source_result,
                'fact_coverage': fact_result,
                'content_completeness': completeness_result,
                'citation_quality': citation_result
            },
            'passed': overall_score >= 60,
            'recommendations': recommendations,
            'checked_at': datetime.now(TZ_BJ).isoformat()
        }

    def check_source_diversity(self, articles: List[Dict],
                                hotspots: List[Dict] = None) -> Dict:
        """
        检查源多样性

        维度：
        1. 媒体数量（至少3家）
        2. 国家分布（至少2个国家）
        3. 媒体层级（是否有Tier1媒体）
        """
        hotspots = hotspots or []

        # 收集所有来源
        sources = set()
        countries = set()
        has_tier1 = False
        has_tier2 = False

        for a in articles:
            platform = a.get('platform', '')
            media_group = a.get('media_group', '')
            country = a.get('country', '')

            if platform:
                sources.add(platform)
            if media_group:
                sources.add(media_group)
            if country:
                countries.add(country)

            # 检查媒体层级
            check_name = (platform + media_group).lower()
            if any(t in check_name for t in self.TIER1_MEDIA):
                has_tier1 = True
            if any(t in check_name for t in self.TIER2_MEDIA):
                has_tier2 = True

        for h in hotspots:
            # 处理两种情况：hotspots可能是整数(排名)或字典对象
            if isinstance(h, dict):
                for p in h.get('platforms', []):
                    sources.add(p)
            # 如果是整数，跳过（无法获取platforms信息）

        # 评分
        score = 0
        details = {
            'source_count': len(sources),
            'country_count': len(countries),
            'has_tier1_media': has_tier1,
            'has_tier2_media': has_tier2,
            'sources': list(sources)[:10],
            'countries': list(countries)[:5]
        }

        # 媒体数量评分（最高40分）
        if len(sources) >= 5:
            score += 40
        elif len(sources) >= 3:
            score += 30
        elif len(sources) >= 2:
            score += 20
        else:
            score += 10

        # 国家分布评分（最高30分）
        if len(countries) >= 3:
            score += 30
        elif len(countries) >= 2:
            score += 20
        else:
            score += 10

        # 媒体层级评分（最高30分）
        if has_tier1:
            score += 30
        elif has_tier2:
            score += 20
        else:
            score += 10

        return {
            'score': score,
            'max_score': 100,
            'details': details,
            'issues': self._identify_source_issues(details)
        }

    def check_fact_coverage(self, publication: Dict,
                            articles: List[Dict]) -> Dict:
        """
        检查事实覆盖度

        维度：
        1. 时间跨度（是否覆盖足够时间范围）
        2. 事件数量
        3. 观点多样性
        """
        content = publication.get('content_md', '')

        # 检查时间跨度
        time_refs = re.findall(r'\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2}', content)
        unique_dates = set(time_refs)

        # 检查事件覆盖
        event_keywords = re.findall(r'#\s+.+|##\s+.+', content)
        events_covered = len(event_keywords)

        # 检查观点词
        view_words = ['认为', '表示', '指出', '强调', '分析', '预测', '建议']
        view_count = sum(1 for w in view_words if w in content)

        # 文章数量
        article_count = len(articles)

        score = 0
        details = {
            'time_span': len(unique_dates),
            'events_covered': events_covered,
            'view_points': view_count,
            'article_count': article_count
        }

        # 时间跨度评分（最高25分）
        if len(unique_dates) >= 3:
            score += 25
        elif len(unique_dates) >= 2:
            score += 20
        elif len(unique_dates) >= 1:
            score += 15

        # 事件覆盖评分（最高25分）
        if events_covered >= 5:
            score += 25
        elif events_covered >= 3:
            score += 20
        elif events_covered >= 1:
            score += 15

        # 观点多样性评分（最高25分）
        if view_count >= 5:
            score += 25
        elif view_count >= 3:
            score += 20
        elif view_count >= 1:
            score += 15

        # 文章数量评分（最高25分）
        if article_count >= 10:
            score += 25
        elif article_count >= 5:
            score += 20
        elif article_count >= 3:
            score += 15

        return {
            'score': score,
            'max_score': 100,
            'details': details,
            'issues': [] if score >= 60 else ['事实覆盖度不足']
        }

    def check_content_completeness(self, publication: Dict) -> Dict:
        """
        检查内容完整性

        维度：
        1. 标题完整性
        2. 内容长度
        3. 章节结构
        4. 必要元素
        """
        title = publication.get('title', '')
        content = publication.get('content_md', '')

        score = 0
        issues = []
        details = {}

        # 标题检查（最高20分）
        if title and len(title) >= 5:
            score += 20
        else:
            issues.append('标题不完整')
            score += 5

        # 内容长度检查（最高30分）
        content_len = len(content)
        details['content_length'] = content_len
        if content_len >= 2000:
            score += 30
        elif content_len >= 1000:
            score += 25
        elif content_len >= 500:
            score += 15
        else:
            issues.append('内容过短')
            score += 5

        # 章节结构检查（最高30分）
        sections = re.findall(r'^#{1,3}\s+.+$', content, re.MULTILINE)
        details['section_count'] = len(sections)
        if len(sections) >= 5:
            score += 30
        elif len(sections) >= 3:
            score += 25
        elif len(sections) >= 2:
            score += 15
        else:
            issues.append('章节结构不完整')
            score += 5

        # 必要元素检查（最高20分）
        has_date = bool(re.search(r'\d{4}年|\d{4}-\d{2}', content))
        has_source = '来源' in content or 'Source' in content.lower()
        has_summary = '摘要' in content or '概览' in content or '回顾' in content

        details['has_date'] = has_date
        details['has_source'] = has_source
        details['has_summary'] = has_summary

        element_score = 0
        if has_date:
            element_score += 7
        if has_source:
            element_score += 7
        if has_summary:
            element_score += 6
        else:
            issues.append('缺少必要元素')
        score += element_score

        return {
            'score': score,
            'max_score': 100,
            'details': details,
            'issues': issues
        }

    def check_citation_quality(self, publication: Dict,
                               articles: List[Dict]) -> Dict:
        """
        检查引用质量（增强版）

        维度：
        1. 内联引用数量 [来源: ...]
        2. 脚注引用 [^...]
        3. 引用覆盖率（事实性句子中带引用的比例）
        4. 链接数量
        """
        content = publication.get('content_md', '')

        # 新增：统计内联引用 [来源: ...]
        inline_citations = re.findall(r'\[来源:\s*[^\]]+\]', content)

        # 新增：统计脚注引用 [^...]
        footnote_refs = re.findall(r'\[\^[^\]]+\]', content)

        # 检查链接
        links = re.findall(r'https?://[^\s\)]+', content)
        unique_links = set(links)

        # 检查来源标注
        source_refs = re.findall(r'\[([^\]]+)\]', content)
        unique_sources = set(s for s in source_refs if len(s) < 30)

        # 新增：计算引用覆盖率
        # 分割为句子，统计事实性句子（长度>15且非标题）
        sentences = re.split(r'[。\n]', content)
        factual_sentences = [s.strip() for s in sentences if len(s.strip()) > 15 and not s.strip().startswith('#')]
        cited_sentences = [s for s in factual_sentences if re.search(r'\[来源:|\[\^', s)]
        coverage_ratio = len(cited_sentences) / max(len(factual_sentences), 1) if factual_sentences else 0

        details = {
            'inline_citations': len(inline_citations),
            'footnote_refs': len(footnote_refs),
            'link_count': len(unique_links),
            'source_references': len(unique_sources),
            'citation_coverage': round(coverage_ratio * 100, 1),
            'total_sentences': len(factual_sentences),
            'cited_sentences': len(cited_sentences),
            'article_count': len(articles)
        }

        score = 0

        # 内联引用评分（最高25分）- 新增
        if len(inline_citations) >= 10:
            score += 25
        elif len(inline_citations) >= 5:
            score += 20
        elif len(inline_citations) >= 2:
            score += 15
        elif len(inline_citations) >= 1:
            score += 10

        # 引用覆盖率评分（最高30分）- 新增
        if coverage_ratio >= 0.7:
            score += 30
        elif coverage_ratio >= 0.5:
            score += 25
        elif coverage_ratio >= 0.3:
            score += 20
        elif coverage_ratio >= 0.1:
            score += 10

        # 链接数量评分（最高20分）
        if len(unique_links) >= 5:
            score += 20
        elif len(unique_links) >= 3:
            score += 15
        elif len(unique_links) >= 1:
            score += 10

        # 文章关联评分（最高25分）
        if len(articles) >= 10:
            score += 25
        elif len(articles) >= 5:
            score += 20
        elif len(articles) >= 3:
            score += 15

        # 生成问题列表
        issues = []
        if coverage_ratio < 0.3:
            issues.append(f"引用覆盖率过低 ({details['citation_coverage']}%)，建议每项事实陈述都附带来源")
        if len(inline_citations) < 3:
            issues.append(f"缺少内联引用 (仅{len(inline_citations)}个)，建议添加 [来源: 媒体名] 格式引用")
        if len(unique_links) < 3:
            issues.append(f"来源链接不足 (仅{len(unique_links)}个)，建议添加原文链接")

        return {
            'score': score,
            'max_score': 100,
            'details': details,
            'issues': issues
        }

    def _identify_source_issues(self, details: Dict) -> List[str]:
        """识别源多样性问题"""
        issues = []

        if details['source_count'] < 3:
            issues.append(f"媒体来源不足（仅{details['source_count']}家，建议至少3家）")

        if details['country_count'] < 2:
            issues.append(f"国家覆盖单一（仅{details['country_count']}国，建议多国视角）")

        if not details['has_tier1_media'] and not details['has_tier2_media']:
            issues.append("缺少权威媒体来源")

        return issues

    def _generate_recommendations(self, source_result: Dict,
                                   fact_result: Dict,
                                   completeness_result: Dict,
                                   citation_result: Dict) -> List[str]:
        """生成改进建议"""
        recommendations = []

        # 源多样性建议
        if source_result['score'] < 80:
            for issue in source_result.get('issues', []):
                recommendations.append(f"[源多样性] {issue}")

        # 事实覆盖建议
        if fact_result['score'] < 80:
            details = fact_result['details']
            if details['article_count'] < 5:
                recommendations.append(f"[建议] 增加更多参考文章（当前{details['article_count']}篇）")
            if details['events_covered'] < 3:
                recommendations.append("[建议] 增加事件分析章节")

        # 内容完整性建议
        if completeness_result['score'] < 80:
            for issue in completeness_result.get('issues', []):
                recommendations.append(f"[内容] {issue}")

        # 引用质量建议
        if citation_result['score'] < 80:
            details = citation_result['details']
            if details['link_count'] < 3:
                recommendations.append(f"[建议] 添加更多来源链接（当前{details['link_count']}个）")

        return recommendations

    def quick_check(self, publication: Dict) -> Dict:
        """
        快速质量检查（简化版）

        只检查最关键的指标
        """
        content = publication.get('content_md', '')
        title = publication.get('title', '')

        # 快速评分
        score = 0

        # 标题存在
        if title and len(title) >= 5:
            score += 20

        # 内容长度
        if len(content) >= 1000:
            score += 30
        elif len(content) >= 500:
            score += 20

        # 章节结构
        sections = content.count('\n## ')
        if sections >= 3:
            score += 30
        elif sections >= 2:
            score += 20

        # 链接
        links = content.count('http')
        if links >= 3:
            score += 20
        elif links >= 1:
            score += 10

        return {
            'score': min(score, 100),
            'passed': score >= 60,
            'quick_check': True
        }


# ═══════════════════════════════════════════════════════════════════
# CLI 测试入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    import json

    parser = argparse.ArgumentParser(description='发布物质量检查')
    parser.add_argument('--quick', action='store_true', help='快速检查')
    parser.add_argument('--demo', action='store_true', help='演示模式')

    args = parser.parse_args()

    checker = QualityChecker()

    if args.demo:
        # 演示模式：使用示例数据
        demo_publication = {
            'title': '2026年4月6日国际舆情简报',
            'content_md': '''
# 2026年4月6日国际舆情简报

**日期**: 2026年4月6日

---

## 头条新闻

### 1. 中美贸易摩擦升级

**来源**: 路透社, 彭博社, CNN
**热度**: 85.5

相关报道:
- [路透社] 中国宣布反制措施
  - 时间: 2026-04-06T10:00
- [彭博社] 美国考虑新的关税政策

## 中国相关动态

1. [路透社] 中国外交部回应...

## 数据来源

本简报聚合了 3 个信息源

[https://reuters.com](https://reuters.com)
[https://bloomberg.com](https://bloomberg.com)
'''
        }

        if args.quick:
            result = checker.quick_check(demo_publication)
        else:
            result = checker.check(demo_publication)

        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        print("使用 --demo 查看演示，或传入实际发布物数据")