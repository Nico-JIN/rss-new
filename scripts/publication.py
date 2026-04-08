#!/usr/bin/env python3
"""
发布物生命周期管理

功能：
1. 状态机管理 (draft → review → approved → published → archived)
2. 版本历史追踪
3. 发布物 CRUD 操作

使用方式：
    from publication import PublicationManager

    mgr = PublicationManager()
    pub_id = mgr.create("每日简报", "daily_digest")
    mgr.update_content(pub_id, content_md)
    mgr.transition_status(pub_id, "review", reviewer="张三")
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from pathlib import Path
import json

TZ_BJ = timezone(timedelta(hours=8))


class Publication:
    """发布物状态机"""

    # 状态流转定义
    STATUS_ORDER = ['draft', 'review', 'approved', 'published', 'archived']

    # 允许的状态流转
    VALID_TRANSITIONS = {
        'draft': ['review', 'archived'],
        'review': ['approved', 'draft', 'archived'],
        'approved': ['published', 'review', 'archived'],
        'published': ['archived'],
        'archived': []  # 终态，不可再流转
    }

    # 状态中文名
    STATUS_NAMES = {
        'draft': '草稿',
        'review': '审核中',
        'approved': '已批准',
        'published': '已发布',
        'archived': '已归档'
    }

    # 发布类型
    PUB_TYPES = {
        'daily_digest': '每日简报',
        'weekly_report': '每周研判',
        'special_issue': '专题报告'
    }

    @classmethod
    def can_transition(cls, current_status: str, target_status: str) -> bool:
        """检查状态流转是否合法"""
        if current_status not in cls.VALID_TRANSITIONS:
            return False
        return target_status in cls.VALID_TRANSITIONS[current_status]

    @classmethod
    def get_next_statuses(cls, current_status: str) -> List[str]:
        """获取当前状态可流转的目标状态"""
        return cls.VALID_TRANSITIONS.get(current_status, [])


class PublicationManager:
    """发布物管理器"""

    def __init__(self, conn=None):
        """
        Args:
            conn: 数据库连接，若为 None 则自动获取
        """
        self._own_conn = conn is None
        self._conn = conn

        if self._own_conn:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from store import get_conn, init_db
            self._conn = get_conn()
            init_db(self._conn)

    def _get_conn(self):
        """获取数据库连接"""
        return self._conn

    def close(self):
        """关闭数据库连接（仅当自动创建时有效）"""
        if self._own_conn and self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD 操作 ──────────────────────────────────────────────

    def create(self, title: str, pub_type: str,
               template_id: str = '',
               source_hotspots: List[int] = None,
               source_articles: List[int] = None,
               author: str = 'system') -> int:
        """
        创建发布物

        Args:
            title: 发布物标题
            pub_type: 发布类型 (daily_digest/weekly_report/special_issue)
            template_id: 使用的模板ID
            source_hotspots: 关联的热点事件ID列表
            source_articles: 关联的文章ID列表
            author: 作者

        Returns:
            发布物ID
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import create_publication

        return create_publication(
            title=title,
            pub_type=pub_type,
            template_id=template_id,
            source_hotspots=source_hotspots or [],
            source_articles=source_articles or [],
            author=author,
            conn=self._get_conn()
        )

    def get(self, pub_id: int) -> Optional[Dict]:
        """获取发布物详情"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import get_publication

        pub = get_publication(pub_id, conn=self._get_conn())
        if pub:
            pub['status_name'] = Publication.STATUS_NAMES.get(pub['status'], pub['status'])
            pub['pub_type_name'] = Publication.PUB_TYPES.get(pub['pub_type'], pub['pub_type'])
        return pub

    def list(self, status: str = None, pub_type: str = None,
             limit: int = 50) -> List[Dict]:
        """获取发布物列表"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import list_publications

        pubs = list_publications(
            status=status,
            pub_type=pub_type,
            limit=limit,
            conn=self._get_conn()
        )
        for pub in pubs:
            pub['status_name'] = Publication.STATUS_NAMES.get(pub['status'], pub['status'])
            pub['pub_type_name'] = Publication.PUB_TYPES.get(pub['pub_type'], pub['pub_type'])
        return pubs

    def delete(self, pub_id: int) -> bool:
        """删除发布物"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import delete_publication

        delete_publication(pub_id, conn=self._get_conn())
        return True

    # ── 内容操作 ──────────────────────────────────────────────

    def update_content(self, pub_id: int, content_md: str,
                       content_html: str = '') -> bool:
        """
        更新发布物内容

        Args:
            pub_id: 发布物ID
            content_md: Markdown 内容
            content_html: HTML 内容（可选）
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import update_publication_content

        update_publication_content(
            pub_id=pub_id,
            content_md=content_md,
            content_html=content_html,
            conn=self._get_conn()
        )
        return True

    def update_quality(self, pub_id: int, quality_score: float,
                       quality_checks: dict) -> bool:
        """
        更新发布物质量评分

        Args:
            pub_id: 发布物ID
            quality_score: 综合质量分 (0-100)
            quality_checks: 各项检查结果
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import update_publication_quality

        update_publication_quality(
            pub_id=pub_id,
            quality_score=quality_score,
            quality_checks=quality_checks,
            conn=self._get_conn()
        )
        return True

    # ── 状态流转 ──────────────────────────────────────────────

    def transition_status(self, pub_id: int, target_status: str,
                          reviewer: str = '',
                          change_note: str = '') -> Dict:
        """
        状态流转

        Args:
            pub_id: 发布物ID
            target_status: 目标状态
            reviewer: 审核人
            change_note: 变更说明

        Returns:
            操作结果 {'success': bool, 'message': str}
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import (update_publication_status, get_publication,
                           create_publication_history)

        # 获取当前状态
        pub = get_publication(pub_id, conn=self._get_conn())
        if not pub:
            return {'success': False, 'message': '发布物不存在'}

        current_status = pub['status']

        # 检查流转合法性
        if not Publication.can_transition(current_status, target_status):
            return {
                'success': False,
                'message': f"不允许从 '{Publication.STATUS_NAMES.get(current_status)}' 流转到 '{Publication.STATUS_NAMES.get(target_status)}'"
            }

        # 保存版本历史（状态变更时）
        create_publication_history(
            pub_id=pub_id,
            version=pub['version'],
            content_md=pub['content_md'],
            status=target_status,
            changed_by=reviewer,
            change_note=change_note,
            conn=self._get_conn()
        )

        # 更新状态
        update_publication_status(
            pub_id=pub_id,
            new_status=target_status,
            reviewer=reviewer,
            conn=self._get_conn()
        )

        return {
            'success': True,
            'message': f"状态已更新为 '{Publication.STATUS_NAMES.get(target_status)}'"
        }

    def submit_for_review(self, pub_id: int, reviewer: str = '') -> Dict:
        """提交审核 (draft → review)"""
        return self.transition_status(pub_id, 'review', reviewer, '提交审核')

    def approve(self, pub_id: int, reviewer: str = '') -> Dict:
        """批准发布 (review → approved)"""
        return self.transition_status(pub_id, 'approved', reviewer, '批准发布')

    def reject(self, pub_id: int, reviewer: str = '', reason: str = '') -> Dict:
        """驳回修改 (review → draft)"""
        return self.transition_status(pub_id, 'draft', reviewer, f'驳回修改: {reason}')

    def publish(self, pub_id: int, reviewer: str = '') -> Dict:
        """正式发布 (approved → published)"""
        return self.transition_status(pub_id, 'published', reviewer, '正式发布')

    def archive(self, pub_id: int, reviewer: str = '') -> Dict:
        """归档 (任意状态 → archived)"""
        pub = self.get(pub_id)
        if not pub:
            return {'success': False, 'message': '发布物不存在'}
        return self.transition_status(pub_id, 'archived', reviewer, '归档')

    # ── 版本历史 ──────────────────────────────────────────────

    def get_history(self, pub_id: int) -> List[Dict]:
        """获取发布物版本历史"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import get_publication_history

        history = get_publication_history(pub_id, conn=self._get_conn())
        for h in history:
            h['status_name'] = Publication.STATUS_NAMES.get(h['status'], h['status'])
        return history

    def rollback(self, pub_id: int, target_version: int,
                 reviewer: str = '') -> Dict:
        """
        回滚到指定版本

        Args:
            pub_id: 发布物ID
            target_version: 目标版本号
            reviewer: 操作人
        """
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from store import get_publication_history, update_publication_content

        # 获取目标版本内容
        history = get_publication_history(pub_id, conn=self._get_conn())
        target_entry = None
        for h in history:
            if h['version'] == target_version:
                target_entry = h
                break

        if not target_entry:
            return {'success': False, 'message': f'版本 {target_version} 不存在'}

        # 恢复内容
        update_publication_content(
            pub_id=pub_id,
            content_md=target_entry['content_md'],
            conn=self._get_conn()
        )

        return {
            'success': True,
            'message': f'已回滚到版本 {target_version}'
        }

    # ── 批量操作 ──────────────────────────────────────────────

    def batch_get_by_status(self, status: str) -> List[Dict]:
        """按状态批量获取发布物"""
        return self.list(status=status)

    def get_drafts(self) -> List[Dict]:
        """获取所有草稿"""
        return self.list(status='draft')

    def get_pending_reviews(self) -> List[Dict]:
        """获取待审核的发布物"""
        return self.list(status='review')

    def get_published(self, limit: int = 20) -> List[Dict]:
        """获取已发布的发布物"""
        return self.list(status='published', limit=limit)


# ═══════════════════════════════════════════════════════════════════
# CLI 测试入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='发布物管理')
    parser.add_argument('--action', choices=['list', 'get', 'create', 'status'],
                        default='list', help='操作类型')
    parser.add_argument('--id', type=int, help='发布物ID')
    parser.add_argument('--status', type=str, help='状态过滤')
    parser.add_argument('--type', type=str, help='类型过滤')

    args = parser.parse_args()

    mgr = PublicationManager()

    try:
        if args.action == 'list':
            pubs = mgr.list(status=args.status, pub_type=args.type)
            print(f"\n共 {len(pubs)} 条发布物:")
            for p in pubs:
                print(f"  [{p['id']}] {p['title']} - {p['status_name']} ({p['pub_type_name']})")

        elif args.action == 'get':
            if not args.id:
                print("请指定 --id")
            else:
                pub = mgr.get(args.id)
                if pub:
                    print(json.dumps(pub, ensure_ascii=False, indent=2))
                else:
                    print("发布物不存在")

        elif args.action == 'create':
            pub_id = mgr.create("测试发布物", "daily_digest")
            print(f"创建成功，ID: {pub_id}")

        elif args.action == 'status':
            if not args.id:
                print("请指定 --id")
            else:
                pub = mgr.get(args.id)
                if pub:
                    print(f"当前状态: {pub['status_name']}")
                    next_statuses = Publication.get_next_statuses(pub['status'])
                    print(f"可流转状态: {[Publication.STATUS_NAMES.get(s, s) for s in next_statuses]}")

    finally:
        mgr.close()