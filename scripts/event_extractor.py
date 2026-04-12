#!/usr/bin/env python3
"""
事件结构化提取模块 — 使用 LLM 批量提取 event_key 等字段

职责：
  1. 接收未分析的新闻列表
  2. 批量调用 LLM 提取：primary_country, event_key, related_countries, entities
  3. 将结果写入数据库

设计原则：
  - 所有异常静默降级，不阻断主流程
  - 批量打包请求，节省 Token
  - 支持多模型切换（豆包/Ollama/DeepSeek/Gemini等）
  - 使用 ThreadPoolExecutor 真正并发执行
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from llm_tagger import _call_llm_api, load_llm_config

TZ_BJ = timezone(timedelta(hours=8))
BASE = Path(__file__).parent.parent

# ═══════════════════════════════════════════════════════════════════════════════
# 软新闻过滤配置
# ═══════════════════════════════════════════════════════════════════════════════

_filter_config_cache = None

def load_filter_config() -> dict:
    """加载软新闻过滤配置"""
    global _filter_config_cache
    if _filter_config_cache is not None:
        return _filter_config_cache

    config_path = BASE / "config" / "filter_keywords.yaml"
    if not config_path.exists():
        _filter_config_cache = {"blacklist": {}, "whitelist": {}}
        return _filter_config_cache

    try:
        _filter_config_cache = yaml.safe_load(config_path.read_text("utf-8")) or {}
        return _filter_config_cache
    except Exception:
        _filter_config_cache = {"blacklist": {}, "whitelist": {}}
        return _filter_config_cache


def get_blacklist_keywords() -> set:
    """获取所有黑名单关键词"""
    cfg = load_filter_config()
    blacklist = cfg.get("blacklist", {})
    keywords = set()
    for category, words in blacklist.items():
        if isinstance(words, list):
            keywords.update(w.lower() for w in words)
    return keywords


def get_whitelist_keywords() -> set:
    """获取所有白名单关键词"""
    cfg = load_filter_config()
    whitelist = cfg.get("whitelist", {})
    keywords = set()
    for category, words in whitelist.items():
        if isinstance(words, list):
            keywords.update(w.lower() for w in words)
    return keywords


def is_soft_news(title: str) -> bool:
    """
    判断是否为软新闻（体育、娱乐、民生等）

    Args:
        title: 文章标题

    Returns:
        True 表示是软新闻（应过滤），False 表示可能不是
    """
    if not title:
        return False

    title_lower = title.lower()

    blacklist = get_blacklist_keywords()
    whitelist = get_whitelist_keywords()

    # 检查白名单优先（政治人物/事件即使含体育词汇也保留）
    for kw in whitelist:
        if kw in title_lower:
            return False  # 白名单命中，保留

    # 检查黑名单
    for kw in blacklist:
        if kw in title_lower:
            return True  # 黑名单命中，过滤

    return False  # 未命中任何名单，保留（保守策略）


# ═══════════════════════════════════════════════════════════════════════════════
# 配置 - 优化参数（避免 API 限流）
# ═══════════════════════════════════════════════════════════════════════════════

BATCH_SIZE = 50        # 每批处理文章数
MAX_WORKERS = 1        # 最大并发线程数
RETRY_DELAY = 1        # 重试间隔（秒），快速切换模型


# ═══════════════════════════════════════════════════════════════════════════════
# LLM Prompt
# ═══════════════════════════════════════════════════════════════════════════════

# 安全版 Prompt - 优化版，适配小模型
EXTRACTION_PROMPT = """分析新闻标题，提取结构化信息。

任务：对每条新闻提取以下字段：
- id: 保持原值
- country: 主要涉及的国家的ISO代码（US/CN/JP/RU/GB/IL/UA/IR等，国际事件用INTL）
- event: 事件摘要，必须包含关键主体，格式"主体+事件"（如"美伊谈判失败"、"特朗普访华"、"美联储加息"），软新闻填"软新闻"
- tags: 关键词列表，必须包含主要人物/组织/国家（最多5个）
- political: 是否属于政治情报类新闻

【event 字段规则 - 重要！】

必须包含主体，让人一看就知道是谁的事：
✓ "美伊谈判失败" - 有主体(美伊)，有事件(谈判失败)
✓ "特朗普宣布制裁" - 有主体(特朗普)，有事件(宣布制裁)
✓ "日本首相访华" - 有主体(日本首相)，有事件(访华)
✓ "美联储加息25基点" - 有主体(美联储)，有事件(加息)

✗ "外交谈判失败" - 缺主体，不知道谁和谁
✗ "谈判未达成协议" - 缺主体
✗ "访问中国" - 缺主体

【tags 字段规则 - 重要！】

必须提取主要实体，按重要性排序：
1. 主要人物：特朗普、拜登、习近平、岸田文雄等
2. 主要国家：美国、中国、日本、伊朗、以色列等
3. 主要组织：美联储、白宫、克里姆林宫等
4. 事件关键词：谈判、制裁、加息、冲突等

【political 判断规则】

返回 "true"（政治情报类）：
- 政治：政府决策、政策法规、选举、政党活动、政治人物言行
- 军事：战争、冲突、军演、军备、国防
- 外交：国际关系、外交会谈、制裁、访问、条约
- 情报：间谍、监控、网络安全、数据泄露
- 经济政策：贸易战、关税、央行政策、重大经济法规

返回 "false"（软新闻类）：
- 体育赛事：NBA、足球、篮球、网球、F1、奥运、比赛战报、球员转会
- 娱乐八卦：明星、电影、综艺、演唱会、颁奖典礼
- 民生休闲：美食、旅游、天气、健康、时尚
- 商业营销：产品发布、品牌推广（不含政策影响）

【示例】

输入: {"id":"1","title":"特朗普称与伊朗的间接谈判未达成协议"}
输出: {"id":"1","country":"US","event":"美伊谈判失败","tags":["特朗普","伊朗","谈判","美国"],"political":"true"}

输入: {"id":"2","title":"日本首相岸田文雄访华会谈"}
输出: {"id":"2","country":"JP","event":"岸田访华会谈","tags":["岸田文雄","日本","中国","访华"],"political":"true"}

输入: {"id":"3","title":"NBA总决赛湖人队获胜"}
输出: {"id":"3","country":"US","event":"软新闻","tags":["NBA","湖人队"],"political":"false"}

输出JSON数组，不要输出其他内容：
[{"id":"1","country":"US","event":"美伊谈判失败","tags":["特朗普","伊朗","谈判"],"political":"true"}]

输入数据："""


# ═══════════════════════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════════════════════

def _build_batch_input(articles: list[dict]) -> str:
    """构建批量输入 JSON（精简版，只含标题）"""
    items = []
    for a in articles:
        items.append({
            "id": a.get("id") or a.get("url_hash", ""),
            "title": a.get("title", "")[:100],  # 限制长度
        })
    return json.dumps(items, ensure_ascii=False)


def _parse_extraction_response(raw: str, article_count: int) -> list[dict]:
    """解析 LLM 返回的 JSON（增强容错）"""
    if not raw:
        return []

    try:
        # 清理各种格式问题
        text = raw.strip()

        # 处理 markdown 代码块
        if text.startswith("```"):
            lines = text.split("\n")
            # 去掉第一行和最后一行
            if len(lines) > 2:
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            else:
                text = ""

        # 去掉 "json" 标记
        if text.startswith("json"):
            text = text[4:].strip()

        # 去掉可能的前缀文本（如 "以下是结果："）
        json_start = text.find("[")
        if json_start >= 0:
            text = text[json_start:]

        # 去掉可能的尾部文本
        json_end = text.rfind("]")
        if json_end >= 0:
            text = text[:json_end + 1]

        if not text:
            return []

        parsed = json.loads(text)

        if not isinstance(parsed, list):
            # 尝试从对象中提取数组
            if isinstance(parsed, dict):
                for key in ["results", "data", "articles", "items"]:
                    if key in parsed and isinstance(parsed[key], list):
                        parsed = parsed[key]
                        break
                else:
                    return []
            else:
                return []

        # 验证并标准化每个条目（兼容新旧字段名）
        results = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            # 兼容两种字段命名
            primary_country = item.get("country") or item.get("primary_country") or ""
            event_key = item.get("event") or item.get("event_key") or ""
            entities = item.get("tags") or item.get("entities") or []

            # 解析 political 字段（严格解析，默认 True 仅当字段缺失）
            political_raw = item.get("political")
            if political_raw is None:
                # 字段缺失时保守处理：保留（假设是政治新闻）
                is_political = True
            else:
                # 字段存在时严格解析
                is_political = str(political_raw).lower() in ("true", "1", "yes")

            result = {
                "id": str(item.get("id", "")),
                "primary_country": str(primary_country).upper() or "INTL",
                "event_key": str(event_key)[:20],
                "related_countries": item.get("related_countries", [])[:3],
                "entities": entities[:5] if isinstance(entities, list) else [],
                "confidence": float(item.get("confidence", 0.8)),
                "is_political": is_political,
            }

            # 验证列表字段
            if not isinstance(result["related_countries"], list):
                result["related_countries"] = []
            if not isinstance(result["entities"], list):
                result["entities"] = []

            results.append(result)

        return results

    except json.JSONDecodeError as e:
        print(f"[WARN] JSON 解析失败: {e}", file=sys.stderr)
        # print(f"[DEBUG] 原始响应前200字符: {raw[:200] if raw else '空'}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[WARN] 解析异常: {e}", file=sys.stderr)
        return []


def _apply_rule_based_fallback(articles: list[dict]) -> list[dict]:
    """
    当模型连续失败时的规则兜底提取
    """
    # 加载置顶词用于强力判定
    config_path = BASE / "config" / "hotspot_schedule.yaml"
    pinning_keywords = set()
    if config_path.exists():
        try:
            cfg = yaml.safe_load(config_path.read_text("utf-8")) or {}
            pinning_keywords = set(cfg.get("settings", {}).get("pinning_keywords", []))
        except: pass
    
    results = []
    for a in articles:
        title = a.get("title", "")
        # 只要包含置顶词，就视为政治情报类
        is_pinned = any(kw in title for kw in pinning_keywords)
        
        results.append({
            "id": str(a.get("id") or a.get("url_hash", "")),
            "primary_country": "INTL",
            "event_key": title[:10] if is_pinned else "",
            "related_countries": [],
            "entities": [],
            "confidence": 0.5,
            "is_political": is_pinned
        })
    return results


def extract_batch_sync(articles: list[dict], llm_cfg: dict, provider: str = None) -> list[dict]:
    """
    同步批量提取（单批次）

    Args:
        articles: 文章列表，每项需含 id, title, source
        llm_cfg: load_llm_config() 返回的配置
        provider: 指定 LLM 提供商

    Returns:
        提取结果列表
    """
    if not articles:
        return []

    # 构建输入
    input_json = _build_batch_input(articles)

    # 构建 messages
    messages = [
        {"role": "user", "content": f"{EXTRACTION_PROMPT}\n\n## 输入\n{input_json}"}
    ]

    # 覆盖 provider
    cfg = dict(llm_cfg) if llm_cfg else {}
    if provider and cfg.get("llm"):
        cfg = {**cfg, "llm": {**cfg["llm"], "provider": provider}}

    # 调用 LLM (带 Fallback 路由)
    raw = _call_llm_api(messages, cfg)

    if not raw:
        print(f"[WARN] 所有 LLM 模型均失效，启用规则兜底", file=sys.stderr)
        return _apply_rule_based_fallback(articles)

    # 解析结果
    results = _parse_extraction_response(raw, len(articles))

    if not results:
        print(f"[WARN] JSON 解析为空，启用规则兜底", file=sys.stderr)
        return _apply_rule_based_fallback(articles)

    return results


def extract_all_concurrent(
    articles: list[dict],
    llm_cfg: dict,
    provider: str = None,
    batch_size: int = BATCH_SIZE,
    max_workers: int = MAX_WORKERS,
    quiet: bool = False
) -> list[dict]:
    """
    真正并发提取所有文章（使用 ThreadPoolExecutor + 错开提交）

    Args:
        articles: 文章列表
        llm_cfg: LLM 配置
        provider: 指定提供商
        batch_size: 每批数量
        max_workers: 最大并发线程数
        quiet: 静默模式

    Returns:
        所有提取结果
    """
    import time

    if not articles:
        return []

    total = len(articles)
    batches = [
        articles[i:i + batch_size]
        for i in range(0, total, batch_size)
    ]

    if not quiet:
        print(f"[提取] 开始并发处理 {total} 条文章", flush=True)
        print(f"[提取] {len(batches)} 批次，每批 {batch_size} 条，并发 {max_workers} 线程", flush=True)

    all_results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 错开提交批次，避免同时触发 API 限流
        future_to_batch = {}
        for i, batch in enumerate(batches):
            future = executor.submit(extract_batch_sync, batch, llm_cfg, provider)
            future_to_batch[future] = i
            # 每提交一个批次后等待一小段时间（错开 API 调用）
            if i < len(batches) - 1:
                time.sleep(0.5)

        # 收集结果
        for future in as_completed(future_to_batch):
            batch_idx = future_to_batch[future]
            try:
                results = future.result()
                all_results.extend(results)
                if not quiet:
                    print(f"[提取] 批次 {batch_idx + 1}/{len(batches)} 完成，提取 {len(results)} 条", flush=True)
            except Exception as e:
                print(f"[WARN] 批次 {batch_idx + 1} 异常: {e}", file=sys.stderr, flush=True)

    if not quiet:
        print(f"[提取] 完成，共提取 {len(all_results)}/{total} 条", flush=True)

    return all_results


# 保持向后兼容
def extract_all_sync(
    articles: list[dict],
    llm_cfg: dict,
    provider: str = None,
    batch_size: int = BATCH_SIZE,
    quiet: bool = False
) -> list[dict]:
    """
    并发提取所有文章（调用新版本的并发实现）
    """
    return extract_all_concurrent(
        articles, llm_cfg, provider,
        batch_size=batch_size,
        max_workers=MAX_WORKERS,
        quiet=quiet
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库操作
# ═══════════════════════════════════════════════════════════════════════════════

def save_extraction_results(results: list[dict], conn=None) -> int:
    """
    将提取结果保存到数据库

    Args:
        results: 提取结果列表
        conn: 数据库连接

    Returns:
        更新的记录数
    """
    if not results:
        return 0

    from store import get_conn, init_db

    own_conn = conn is None
    if own_conn:
        conn = get_conn()
        init_db(conn)

    updated = 0
    now = datetime.now(TZ_BJ).isoformat()

    for r in results:
        try:
            # 根据 id 或 url_hash 更新
            article_id = r.get("id", "")
            if not article_id:
                continue

            # 判断是数字ID还是 url_hash
            if article_id.isdigit():
                where_clause = "id = ?"
            else:
                where_clause = "url_hash = ?"

            conn.execute(f"""
                UPDATE articles SET
                    primary_country = ?,
                    event_key = ?,
                    related_countries = ?,
                    entities = ?,
                    llm_confidence = ?,
                    is_political = ?,
                    analyzed_at = ?
                WHERE {where_clause}
            """, [
                r.get("primary_country", ""),
                r.get("event_key", ""),
                json.dumps(r.get("related_countries", []), ensure_ascii=False),
                json.dumps(r.get("entities", []), ensure_ascii=False),
                r.get("confidence", 0.8),
                r.get("is_political", True),
                now,
                article_id
            ])
            updated += 1
        except Exception as e:
            print(f"[WARN] 保存失败: {e}", file=sys.stderr)

    conn.commit()

    if own_conn:
        conn.close()

    return updated


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def get_unanalyzed_articles(hours: int = 24, limit: int = 1000, conn=None) -> list[dict]:
    """
    获取未分析的文章（全部交给LLM判断是否为软新闻）

    Args:
        hours: 时间窗口
        limit: 最大数量
        conn: 数据库连接

    Returns:
        文章列表
    """
    from store import get_conn, init_db

    own_conn = conn is None
    if own_conn:
        conn = get_conn()
        init_db(conn)

    now = datetime.now(TZ_BJ)
    start_time = (now - timedelta(hours=hours)).isoformat()

    rows = conn.execute("""
        SELECT id, url_hash, title, summary, platform, media_group, published, media_tier
        FROM articles
        WHERE published >= ?
          AND (event_key IS NULL OR event_key = '')
        ORDER BY published DESC
        LIMIT ?
    """, [start_time, limit]).fetchall()

    articles = [dict(r) for r in rows]

    if own_conn:
        conn.close()

    return articles


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="事件结构化提取")
    parser.add_argument("--hours", type=int, default=24, help="时间窗口（小时）")
    parser.add_argument("--limit", type=int, default=100, help="最大处理数量")
    parser.add_argument("--provider", type=str, help="指定 LLM 提供商")
    parser.add_argument("--dry-run", action="store_true", help="只打印不保存")
    args = parser.parse_args()

    # 加载配置
    llm_cfg = load_llm_config()
    if not llm_cfg:
        print("[ERROR] 无法加载 LLM 配置")
        sys.exit(1)

    # 获取未分析文章
    print(f"[INFO] 获取最近 {args.hours} 小时未分析文章...")
    articles = get_unanalyzed_articles(hours=args.hours, limit=args.limit)
    print(f"[INFO] 找到 {len(articles)} 条")

    if not articles:
        print("[INFO] 无待处理文章")
        sys.exit(0)

    # 提取
    results = extract_all_sync(articles, llm_cfg, provider=args.provider)

    # 保存
    if args.dry_run:
        print("\n[DRY-RUN] 结果预览：")
        for r in results[:5]:
            print(f"  {r}")
    else:
        updated = save_extraction_results(results)
        print(f"[INFO] 已保存 {updated} 条")


# ═══════════════════════════════════════════════════════════════════════════════
# 快速入口（供外部调用）
# ═══════════════════════════════════════════════════════════════════════════════

def run_extraction(
    articles: list[dict],
    llm_cfg: dict,
    provider: str = None,
    batch_size: int = BATCH_SIZE,
    max_workers: int = MAX_WORKERS,
    quiet: bool = False
) -> list[dict]:
    """
    并发提取入口（推荐用于生产环境）
    """
    return extract_all_concurrent(
        articles, llm_cfg, provider,
        batch_size, max_workers, quiet
    )