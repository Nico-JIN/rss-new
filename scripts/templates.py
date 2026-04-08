#!/usr/bin/env python3
"""
发布模板系统

功能：
1. 支持多种发布类型的模板
2. 模板渲染（注入数据生成内容）
3. 章节组合与样式配置

使用方式：
    from templates import TemplateRenderer

    renderer = TemplateRenderer()
    content = renderer.render('daily_digest', articles, hotspots, context)
"""

from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from pathlib import Path
import json

TZ_BJ = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════════════════════════
# 模板定义
# ═══════════════════════════════════════════════════════════════════

DEFAULT_TEMPLATES = {
    'daily_digest': {
        'name': '每日国际舆情简报',
        'pub_type': 'daily_digest',
        'sections': [
            {
                'id': 'header',
                'title': '简报概览',
                'type': 'header',
                'required': True
            },
            {
                'id': 'top_news',
                'title': '头条新闻',
                'type': 'hotspot_list',
                'max_items': 3,
                'min_score': 50
            },
            {
                'id': 'china_related',
                'title': '中国相关动态',
                'type': 'article_list',
                'filter': {'is_china_related': True},
                'max_items': 5
            },
            {
                'id': 'international',
                'title': '国际热点',
                'type': 'article_list',
                'filter': {'is_china_related': False},
                'max_items': 5
            },
            {
                'id': 'footer',
                'title': '数据来源',
                'type': 'footer',
                'required': True
            }
        ],
        'style_config': {
            'show_score': True,
            'show_source': True,
            'show_time': True,
            'max_title_length': 60,
            'include_summary': True
        }
    },

    'weekly_report': {
        'name': '每周深度研判报告',
        'pub_type': 'weekly_report',
        'sections': [
            {
                'id': 'header',
                'title': '本周概览',
                'type': 'header',
                'required': True
            },
            {
                'id': 'weekly_summary',
                'title': '本周回顾',
                'type': 'summary',
                'require_llm': True
            },
            {
                'id': 'key_events',
                'title': '重点事件分析',
                'type': 'hotspot_analysis',
                'max_items': 5,
                'require_llm': True
            },
            {
                'id': 'trend_analysis',
                'title': '趋势研判',
                'type': 'llm_analysis',
                'require_llm': True
            },
            {
                'id': 'next_week',
                'title': '下周展望',
                'type': 'llm_analysis',
                'require_llm': True
            },
            {
                'id': 'footer',
                'title': '数据来源',
                'type': 'footer',
                'required': True
            }
        ],
        'style_config': {
            'show_score': True,
            'show_source': True,
            'detailed_analysis': True,
            'include_recommendations': True
        }
    },

    'special_issue': {
        'name': '专题报告',
        'pub_type': 'special_issue',
        'sections': [
            {
                'id': 'header',
                'title': '专题报告',
                'type': 'header',
                'required': True
            },
            {
                'id': 'event_overview',
                'title': '事件综述',
                'type': 'llm_analysis',
                'require_llm': True
            },
            {
                'id': 'timeline',
                'title': '事件时间线',
                'type': 'timeline',
                'require_llm': True
            },
            {
                'id': 'impact_analysis',
                'title': '影响分析',
                'type': 'llm_analysis',
                'require_llm': True
            },
            {
                'id': 'expert_view',
                'title': '专家研判',
                'type': 'llm_analysis',
                'require_llm': True
            },
            {
                'id': 'recommendations',
                'title': '对策建议',
                'type': 'llm_analysis',
                'require_llm': True
            },
            {
                'id': 'footer',
                'title': '参考文献',
                'type': 'references',
                'required': True
            }
        ],
        'style_config': {
            'show_score': True,
            'show_source': True,
            'detailed_analysis': True,
            'include_recommendations': True,
            'expert_perspective': True
        }
    }
}


# ═══════════════════════════════════════════════════════════════════
# 模板渲染器
# ═══════════════════════════════════════════════════════════════════

class TemplateRenderer:
    """模板渲染器"""

    def __init__(self):
        self.templates = DEFAULT_TEMPLATES.copy()
        self._load_custom_templates()

    def _load_custom_templates(self):
        """从数据库加载自定义模板"""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from store import list_publication_templates

            custom_templates = list_publication_templates()
            for tmpl in custom_templates:
                self.templates[tmpl['id']] = {
                    'name': tmpl['name'],
                    'pub_type': tmpl['pub_type'],
                    'sections': tmpl['sections'],
                    'style_config': tmpl['style_config']
                }
        except Exception as e:
            print(f"[WARN] 加载自定义模板失败: {e}")

    def get_template(self, template_id: str) -> Optional[Dict]:
        """获取模板定义"""
        return self.templates.get(template_id)

    def list_templates(self, pub_type: str = None) -> List[Dict]:
        """列出可用模板"""
        templates = []
        for tid, tmpl in self.templates.items():
            if pub_type and tmpl['pub_type'] != pub_type:
                continue
            templates.append({
                'id': tid,
                'name': tmpl['name'],
                'pub_type': tmpl['pub_type'],
                'section_count': len(tmpl['sections'])
            })
        return templates

    def render(self, template_id: str,
               articles: List[Dict] = None,
               hotspots: List[Dict] = None,
               timeline_events: List[Dict] = None,
               context: Dict = None,
               llm_analysis: str = None) -> str:
        """
        渲染模板生成 Markdown 内容

        Args:
            template_id: 模板ID
            articles: 文章列表
            hotspots: 热点事件列表
            timeline_events: 时间线事件列表
            context: 上下文信息 (date, title, etc.)
            llm_analysis: LLM 生成的分析内容

        Returns:
            Markdown 格式的发布内容
        """
        template = self.get_template(template_id)
        if not template:
            return f"# 错误：模板 '{template_id}' 不存在"

        context = context or {}
        articles = articles or []
        hotspots = hotspots or []
        timeline_events = timeline_events or []

        # 设置默认上下文
        if 'date' not in context:
            context['date'] = datetime.now(TZ_BJ).strftime('%Y年%m月%d日')
        if 'title' not in context:
            context['title'] = template['name']

        style = template.get('style_config', {})

        # 渲染各章节
        sections_md = []

        for section in template['sections']:
            section_md = self._render_section(
                section=section,
                articles=articles,
                hotspots=hotspots,
                timeline_events=timeline_events,
                context=context,
                style=style,
                llm_analysis=llm_analysis
            )
            if section_md:
                sections_md.append(section_md)

        return '\n\n'.join(sections_md)

    def _render_section(self, section: Dict, articles: List[Dict],
                        hotspots: List[Dict], timeline_events: List[Dict],
                        context: Dict, style: Dict,
                        llm_analysis: str = None) -> str:
        """渲染单个章节"""
        section_type = section.get('type', 'text')
        title = section.get('title', '')

        if section_type == 'header':
            return self._render_header(context)

        elif section_type == 'hotspot_list':
            return self._render_hotspot_list(
                title=title,
                hotspots=hotspots,
                max_items=section.get('max_items', 5),
                min_score=section.get('min_score', 0),
                style=style
            )

        elif section_type == 'article_list':
            return self._render_article_list(
                title=title,
                articles=articles,
                filter_cfg=section.get('filter', {}),
                max_items=section.get('max_items', 10),
                style=style
            )

        elif section_type == 'timeline':
            return self._render_timeline(
                title=title,
                events=timeline_events,
                style=style
            )

        elif section_type == 'llm_analysis':
            return self._render_llm_analysis(
                title=title,
                content=llm_analysis
            )

        elif section_type == 'hotspot_analysis':
            return self._render_hotspot_analysis(
                title=title,
                hotspots=hotspots,
                max_items=section.get('max_items', 5),
                style=style
            )

        elif section_type == 'summary':
            return self._render_summary(
                title=title,
                hotspots=hotspots,
                articles=articles,
                content=llm_analysis
            )

        elif section_type == 'footer':
            return self._render_footer(articles, hotspots)

        elif section_type == 'references':
            return self._render_references(articles, hotspots)

        else:
            return f"## {title}\n\n（待补充内容）"

    def _render_header(self, context: Dict) -> str:
        """渲染头部"""
        lines = [
            f"# {context.get('title', '新闻简报')}",
            "",
            f"**日期**: {context.get('date', datetime.now(TZ_BJ).strftime('%Y年%m月%d日'))}",
            f"**生成时间**: {datetime.now(TZ_BJ).strftime('%Y-%m-%d %H:%M')}",
            "",
            "---"
        ]
        return '\n'.join(lines)

    def _render_hotspot_list(self, title: str, hotspots: List[Dict],
                              max_items: int, min_score: float,
                              style: Dict) -> str:
        """渲染热点列表（带引用链接）"""
        lines = [f"## {title}", ""]

        # 过滤低分热点
        filtered = [h for h in hotspots if h.get('score', 0) >= min_score]
        filtered = sorted(filtered, key=lambda x: x.get('score', 0), reverse=True)
        filtered = filtered[:max_items]

        if not filtered:
            lines.append("*暂无热点事件*")
            return '\n'.join(lines)

        citation_counter = 0  # 全局引用计数器

        for i, h in enumerate(filtered, 1):
            score_str = f" (热度: {h.get('score', 0)})" if style.get('show_score') else ""
            china_tag = " 🇨🇳" if h.get('is_china_related') else ""

            lines.append(f"### {i}. {h.get('title', '未知事件')}{china_tag}{score_str}")
            lines.append("")

            if style.get('show_source'):
                platforms = h.get('platforms', [])[:3]
                lines.append(f"**来源**: {', '.join(platforms)}")

            lines.append(f"**媒体数**: {h.get('media_count', 0)} | **文章数**: {h.get('article_count', 0)}")
            lines.append("")

            # 文章详情（带引用链接）
            items = h.get('items') or h.get('articles') or []
            if items:
                lines.append("**相关报道**:")
                for idx, a in enumerate(items[:5], 1):  # 增加到 5 篇
                    platform = a.get('platform', '')
                    title_text = a.get('title', '')[:50]
                    url = a.get('url', '')
                    pub_time = a.get('published', '')[:16] if a.get('published') else ''

                    # 新增：创建带引用链接的格式
                    citation_counter += 1
                    if url:
                        lines.append(f"- [{platform}] {title_text} [来源: [{platform}]({url})][^{citation_counter}]")
                    else:
                        lines.append(f"- [{platform}] {title_text}")

                    if style.get('show_time') and pub_time:
                        lines.append(f"  - 时间: {pub_time}")
                lines.append("")

        return '\n'.join(lines)

    def _render_article_list(self, title: str, articles: List[Dict],
                              filter_cfg: Dict, max_items: int,
                              style: Dict) -> str:
        """渲染文章列表（带引用链接）"""
        lines = [f"## {title}", ""]

        # 应用过滤
        filtered = articles
        if filter_cfg.get('is_china_related') is not None:
            filtered = [a for a in filtered if a.get('is_china_related') == filter_cfg['is_china_related']]

        filtered = filtered[:max_items]

        if not filtered:
            lines.append("*暂无相关新闻*")
            return '\n'.join(lines)

        for i, a in enumerate(filtered, 1):
            platform = a.get('platform', '')
            title_text = a.get('title', '')[:style.get('max_title_length', 60)]
            url = a.get('url', '')

            # 新增：添加引用链接
            if url:
                lines.append(f"{i}. [{platform}] [{title_text}]({url}) [来源: {platform}]")
            else:
                lines.append(f"{i}. [{platform}] {title_text}")

            if style.get('show_time') and a.get('published'):
                lines.append(f"   时间: {a.get('published', '')[:16]}")

            if style.get('include_summary') and a.get('summary'):
                summary = a.get('summary', '')[:100]
                lines.append(f"   摘要: {summary}...")

        return '\n'.join(lines)

    def _render_timeline(self, title: str, events: List[Dict],
                         style: Dict) -> str:
        """渲染时间线"""
        lines = [f"## {title}", ""]

        if not events:
            lines.append("*暂无时间线数据*")
            return '\n'.join(lines)

        lines.append("```")
        for e in events:
            time_str = e.get('event_time', '')[:16]
            lines.append(f"[{time_str}] {e.get('title', '')}")
        lines.append("```")

        return '\n'.join(lines)

    def _render_llm_analysis(self, title: str, content: str) -> str:
        """渲染 LLM 分析内容"""
        lines = [f"## {title}", ""]

        if content:
            lines.append(content)
        else:
            lines.append("*（待 AI 分析生成）*")

        return '\n'.join(lines)

    def _render_hotspot_analysis(self, title: str, hotspots: List[Dict],
                                  max_items: int, style: Dict) -> str:
        """渲染热点分析（简化版，完整版需 LLM）"""
        lines = [f"## {title}", ""]

        for h in hotspots[:max_items]:
            lines.append(f"### {h.get('title', '')}")
            lines.append("")
            lines.append(f"- **热度评分**: {h.get('score', 0)}")
            lines.append(f"- **媒体报道**: {h.get('media_count', 0)} 家")
            lines.append(f"- **文章数量**: {h.get('article_count', 0)} 篇")
            lines.append("")

        return '\n'.join(lines)

    def _render_summary(self, title: str, hotspots: List[Dict],
                        articles: List[Dict], content: str = None) -> str:
        """渲染综述"""
        lines = [f"## {title}", ""]

        # 基础统计
        china_count = sum(1 for h in hotspots if h.get('is_china_related'))
        total_media = sum(h.get('media_count', 0) for h in hotspots)

        lines.append(f"**本周热点**: {len(hotspots)} 个")
        lines.append(f"**中国相关**: {china_count} 个")
        lines.append(f"**涉及媒体**: {total_media} 家")
        lines.append("")

        if content:
            lines.append(content)

        return '\n'.join(lines)

    def _render_footer(self, articles: List[Dict], hotspots: List[Dict]) -> str:
        """渲染页脚"""
        # 统计来源
        sources = set()
        for h in hotspots:
            sources.update(h.get('platforms', []))
        for a in articles:
            if a.get('platform'):
                sources.add(a.get('platform'))

        lines = [
            "---",
            "",
            "## 数据来源",
            "",
            f"本简报聚合了 {len(sources)} 个信息源的数据：",
            ""
        ]

        # 按来源分组
        for source in sorted(sources)[:10]:
            lines.append(f"- {source}")

        if len(sources) > 10:
            lines.append(f"- ...及其他 {len(sources) - 10} 个来源")

        lines.extend([
            "",
            f"**生成时间**: {datetime.now(TZ_BJ).strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "*本简报由 AI 自动生成，仅供参考。*"
        ])

        return '\n'.join(lines)

    def _render_references(self, articles: List[Dict], hotspots: List[Dict]) -> str:
        """渲染参考文献（带脚注格式）"""
        lines = ["## 参考文献", ""]
        lines.append("以下是本简报引用的所有来源，点击可跳转至原文：")
        lines.append("")

        ref_num = 1
        seen_urls = set()  # 避免重复引用

        # 从热点收集文章
        for h in hotspots[:10]:
            items = h.get('items') or h.get('articles') or []
            for a in items[:5]:
                url = a.get('url', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    platform = a.get('platform', '')
                    title = a.get('title', '')[:60]
                    pub_time = a.get('published', '')[:10] if a.get('published') else ''

                    # 脚注格式
                    lines.append(f"[^{ref_num}]: [{platform}] {title}")
                    lines.append(f"    链接: {url}")
                    if pub_time:
                        lines.append(f"    时间: {pub_time}")
                    lines.append("")
                    ref_num += 1

        # 从单独文章收集
        for a in articles[:10]:
            url = a.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                platform = a.get('platform', '')
                title = a.get('title', '')[:60]
                pub_time = a.get('published', '')[:10] if a.get('published') else ''

                lines.append(f"[^{ref_num}]: [{platform}] {title}")
                lines.append(f"    链接: {url}")
                if pub_time:
                    lines.append(f"    时间: {pub_time}")
                lines.append("")
                ref_num += 1

        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════════
# 模板管理
# ═══════════════════════════════════════════════════════════════════

def init_default_templates(conn=None):
    """初始化默认模板到数据库"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from store import create_publication_template

    for tmpl_id, tmpl_data in DEFAULT_TEMPLATES.items():
        create_publication_template(
            template_id=tmpl_id,
            name=tmpl_data['name'],
            pub_type=tmpl_data['pub_type'],
            sections=tmpl_data['sections'],
            style_config=tmpl_data['style_config'],
            conn=conn
        )
        print(f"[INFO] 已初始化模板: {tmpl_data['name']}")


# ═══════════════════════════════════════════════════════════════════
# CLI 测试入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='发布模板系统')
    parser.add_argument('--action', choices=['list', 'render', 'init'],
                        default='list', help='操作类型')
    parser.add_argument('--template', type=str, default='daily_digest',
                        help='模板ID')
    parser.add_argument('--init', action='store_true',
                        help='初始化默认模板到数据库')

    args = parser.parse_args()

    renderer = TemplateRenderer()

    if args.action == 'list':
        templates = renderer.list_templates()
        print("\n可用模板:")
        for t in templates:
            print(f"  - {t['id']}: {t['name']} ({t['section_count']} 章节)")

    elif args.action == 'render':
        # 测试渲染
        test_hotspots = [
            {
                'title': '中美贸易摩擦升级',
                'score': 85.5,
                'media_count': 5,
                'article_count': 12,
                'is_china_related': True,
                'platforms': ['路透社', '彭博社', 'CNN'],
                'articles': [
                    {'title': '中国宣布反制措施', 'platform': '路透社', 'published': '2026-04-06T10:00'}
                ]
            }
        ]

        content = renderer.render(
            template_id=args.template,
            hotspots=test_hotspots,
            context={'title': '测试简报'}
        )
        print(content)

    elif args.action == 'init' or args.init:
        init_default_templates()