#!/usr/bin/env python3
"""RSS 管理面板 — Flask API + 后台定时抓取 + 静态前端"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, abort, Response
import yaml
import requests

BASE = Path(__file__).parent
SCRIPTS = BASE / "scripts"
CFG_PATH = BASE / "config" / "feeds.yaml"
WEB_DIR = BASE / "web"
TZ_BJ = timezone(timedelta(hours=8))

sys.path.insert(0, str(SCRIPTS))
from store import (
    init_db, get_conn, upsert_articles,
    query_by_time, query_by_keyword, query_by_period,
    get_stats, delete_before, count_articles,
    log_fetch_start, log_fetch_end, get_fetch_logs, get_latest_fetch_status,
    upsert_external_articles
)
from external_fetcher import UnifiedSearcher
from filter_utils import is_chinese_media
import intelligence
import timeline

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path='')

# ── 全局状态 ─────────────────────────────────────────────

scheduler_state = {
    'running': False,
    'interval_min': 60,
    'last_run': None,
    'next_run': None,
    'next_run_reason': '', # 新增：记录下一次运行的原因（常规或补位）
    'is_fetching': False,
    'fetch_progress': '',
}


# ── 工具函数 ─────────────────────────────────────────────

def load_feeds_config():
    if not CFG_PATH.exists():
        return {'feeds': [], 'settings': {}}
    return yaml.safe_load(CFG_PATH.read_text('utf-8')) or {'feeds': [], 'settings': {}}


def save_feeds_config(cfg):
    CFG_PATH.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False), 'utf-8')


def run_fetch(hours=None, full=False, since=None):
    """
    异步启动增量抓取 (scripts/fetch.py)
    hours: 只保留近 N 小时
    full: 是否全量抓取 (默认 False 为增量)
    since: 指定起始时间 (ISO 字符串)
    """
    started = datetime.now(TZ_BJ)
    conn = get_conn()
    init_db(conn)
    log_id = log_fetch_start(conn)
    conn.close()

    cmd = [sys.executable, str(SCRIPTS / "fetch.py")]
    if hours:
        cmd += ['--hours', str(hours)]
    if since:
        cmd += ['--since', str(since)]
    if full:
        cmd += ['--full']

    print(f'[INFO] 执行抓取任务: {"全量模式" if full else "增量模式"} {"(since "+str(since)+")" if since else ""}')
    print(f'[INFO] 命令: {" ".join(cmd)}')

    child_env = os.environ.copy()
    child_env.setdefault('PYTHONUTF8', '1')
    child_env.setdefault('PYTHONIOENCODING', 'utf-8')
    
    try:
        scheduler_state['is_fetching'] = True
        scheduler_state['fetch_progress'] = '启动抓取任务...'
        
        # 使用 Popen 读取实时 progress，强制 UTF-8 避免 Windows 编码死锁
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True, 
            bufsize=1,
            encoding='utf-8',
            errors='replace',
            env=child_env
        )
        
        # 实时读取 stderr 的线程函数
        def read_stderr(pipe):
            try:
                for line in iter(pipe.readline, ''):
                    if not line: break
                    if '[PROGRESS]' in line:
                        p = line.split('[PROGRESS]')[-1].strip()
                        try:
                            # 尝试 JSON 解析，失败则降级为文本
                            if p.startswith('{') and p.endswith('}'):
                                scheduler_state['fetch_progress'] = json.loads(p)
                            else:
                                scheduler_state['fetch_progress'] = p
                        except:
                            scheduler_state['fetch_progress'] = p
            except Exception as e:
                print(f"[WARN] Error reading stderr: {e}")
            finally:
                pipe.close()

        stderr_thread = threading.Thread(target=read_stderr, args=(process.stderr,), daemon=True)
        stderr_thread.start()

        # 仅手动读取 stdout，stderr 由独立线程消费，避免与 communicate() 竞争
        if process.stdout is not None:
            stdout = process.stdout.read()
        else:
            stdout = ''
        process.wait()
        stderr_thread.join(timeout=5)
        
        finished = datetime.now(TZ_BJ)
        duration = (finished - started).total_seconds()

        if process.returncode == 0:
            try:
                data = json.loads(stdout)
                stats_data = data.get('stats', {})
                cfg = load_feeds_config()
                fetch_stats = {
                    'status': 'done',
                    'duration_sec': round(duration, 1),
                    'feeds_total': stats_data.get('total_feeds_configured', len(cfg.get('feeds', []))),
                    'feeds_ok': stats_data.get('total_feeds_configured', 0) - len([w for w in stats_data.get('dup_intra_titles', []) if '失败' in w]),
                    'feeds_failed': 0,
                    'articles_new': stats_data.get('db_inserted', stats_data.get('final_count', 0)),
                    'articles_total': stats_data.get('total_raw_count', 0),
                    'failed_feeds': [],
                    'details': {
                        'time_filtered_out': stats_data.get('time_filtered_out_count', 0),
                        'merged_intra': stats_data.get('merged_intra_source_count', 0),
                        'merged_global': stats_data.get('merged_global_count', 0),
                        'historical_filtered': stats_data.get('historical_filtered_count', 0),
                    }
                }
            except Exception:
                fetch_stats = {
                    'status': 'done', 'duration_sec': round(duration, 1),
                    'articles_new': 0, 'details': {'raw_output_size': len(stdout)}
                }
        else:
            fetch_stats = {
                'status': 'error', 'duration_sec': round(duration, 1),
                'details': {'error': 'Subprocess failed with code ' + str(process.returncode)}
            }
    except Exception as e:
        fetch_stats = { 'status': 'error', 'duration_sec': 0, 'details': {'error': str(e)} }
    finally:
        scheduler_state['is_fetching'] = False
        scheduler_state['fetch_progress'] = ''

    conn = get_conn()
    log_fetch_end(log_id, fetch_stats, conn)
    conn.close()

    scheduler_state['last_run'] = datetime.now(TZ_BJ).isoformat()
    return fetch_stats


# ── 后台调度器 ────────────────────────────────────────────

def scheduler_loop():
    while scheduler_state['running']:
        interval = scheduler_state['interval_min']
        now = datetime.now(TZ_BJ)
        
        # --- 计算下一次执行的确切目标时间 ---
        # 1. 理论下次运行
        theory_next = now + timedelta(minutes=interval)
        
        # 2. 计算整点补位时刻 (下一个整点 + 90秒)
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        padding_target = next_hour + timedelta(seconds=90)
        
        # 3. 取较早者
        if theory_next > padding_target:
            target_run_dt = padding_target
            wait_reason = f"整点补位 (对齐 {next_hour.strftime('%H:00')})"
        else:
            target_run_dt = theory_next
            wait_reason = f"常规间隔 ({interval}m)"
            
        scheduler_state['next_run'] = target_run_dt.isoformat()
        scheduler_state['next_run_reason'] = wait_reason
        print(f"[*] 调度器排队中: 预计 {target_run_dt.strftime('%H:%M:%S')} 执行 ({wait_reason})")

        # --- 精确等待循环 ---
        while scheduler_state['running']:
            curr = datetime.now(TZ_BJ)
            if curr >= target_run_dt:
                break
            # 每秒检查一次，支持即时停止或间隔调整（虽然此处间隔调整需下次循环生效）
            _time.sleep(1)
            
        if scheduler_state['running']:
            print(f"[*] 启动预定抓取任务: {datetime.now(TZ_BJ).strftime('%H:%M:%S')}")
            run_fetch()


scheduler_thread = None


def start_scheduler(interval_min=60):
    global scheduler_thread
    if scheduler_state['running']:
        return
    scheduler_state['running'] = True
    scheduler_state['interval_min'] = interval_min
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()


def stop_scheduler():
    scheduler_state['running'] = False


# ── 静态文件 ──────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(str(WEB_DIR), 'index.html')


@app.route('/foreign_media.html')
def foreign_media():
    """外媒舆情页面"""
    return send_from_directory(str(WEB_DIR), 'foreign_media.html')


@app.route('/playground')
def playground():
    """API Playground 测试页面"""
    return send_from_directory(str(WEB_DIR), 'api_playground.html')


@app.route('/<path:path>')
def static_files(path):
    return send_from_directory(str(WEB_DIR), path)


# ── API: 源管理 ──────────────────────────────────────────

@app.route('/api/feeds', methods=['GET'])
def api_get_feeds():
    cfg = load_feeds_config()
    feeds = cfg.get('feeds', [])
    for i, f in enumerate(feeds):
        f['_index'] = i
    return jsonify({'feeds': feeds, 'settings': cfg.get('settings', {})})


@app.route('/api/feeds', methods=['POST'])
def api_add_feed():
    data = request.json
    if not data or not data.get('url'):
        return jsonify({'error': 'url 必填'}), 400
    cfg = load_feeds_config()
    new_feed = {
        'url': data['url'].strip(),
        'platform': data.get('platform', '').strip() or data['url'],
        'media_group': data.get('media_group', '').strip(),
        'country': data.get('country', '').strip(),
        'city': data.get('city', '').strip(),
        'timeout': int(data.get('timeout', 30)),
        'time_only': bool(data.get('time_only', False)),
        'scrape_url': data.get('scrape_url', '').strip(),
        'fetch_jina': bool(data.get('fetch_jina', False)),
    }
    cfg.setdefault('feeds', []).append(new_feed)
    save_feeds_config(cfg)
    return jsonify({'ok': True, 'index': len(cfg['feeds']) - 1})


@app.route('/api/feeds/<int:idx>', methods=['PUT'])
def api_update_feed(idx):
    data = request.json
    cfg = load_feeds_config()
    feeds = cfg.get('feeds', [])
    if idx < 0 or idx >= len(feeds):
        return jsonify({'error': '索引越界'}), 404
    if data.get('url'):
        feeds[idx]['url'] = data['url'].strip()
    if 'platform' in data:
        feeds[idx]['platform'] = data['platform'].strip()
    if 'media_group' in data:
        feeds[idx]['media_group'] = data['media_group'].strip()
    if 'country' in data:
        feeds[idx]['country'] = data['country'].strip()
    if 'city' in data:
        feeds[idx]['city'] = data['city'].strip()
    if 'timeout' in data:
        feeds[idx]['timeout'] = int(data['timeout'])
    if 'time_only' in data:
        feeds[idx]['time_only'] = bool(data['time_only'])
    if 'scrape_url' in data:
        feeds[idx]['scrape_url'] = data['scrape_url'].strip()
    if 'fetch_jina' in data:
        feeds[idx]['fetch_jina'] = bool(data['fetch_jina'])
    if 'is_s_tier' in data:
        feeds[idx]['is_s_tier'] = bool(data['is_s_tier'])
    save_feeds_config(cfg)
    return jsonify({'ok': True})


@app.route('/api/feeds/<int:idx>', methods=['DELETE'])
def api_delete_feed(idx):
    cfg = load_feeds_config()
    feeds = cfg.get('feeds', [])
    if idx < 0 or idx >= len(feeds):
        return jsonify({'error': '索引越界'}), 404
    removed = feeds.pop(idx)
    save_feeds_config(cfg)
    return jsonify({'ok': True, 'removed': removed})


# ── API: 文章查询 ─────────────────────────────────────────

@app.route('/api/articles', methods=['GET'])
def api_articles():
    keyword = request.args.get('keyword', '').strip()
    period = request.args.get('period', '')
    offset_n = int(request.args.get('offset', 0))
    hours = request.args.get('hours', type=float)
    start = request.args.get('start', '')
    end = request.args.get('end', '')
    media = request.args.get('media', '').strip()
    platform = request.args.get('platform', '').strip()
    country = request.args.get('country', '').strip()
    limit = min(int(request.args.get('limit', 100)), 500)
    page = max(int(request.args.get('page', 1)), 1)
    offset_val = (page - 1) * limit

    conn = get_conn()
    now = datetime.now(TZ_BJ)

    # 确定时间范围
    if hours:
        start = (now - timedelta(hours=hours)).isoformat()
        end = now.isoformat()
    elif period in ('day', 'week', 'month'):
        if period == 'day':
            base = now.replace(hour=0, minute=0, second=0, microsecond=0)
            s = base + timedelta(days=offset_n)
            e = s + timedelta(days=1)
        elif period == 'week':
            base = now - timedelta(days=now.weekday())
            base = base.replace(hour=0, minute=0, second=0, microsecond=0)
            s = base + timedelta(weeks=offset_n)
            e = s + timedelta(weeks=1)
        elif period == 'month':
            y, m = now.year, now.month + offset_n
            while m <= 0: y -= 1; m += 12
            while m > 12: y += 1; m -= 12
            s = datetime(y, m, 1, tzinfo=TZ_BJ)
            nm, ny = m + 1, y
            if nm > 12: nm, ny = 1, y + 1
            e = datetime(ny, nm, 1, tzinfo=TZ_BJ)
        start = s.isoformat()
        end = e.isoformat()

    # 查询
    if keyword:
        items = query_by_keyword(
            keyword, start=start or None, end=end or None,
            media_group=media or None, platform=platform or None, country=country or None,
            limit=limit, offset=offset_val, conn=conn)
        total = count_articles(
            start=start or None, end=end or None,
            keyword=keyword, media_group=media or None, platform=platform or None, country=country or None,
            conn=conn)
    elif start:
        items = query_by_time(
            start, end or now.isoformat(),
            media_group=media or None, platform=platform or None, country=country or None,
            limit=limit, offset=offset_val, conn=conn)
        total = count_articles(
            start=start, end=end or now.isoformat(),
            media_group=media or None, platform=platform or None, country=country or None,
            conn=conn)
    else:
        # 如果没有指定时间，不再强制“今天”，默认返回最近的高频数据（如近30天）
        # 让“所有时间跨度”真正生效
        items = query_by_time(
            None, None,
            media_group=media or None, platform=platform or None, country=country or None,
            limit=limit, offset=offset_val, conn=conn)
        total = count_articles(
            start=None, end=None,
            media_group=media or None, platform=platform or None, country=country or None,
            conn=conn)

    conn.close()

    # 清理内部字段（但保留 id，前端选择文章需要用 id）
    internal = {'url_hash', 'title_hash', 'created_at'}
    clean_items = [{k: v for k, v in dict(i).items() if k not in internal} for i in items]

    return jsonify({
        'count': len(clean_items),
        'total': total,
        'page': page,
        'limit': limit,
        'items': clean_items
    })


# ── API: 统计 ────────────────────────────────────────────

@app.route('/api/stats', methods=['GET'])
def api_stats():
    conn = get_conn()
    stats = get_stats(conn)
    conn.close()
    return jsonify(stats)


# ── API: 数据维护 ─────────────────────────────────────────

@app.route('/api/articles/cleanup', methods=['DELETE'])
def api_cleanup():
    before_date = request.args.get('before', '')
    if not before_date:
        return jsonify({'error': 'before 参数必填'}), 400
    conn = get_conn()
    deleted = delete_before(before_date, conn)
    conn.close()
    return jsonify({'deleted': deleted})


@app.route('/api/articles/clear-all', methods=['POST'])
def api_clear_all():
    conn = get_conn()
    conn.execute("DELETE FROM articles")
    conn.execute("DELETE FROM fetch_logs")
    conn.commit()
    # 释放存储空间
    conn.execute("VACUUM")
    conn.close()
    
    # 关键点：物理删除已读缓存
    state_file = BASE / "config" / "state.json"
    if state_file.exists():
        try:
            os.remove(state_file)
        except Exception as e:
            print(f"[WARN] Failed to delete state.json: {e}")
            
    return jsonify({'ok': True})


# ── API: 抓取控制 ─────────────────────────────────────────

@app.route('/api/fetch', methods=['POST'])
def api_trigger_fetch():
    if scheduler_state['is_fetching']:
        return jsonify({'error': '抓取正在进行中'}), 409
    
    hours = None
    full = False
    since = None
    if request.json:
        hours = request.json.get('hours', None)
        full = request.json.get('full', False)
        since = request.json.get('since', None)

    def do_fetch():
        run_fetch(hours=hours, full=full, since=since)
    t = threading.Thread(target=do_fetch, daemon=True)
    t.start()
    return jsonify({'ok': True, 'message': f'已触发抓取 ({"全量模式" if full else "增量检查模式"})'})


@app.route('/api/fetch/status', methods=['GET'])
def api_fetch_status():
    conn = get_conn()
    latest = get_latest_fetch_status(conn)
    conn.close()
    # 获取 state.json 中的增量时间点
    last_fetch_at = None
    state_file = BASE / "config" / "state.json"
    if state_file.exists():
        try:
            state_data = json.loads(state_file.read_text('utf-8'))
            last_fetch_at = state_data.get('last_fetch_at')
        except: pass

    return jsonify({
        'scheduler': {
            'running': scheduler_state['running'],
            'interval_min': scheduler_state['interval_min'],
            'last_run': scheduler_state['last_run'],
            'next_run': scheduler_state['next_run'],
            'next_run_reason': scheduler_state['next_run_reason'],
            'is_fetching': scheduler_state['is_fetching'],
            'fetch_progress': scheduler_state['fetch_progress'],
            'last_fetch_at': last_fetch_at  # 新增：从 state.json 拿到的增量锚点
        },
        'latest_fetch': latest
    })


@app.route('/api/scheduler/interval', methods=['POST'])
def api_set_interval():
    val = request.json.get('interval', 60)
    auto_fetch = request.json.get('auto_fetch', False)
    
    scheduler_state['interval_min'] = int(val)
    
    if auto_fetch and not scheduler_state['running']:
        start_scheduler(int(val))
    elif not auto_fetch and scheduler_state['running']:
        stop_scheduler()
        
    # 也同步更新到 config 中持久化
    cfg = load_feeds_config()
    cfg.setdefault('settings', {})['interval_min'] = int(val)
    cfg.setdefault('settings', {})['auto_fetch'] = bool(auto_fetch)
    save_feeds_config(cfg)
    return jsonify({'ok': True, 'interval': val, 'auto_fetch': auto_fetch})


@app.route('/api/fetch/logs', methods=['GET'])
def api_fetch_logs():
    limit = int(request.args.get('limit', 20))
    conn = get_conn()
    logs = get_fetch_logs(limit, conn)
    conn.close()
    return jsonify({'logs': logs})


# ── API: 媒体组列表 ──────────────────────────────────────

@app.route('/api/media-groups', methods=['GET'])
def api_media_groups():
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT media_group FROM articles WHERE media_group IS NOT NULL ORDER BY media_group"
    ).fetchall()
    conn.close()
    return jsonify({'groups': [r['media_group'] for r in rows]})


# ── API: 国家列表 ────────────────────────────────────────

@app.route('/api/countries', methods=['GET'])
def api_countries():
    """获取所有已配置的或数据库中存在的国家列表"""
    # 先从配置文件拿，保证包含尚未抓取到文章的源所属国家
    cfg = load_feeds_config()
    countries = set()
    for f in cfg.get('feeds', []):
        c = f.get('country', '').strip()
        if c: countries.add(c)
    
    # 再并入数据库中存在的国家（可能包含已被删除的源的文章）
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT country FROM articles WHERE country IS NOT NULL AND country != '' ORDER BY country"
    ).fetchall()
    conn.close()
    for r in rows:
        countries.add(r['country'])
        
    return jsonify({'countries': sorted(list(countries))})



# ── LLM 主题管理 API ──────────────────────────────────

LLM_CFG_PATH = BASE / "config" / "llm_topics.yaml"


def _load_llm_cfg():
    if not LLM_CFG_PATH.exists():
        return {'llm': {'enabled': False}, 'topics': []}
    try:
        return yaml.safe_load(LLM_CFG_PATH.read_text('utf-8')) or {'llm': {'enabled': False}, 'topics': []}
    except Exception:
        return {'llm': {'enabled': False}, 'topics': []}


def _save_llm_cfg(cfg):
    LLM_CFG_PATH.write_text(
        yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
        'utf-8'
    )


@app.route('/api/llm/config', methods=['GET'])
def api_llm_config():
    cfg = _load_llm_cfg()
    llm = cfg.get('llm', {})
    safe_llm = {k: v for k, v in llm.items() if k != 'api_key'}
    safe_llm['has_api_key'] = bool(llm.get('api_key', '')) and not llm.get('api_key', '').startswith('sk-YOUR')
    return jsonify({'llm': safe_llm, 'topics': cfg.get('topics', [])})


@app.route('/api/llm/config', methods=['PUT'])
def api_llm_config_update():
    data = request.json or {}
    cfg = _load_llm_cfg()
    llm = cfg.setdefault('llm', {})
    for key in ('enabled', 'model', 'base_url', 'api_key', 'max_batch', 'temperature'):
        if key in data:
            llm[key] = data[key]
    _save_llm_cfg(cfg)
    return jsonify({'ok': True})


@app.route('/api/llm/topics', methods=['GET'])
def api_llm_topics():
    cfg = _load_llm_cfg()
    return jsonify(cfg.get('topics', []))


@app.route('/api/llm/topics', methods=['POST'])
def api_llm_topic_add():
    data = request.json or {}
    name = data.get('name', '').strip()
    desc = data.get('description', '').strip()
    if not name:
        return jsonify({'error': '主题名称不能为空'}), 400
    cfg = _load_llm_cfg()
    topics = cfg.setdefault('topics', [])
    if any(t['name'] == name for t in topics):
        return jsonify({'error': f'主题 "{name}" 已存在'}), 409
    topics.append({'name': name, 'description': desc or f'与"{name}"相关的新闻内容'})
    _save_llm_cfg(cfg)
    return jsonify({'ok': True, 'topics': topics})


@app.route('/api/llm/topics/<int:idx>', methods=['PUT'])
def api_llm_topic_update(idx):
    data = request.json or {}
    cfg = _load_llm_cfg()
    topics = cfg.get('topics', [])
    if idx < 0 or idx >= len(topics):
        return jsonify({'error': '主题索引越界'}), 404
    if 'name' in data:
        topics[idx]['name'] = data['name'].strip()
    if 'description' in data:
        topics[idx]['description'] = data['description'].strip()
    _save_llm_cfg(cfg)
    return jsonify({'ok': True, 'topic': topics[idx]})


@app.route('/api/llm/topics/<int:idx>', methods=['DELETE'])
def api_llm_topic_delete(idx):
    cfg = _load_llm_cfg()
    topics = cfg.get('topics', [])
    if idx < 0 or idx >= len(topics):
        return jsonify({'error': '主题索引越界'}), 404
    removed = topics.pop(idx)
    _save_llm_cfg(cfg)
    return jsonify({'ok': True, 'removed': removed})


@app.route('/api/llm/topics/<int:idx>/polish', methods=['POST'])
def api_llm_topic_polish(idx):
    cfg = _load_llm_cfg()
    topics = cfg.get('topics', [])
    if idx < 0 or idx >= len(topics):
        return jsonify({'error': '主题索引越界'}), 404
    topic = topics[idx]
    try:
        sys.path.insert(0, str(BASE / 'scripts'))
        from llm_tagger import polish_topic_description
        polished = polish_topic_description(topic['name'], topic['description'], cfg)
        if polished:
            topics[idx]['description'] = polished
            _save_llm_cfg(cfg)
            return jsonify({'ok': True, 'topic': topics[idx], 'polished': True})
        else:
            return jsonify({'ok': False, 'error': 'AI 润色失败'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/articles/by-tag', methods=['GET'])
def api_articles_by_tag():
    tag = request.args.get('tag', '')
    if not tag:
        return jsonify({'error': '缺少 tag 参数'}), 400
    start = request.args.get('start')
    end = request.args.get('end')
    media_group = request.args.get('media_group')
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    from scripts.store import query_by_tag
    items = query_by_tag(tag=tag, start=start, end=end,
                         media_group=media_group, limit=limit, offset=offset)
    return jsonify({'count': len(items), 'tag': tag, 'items': items})


# ── 情报聚合与分析 API ──────────────────────────────────

@app.route('/api/intelligence/hot', methods=['GET'])
def api_get_hot_events():
    period = request.args.get('period', 'day')
    start_time = request.args.get('start')
    end_time = request.args.get('end')
    provider = request.args.get('provider')
    use_fast = request.args.get('fast', 'true').lower() == 'true'

    try:
        if use_fast:
            from hotspot_detector import detect_hot_events
            
            # 优先级：若提供了 start，则使用自定义时间范围
            if start_time:
                events = detect_hot_events(start_time=start_time, end_time=end_time, max_results=20)
            else:
                hours_map = {
                    'min30': 0.5,
                    'hour1': 1,
                    'hour2': 2,
                    'day': 24,
                    'week': 168,
                    'month': 720
                }
                hours = hours_map.get(period, 24)
                events = detect_hot_events(hours=hours, max_results=20)

            # 移除 items 全文以节省带宽，只保留 id
            for e in events:
                e['article_ids'] = [a['id'] for a in e.get('items', []) if a.get('id')]
                if 'items' in e:
                    for item in e['items']:
                        if 'content' in item: del item['content']
                if 'score_details' in e:
                    del e['score_details']
        else:
            # 使用原有 LLM 聚类算法（较慢）
            events = intelligence.generate_hot_events(period, provider=provider)
            for e in events:
                for item in e['items']:
                    if 'content' in item: del item['content']

        return jsonify({'events': events, 'mode': 'fast' if use_fast else 'llm'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/intelligence/hot/fast', methods=['GET'])
def api_get_hot_events_fast():
    """快速热点检测API - 纯本地算法，支持任意时间窗口"""
    hours = request.args.get('hours', 24, type=int)
    max_results = request.args.get('max', 15, type=int)

    try:
        from hotspot_detector import get_hot_events_brief
        events = get_hot_events_brief(hours=hours, max_results=max_results)
        return jsonify({'events': events, 'hours': hours})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── 定时热点检测 API ─────────────────────────────────────────────

@app.route('/api/hotspot/categories', methods=['GET'])
def api_get_hotspot_categories():
    """获取所有热点检测分类配置"""
    # category_id 映射：配置文件旧名称 → 数据库新名称
    CATEGORY_MAP = {
        'china_related': 'foreign_china',
        'hk_tw_macau': 'greater_china',
        'asia_neighbors': 'asia_other',
    }

    try:
        import yaml
        from pathlib import Path
        config_path = Path(__file__).parent / 'config' / 'hotspot_schedule.yaml'
        if not config_path.exists():
            return jsonify({'categories': [], 'error': '配置文件不存在'})

        cfg = yaml.safe_load(config_path.read_text('utf-8')) or {}
        categories = cfg.get('categories', {})
        settings = cfg.get('settings', {})

        # 添加最后执行时间（使用映射后的 category_id 查询）
        from store import get_conn
        conn = get_conn()
        for cat_id in categories:
            # 映射到数据库中的 category_id
            db_cat_id = CATEGORY_MAP.get(cat_id, cat_id)
            try:
                row = conn.execute("""
                    SELECT executed_at FROM scheduled_hotspots
                    WHERE category_id = ? ORDER BY executed_at DESC LIMIT 1
                """, [db_cat_id]).fetchone()
                categories[cat_id]['last_executed'] = row['executed_at'] if row else None
            except:
                categories[cat_id]['last_executed'] = None
        conn.close()

        return jsonify({
            'categories': [{**v, 'id': k} for k, v in categories.items()],
            'settings': settings
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/hotspot/scheduled', methods=['GET'])
def api_get_hotspot_scheduled():
    """获取最新的定时热点检测结果"""
    category = request.args.get('category')
    limit = request.args.get('limit', 5, type=int)

    # category_id 映射：前端旧名称 → 数据库新名称
    CATEGORY_MAP = {
        'china_related': 'foreign_china',
        'hk_tw_macau': 'greater_china',
        'asia_neighbors': 'asia_other',
        # us_news, japan_news, middle_east 保持不变
    }

    # 映射 category_id
    db_category = CATEGORY_MAP.get(category, category)
    print(f"[API] 查询: category={category} → db_category={db_category}", flush=True)

    try:
        from store import get_conn
        conn = get_conn()

        if db_category:
            rows = conn.execute("""
                SELECT * FROM scheduled_hotspots
                WHERE category_id = ?
                ORDER BY executed_at DESC LIMIT ?
            """, [db_category, limit]).fetchall()
            print(f"[API] 查询到 {len(rows)} 条记录", flush=True)
        else:
            # 获取每个分类的最新一条
            rows = conn.execute("""
                SELECT * FROM scheduled_hotspots
                WHERE id IN (
                    SELECT MAX(id) FROM scheduled_hotspots GROUP BY category_id
                )
                ORDER BY executed_at DESC
            """).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            r['events'] = json.loads(r['events']) if isinstance(r['events'], str) else r['events']
            results.append(r)

        conn.close()
        return jsonify({'results': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/hotspot/history', methods=['GET'])
def api_get_hotspot_history():
    """获取历史热点记录"""
    category = request.args.get('category')
    days = request.args.get('days', 7, type=int)
    limit = request.args.get('limit', 50, type=int)

    try:
        from store import get_conn
        from datetime import datetime, timedelta, timezone
        TZ_BJ = timezone(timedelta(hours=8))

        conn = get_conn()
        cutoff = (datetime.now(TZ_BJ) - timedelta(days=days)).isoformat()

        if category:
            rows = conn.execute("""
                SELECT * FROM scheduled_hotspots
                WHERE category_id = ? AND executed_at >= ?
                ORDER BY executed_at DESC LIMIT ?
            """, [category, cutoff, limit]).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM scheduled_hotspots
                WHERE executed_at >= ?
                ORDER BY executed_at DESC LIMIT ?
            """, [cutoff, limit]).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            r['events'] = json.loads(r['events']) if isinstance(r['events'], str) else r['events']
            results.append(r)

        conn.close()
        return jsonify({'results': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/hotspot/execution-logs', methods=['GET'])
def api_hotspot_execution_logs():
    """
    获取热点检测执行记录（用于前端进度展示）

    Query params:
        days: 查询最近N天的记录（默认7天）
        category_id: 可选，筛选指定分类
    """
    days = request.args.get('days', 7, type=int)
    category_id = request.args.get('category_id', None)

    try:
        conn = get_conn()

        cutoff = (datetime.now(TZ_BJ) - timedelta(days=days)).isoformat()

        if category_id:
            rows = conn.execute("""
                SELECT id, category_id, category_name, executed_at, duration_seconds, status, event_count, article_count
                FROM scheduled_hotspots
                WHERE category_id = ? AND executed_at >= ?
                ORDER BY executed_at DESC
            """, [category_id, cutoff]).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, category_id, category_name, executed_at, duration_seconds, status, event_count, article_count
                FROM scheduled_hotspots
                WHERE executed_at >= ?
                ORDER BY executed_at DESC
            """, [cutoff]).fetchall()

        logs = []
        for r in rows:
            log = {
                'id': r['id'],
                'category_id': r['category_id'],
                'category_name': r['category_name'],
                'executed_at': r['executed_at'][:16] if r['executed_at'] else '',
                'duration_seconds': r['duration_seconds'] or 0,
                'status': r['status'] or 'success',
                'event_count': r['event_count'] or 0,
                'article_count': r['article_count'] or 0,
            }
            logs.append(log)

        # 计算统计
        total_executions = len(logs)
        success_count = sum(1 for l in logs if l['status'] == 'success')
        success_rate = success_count / total_executions if total_executions > 0 else 1.0
        avg_duration = sum(l['duration_seconds'] for l in logs) / total_executions if total_executions > 0 else 0

        # 获取下次执行时间（从配置解析）
        schedule_cfg_path = BASE / "config" / "hotspot_schedule.yaml"
        next_schedule = None
        if schedule_cfg_path.exists():
            schedule_cfg = yaml.safe_load(schedule_cfg_path.read_text('utf-8')) or {}
            categories = schedule_cfg.get('categories', {})
            for cat_id, cat_cfg in categories.items():
                if not cat_cfg.get('enabled', True):
                    continue
                schedule_expr = cat_cfg.get('schedule', '')
                if schedule_expr:
                    next_schedule = {
                        'category_id': cat_id,
                        'category_name': cat_cfg.get('name', cat_id),
                        'schedule': schedule_expr,
                    }
                    break

        conn.close()

        return jsonify({
            'logs': logs[:20],
            'stats': {
                'total_executions': total_executions,
                'success_rate': round(success_rate, 2),
                'avg_duration': round(avg_duration, 1),
            },
            'next_schedule': next_schedule,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/hotspot/execute', methods=['POST'])
def api_execute_hotspot():
    """手动执行热点检测"""
    data = request.json or {}
    category = data.get('category')
    hours = data.get('hours')
    max_results = data.get('max_results')
    keywords = data.get('keywords')
    provider = data.get('provider')
    pipeline = data.get('pipeline', 'v3')  # 默认使用 v3 新流水线

    if not category:
        return jsonify({'error': '缺少 category 参数'}), 400

    try:
        from scheduled_hotspot import run_detection, run_detection_v3

        print(f"[API] 执行热点检测: category={category}, pipeline={pipeline}, provider={provider}", flush=True)

        # 根据 pipeline 版本选择检测函数
        if pipeline == 'v3':
            result = run_detection_v3(
                category,
                hours=hours,
                max_results=max_results,
                provider=provider,
                quiet=False  # 输出进度
            )
        else:
            result = run_detection(
                category,
                hours=hours,
                max_results=max_results,
                keywords=keywords,
                provider=provider,
                quiet=False  # 输出进度
            )
        print(f"[API] 完成: {result.get('event_count', 0)} 个热点", flush=True)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/hotspot/execute-all', methods=['POST'])
def api_execute_hotspot_all():
    """批量执行所有分类的热点检测（v3 流水线）"""
    data = request.json or {}
    hours = data.get('hours')
    provider = data.get('provider')
    max_results = data.get('max_results', 20)
    pipeline = data.get('pipeline', 'v3')

    try:
        from scheduled_hotspot import run_all_categories, run_all_categories_v3

        if pipeline == 'v3':
            results = run_all_categories_v3(
                hours=hours,
                provider=provider,
                max_results=max_results,
                quiet=True
            )
        else:
            results = run_all_categories(conn=None)

        return jsonify({'results': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/hotspot/config/<category_id>', methods=['PUT'])
def api_update_hotspot_config(category_id):
    """更新分类配置"""
    data = request.json or {}

    try:
        import yaml
        from pathlib import Path
        config_path = Path(__file__).parent / 'config' / 'hotspot_schedule.yaml'

        cfg = yaml.safe_load(config_path.read_text('utf-8')) or {}
        categories = cfg.get('categories', {})

        if category_id not in categories:
            return jsonify({'error': f'分类不存在: {category_id}'}), 404

        # 更新配置
        if 'enabled' in data:
            categories[category_id]['enabled'] = data['enabled']
        if 'hours' in data:
            categories[category_id]['hours'] = data['hours']
        if 'max_results' in data:
            categories[category_id]['max_results'] = data['max_results']
        if 'keywords' in data:
            categories[category_id]['keywords'] = data['keywords']
        if 'schedule' in data:
            categories[category_id]['schedule'] = data['schedule']
        # 新增：阈值和起报数配置
        if 'similarity_threshold' in data:
            categories[category_id]['similarity_threshold'] = float(data['similarity_threshold'])
        if 'min_articles' in data:
            categories[category_id]['min_articles'] = int(data['min_articles'])

        cfg['categories'] = categories
        config_path.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False), 'utf-8')

        return jsonify({'ok': True, 'category': categories[category_id]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/hotspot/s-tier', methods=['GET'])
def api_get_s_tier_media():
    """获取S级媒体列表"""
    cfg = load_feeds_config()
    feeds = cfg.get('feeds', [])

    s_tier_list = []
    for i, f in enumerate(feeds):
        is_s = f.get('is_s_tier', False)
        s_tier_list.append({
            'index': i,
            'platform': f.get('platform', ''),
            'media_group': f.get('media_group', ''),
            'is_s_tier': is_s
        })

    return jsonify({'feeds': s_tier_list})


@app.route('/api/hotspot/s-tier/<int:idx>', methods=['PUT'])
def api_update_s_tier(idx):
    """更新S级媒体标记"""
    data = request.json or {}
    cfg = load_feeds_config()
    feeds = cfg.get('feeds', [])

    if idx < 0 or idx >= len(feeds):
        return jsonify({'error': '索引越界'}), 404

    feeds[idx]['is_s_tier'] = bool(data.get('is_s_tier', False))
    save_feeds_config(cfg)

    return jsonify({'ok': True, 'feed': feeds[idx]})


@app.route('/api/intelligence/write', methods=['POST'])
def api_write_report():
    data = request.json or {}
    ids = data.get('ids', [])
    prompt = data.get('prompt', '')
    provider = data.get('provider')
    if not ids:
        return jsonify({'error': '未选择任何文章'}), 400

    try:
        # 调试输出：看看前端发送的原始数据到底是什么
        print(f"[*] 撰写请求触发: IDs={ids}, Provider={provider}")
        report = intelligence.write_intelligence_report(ids, prompt, provider=provider)
        return jsonify({'report': report})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── 时间线 API ─────────────────────────────────────────────

@app.route('/api/timeline/create', methods=['POST'])
def api_timeline_create():
    """创建时间线"""
    data = request.json or {}
    article_ids = data.get('article_ids', [])
    provider = data.get('provider')
    search_days = data.get('search_days', 30)

    if not article_ids:
        return jsonify({'error': '未选择任何文章'}), 400

    try:
        print(f"[INFO] 创建时间线: article_ids={article_ids}")
        result = timeline.generate_timeline(
            article_ids, provider=provider, search_days=search_days
        )
        if 'error' in result:
            return jsonify({'error': result['error']}), 400
        return jsonify({
            'timeline_id': result['id'],
            'status': 'created',
            'timeline': result
        })
    except Exception as e:
        print(f"[ERROR] 创建时间线失败: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/list', methods=['GET'])
def api_timeline_list():
    """获取时间线列表"""
    status = request.args.get('status')
    limit = int(request.args.get('limit', 50))

    try:
        timelines = timeline.list_timelines(status=status, limit=limit)
        return jsonify({'timelines': timelines})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/<int:timeline_id>', methods=['GET'])
def api_timeline_get(timeline_id):
    """获取时间线详情"""
    try:
        tl = timeline.get_timeline(timeline_id)
        if not tl:
            return jsonify({'error': '时间线不存在'}), 404
        return jsonify(tl)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/<int:timeline_id>/track', methods=['POST'])
def api_timeline_track(timeline_id):
    """跟踪时间线最新进展"""
    data = request.json or {}
    provider = data.get('provider')

    try:
        result = timeline.track_timeline_updates(timeline_id, provider=provider)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/<int:timeline_id>/export', methods=['GET'])
def api_timeline_export(timeline_id):
    """导出时间线为 Markdown"""
    try:
        md = timeline.export_timeline_markdown(timeline_id)
        return jsonify({'markdown': md})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/<int:timeline_id>/status', methods=['PUT'])
def api_timeline_set_status(timeline_id):
    """设置时间线状态"""
    data = request.json or {}
    status = data.get('status', 'active')

    if status not in ('active', 'archived', 'completed'):
        return jsonify({'error': '无效的状态值'}), 400

    try:
        from store import set_timeline_status
        set_timeline_status(timeline_id, status)
        return jsonify({'ok': True, 'status': status})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/timeline/<int:timeline_id>', methods=['DELETE'])
def api_timeline_delete(timeline_id):
    """删除时间线"""
    try:
        from store import delete_timeline
        delete_timeline(timeline_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Publications API ─────────────────────────────────────────

@app.route('/api/publications', methods=['GET'])
def api_list_publications():
    """获取发布物列表"""
    status = request.args.get('status')
    pub_type = request.args.get('pub_type')
    limit = int(request.args.get('limit', 50))

    try:
        from store import list_publications
        pubs = list_publications(status=status, pub_type=pub_type, limit=limit)
        return jsonify({'publications': pubs, 'count': len(pubs)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications', methods=['POST'])
def api_create_publication():
    """创建发布物"""
    data = request.json or {}
    title = data.get('title', '').strip()
    pub_type = data.get('pub_type', 'daily_digest')

    if not title:
        return jsonify({'error': '标题不能为空'}), 400

    try:
        from store import create_publication
        pub_id = create_publication(
            title=title,
            pub_type=pub_type,
            template_id=data.get('template_id', ''),
            source_hotspots=data.get('source_hotspots', []),
            source_articles=data.get('source_articles', []),
            author=data.get('author', 'system')
        )
        return jsonify({'ok': True, 'id': pub_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications/<int:pub_id>', methods=['GET'])
def api_get_publication(pub_id):
    """获取发布物详情"""
    try:
        from store import get_publication
        pub = get_publication(pub_id)
        if not pub:
            return jsonify({'error': '发布物不存在'}), 404
        return jsonify(pub)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications/<int:pub_id>', methods=['PUT'])
def api_update_publication(pub_id):
    """更新发布物"""
    data = request.json or {}
    try:
        from store import get_publication, update_publication_content, update_publication_status

        pub = get_publication(pub_id)
        if not pub:
            return jsonify({'error': '发布物不存在'}), 404

        if 'content_md' in data:
            update_publication_content(
                pub_id=pub_id,
                content_md=data['content_md'],
                content_html=data.get('content_html', '')
            )

        if 'status' in data:
            update_publication_status(
                pub_id=pub_id,
                new_status=data['status'],
                reviewer=data.get('reviewer', '')
            )

        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications/<int:pub_id>', methods=['DELETE'])
def api_delete_publication(pub_id):
    """删除发布物"""
    try:
        from store import delete_publication
        delete_publication(pub_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications/<int:pub_id>/status', methods=['POST'])
def api_transition_publication_status(pub_id):
    """状态流转"""
    data = request.json or {}
    target_status = data.get('status', 'draft')
    reviewer = data.get('reviewer', '')
    note = data.get('note', '')

    try:
        from publication import PublicationManager
        mgr = PublicationManager()
        result = mgr.transition_status(pub_id, target_status, reviewer, note)
        mgr.close()
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications/<int:pub_id>/quality', methods=['GET'])
def api_check_publication_quality(pub_id):
    """检查发布物质量"""
    try:
        from quality_checker import QualityChecker
        checker = QualityChecker()
        result = checker.check_publication(pub_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications/<int:pub_id>/export', methods=['GET'])
def api_export_publication(pub_id):
    """导出发布物"""
    format = request.args.get('format', 'markdown')
    try:
        from store import get_publication
        pub = get_publication(pub_id)
        if not pub:
            return jsonify({'error': '发布物不存在'}), 404

        if format == 'markdown':
            return jsonify({'content': pub.get('content_md', ''), 'title': pub['title']})
        else:
            return jsonify({'content': pub.get('content_html', ''), 'title': pub['title']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications/<int:pub_id>/history', methods=['GET'])
def api_get_publication_history(pub_id):
    """获取发布物版本历史"""
    try:
        from store import get_publication_history
        history = get_publication_history(pub_id)
        return jsonify({'history': history})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/publications/auto-generate', methods=['POST'])
def api_auto_generate_publication():
    """从热点自动生成发布物"""
    data = request.json or {}
    pub_type = data.get('pub_type', 'daily_digest')
    hours = data.get('hours', 24)
    max_hotspots = data.get('max_hotspots', 5)

    try:
        from hotspot_detector import detect_hot_events
        from publication import PublicationManager
        from templates import TemplateManager
        from datetime import datetime, timezone, timedelta

        TZ_BJ = timezone(timedelta(hours=8))

        # 检测热点
        events = detect_hot_events(hours=hours, max_results=max_hotspots)

        if not events:
            return jsonify({'error': '未检测到热点事件'}), 400

        # 生成标题
        now = datetime.now(TZ_BJ)
        title = f"每日简报 - {now.strftime('%Y-%m-%d')}"

        # 收集文章ID
        article_ids = []
        for e in events:
            article_ids.extend([a['id'] for a in e.get('items', []) if a.get('id')])

        # 创建发布物
        mgr = PublicationManager()
        pub_id = mgr.create(
            title=title,
            pub_type=pub_type,
            source_hotspots=[e.get('cluster_id', i) for i, e in enumerate(events)],
            source_articles=article_ids[:50]  # 限制文章数量
        )

        # 生成初始内容
        content_parts = [f"# {title}\n\n## 热点概览\n"]
        for i, e in enumerate(events, 1):
            content_parts.append(f"### {i}. {e.get('title', '未知事件')}\n")
            content_parts.append(f"{e.get('summary', '')}\n\n")

        mgr.update_content(pub_id, '\n'.join(content_parts))
        mgr.close()

        return jsonify({
            'ok': True,
            'pub_id': pub_id,
            'hotspots_count': len(events),
            'articles_count': len(article_ids)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Search Schedule API ─────────────────────────────────────

@app.route('/api/search/schedule', methods=['GET'])
def api_list_search_schedule():
    """获取关键词监控列表"""
    try:
        from store import list_keyword_watches
        watches = list_keyword_watches(enabled_only=False)
        return jsonify({'watches': watches})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/schedule', methods=['POST'])
def api_create_search_schedule():
    """创建关键词监控"""
    data = request.json or {}
    keyword = data.get('keyword', '').strip()
    sources = data.get('sources', ['google_news'])
    interval = data.get('interval', 'daily')

    if not keyword:
        return jsonify({'error': '关键词不能为空'}), 400

    try:
        from store import create_keyword_watch
        watch_id = create_keyword_watch(keyword, sources, interval)
        return jsonify({'ok': True, 'id': watch_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/schedule/<int:watch_id>', methods=['PUT'])
def api_update_search_schedule(watch_id):
    """更新关键词监控"""
    data = request.json or {}
    try:
        from store import set_keyword_watch_enabled
        if 'enabled' in data:
            set_keyword_watch_enabled(watch_id, data['enabled'])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/schedule/<int:watch_id>', methods=['DELETE'])
def api_delete_search_schedule(watch_id):
    """删除关键词监控"""
    try:
        from store import delete_keyword_watch
        delete_keyword_watch(watch_id)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/schedule/<int:watch_id>/run', methods=['POST'])
def api_run_search_schedule(watch_id):
    """执行单个关键词监控"""
    try:
        from scheduled_search import ScheduledSearchManager
        mgr = ScheduledSearchManager()
        result = mgr.execute_watch(watch_id)
        mgr.close()
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/run-all', methods=['POST'])
def api_run_all_search_schedules():
    """执行所有到期的关键词监控"""
    try:
        from scheduled_search import ScheduledSearchManager
        mgr = ScheduledSearchManager()
        results = mgr.run_due_watches()
        mgr.close()
        return jsonify({'results': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/results', methods=['GET'])
def api_get_search_results():
    """获取外部搜索结果"""
    keyword = request.args.get('keyword')
    source_type = request.args.get('source_type')
    hours = int(request.args.get('hours', 24))
    limit = int(request.args.get('limit', 50))

    try:
        from store import query_external_articles
        from datetime import datetime, timezone, timedelta

        TZ_BJ = timezone(timedelta(hours=8))
        now = datetime.now(TZ_BJ)
        start = (now - timedelta(hours=hours)).isoformat()

        results = query_external_articles(
            keyword=keyword,
            source_type=source_type,
            start=start,
            limit=limit
        )
        return jsonify({'results': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/keyword', methods=['POST'])
def api_search_keyword():
    """一次性关键词搜索"""
    data = request.json or {}
    keyword = data.get('keyword', '').strip()
    sources = data.get('sources', ['google_news'])
    max_results = data.get('max_results', 20)

    if not keyword:
        return jsonify({'error': '关键词不能为空'}), 400

    try:
        from scheduled_search import ScheduledSearchManager
        mgr = ScheduledSearchManager()
        result = mgr.search_and_store(keyword, sources, max_results)
        mgr.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Foreign Media Search API ─────────────────────────────────────

@app.route('/api/external-sources', methods=['GET'])
def api_get_external_sources():
    """获取外部搜索源配置列表（按分类分组）"""
    try:
        cfg = load_feeds_config()
        sources = cfg.get('external_sources', [])

        # 按分类分组
        grouped = {}
        for src in sources:
            category = src.get('category', '其他')
            if category not in grouped:
                grouped[category] = []
            # 隐藏敏感的 API key
            safe_src = {k: v for k, v in src.items() if k != 'config'}
            safe_src['config'] = {}
            if 'config' in src:
                for k, v in src['config'].items():
                    if 'api_key' in k.lower() or 'key' in k.lower():
                        safe_src['config'][k] = '******' if v else ''
                    else:
                        safe_src['config'][k] = v
            grouped[category].append(safe_src)

        return jsonify({
            'sources': sources,
            'grouped': grouped,
            'categories': list(grouped.keys())
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/external-sources/<name>', methods=['PUT'])
def api_update_external_source(name):
    """更新外部搜索源配置"""
    data = request.json or {}
    try:
        cfg = load_feeds_config()
        sources = cfg.get('external_sources', [])

        for src in sources:
            if src.get('name') == name:
                if 'enabled' in data:
                    src['enabled'] = data['enabled']
                if 'description' in data:
                    src['description'] = data['description']
                if 'config' in data:
                    if 'config' not in src:
                        src['config'] = {}
                    src['config'].update(data['config'])
                save_feeds_config(cfg)
                return jsonify({'ok': True, 'source': src})

        return jsonify({'error': f'未找到源: {name}'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Ollama API ──────────────────────────────────────────

@app.route('/api/ollama/status', methods=['GET'])
def api_ollama_status():
    """检查本地 Ollama 状态"""
    try:
        from ollama_helper import OllamaClient
        client = OllamaClient()
        available = client.is_available()
        models = client.list_models() if available else []
        
        # 从配置中获取推荐模型
        llm_cfg = _load_llm_cfg()
        config_model = llm_cfg.get('llm', {}).get('model', '')
        
        return jsonify({
            'available': available,
            'models': models,
            'current_model': config_model,
            'config_models': [
                {'name': 'qwen2.5:7b', 'description': '推荐：中文能力强'},
                {'name': 'llama3.1:8b', 'description': '均衡性好'},
                {'name': 'gemma2:9b', 'description': 'Google 最佳开源模型'}
            ]
        })
    except Exception as e:
        return jsonify({'available': False, 'error': str(e)}), 200


@app.route('/api/foreign-media/search', methods=['POST'])
def api_foreign_media_search():
    """外媒搜索（多源聚合，流式响应，支持关键词扩展）"""
    data = request.json or {}
    keyword = data.get('keyword', '').strip() or data.get('keywords', '').strip()
    sources = data.get('sources', ['google_news'])
    max_results = data.get('max_results', 20)
    use_keyword_expansion = data.get('expand_keywords', True)  # 默认启用关键词扩展

    if not keyword:
        return jsonify({'error': '关键词不能为空'}), 400

    def generate():
        try:
            import sys
            sys.path.insert(0, str(SCRIPTS))
            from external_fetcher import UnifiedSearcher
            from ollama_helper import OllamaClient

            searcher = UnifiedSearcher()
            ollama = OllamaClient()
            ollama_ready = ollama.is_available()

            llm_cfg = _load_llm_cfg()
            ollama_model = llm_cfg.get('llm', {}).get('model', 'qwen2.5:7b')

            # 关键词扩展（如果启用且 Ollama 可用）
            search_keywords = [keyword]
            if use_keyword_expansion and ollama_ready:
                try:
                    expanded = ollama.smart_search_keywords(ollama_model, keyword, max_keywords=8)
                    if expanded and len(expanded) > 1:
                        search_keywords = expanded[:5]  # 最多使用 5 个扩展关键词
                        yield json.dumps({
                            'type': 'keywords_expanded',
                            'original': keyword,
                            'expanded': search_keywords
                        }, ensure_ascii=False) + '\n'
                except Exception as e:
                    print(f"[WARN] 关键词扩展失败: {e}")

            all_articles = []
            url_hashes = set()
            import hashlib

            for src in sources:
                # 1. 告知前端开始处理该源
                yield json.dumps({'type': 'source_start', 'source': src}, ensure_ascii=False) + '\n'

                try:
                    fetcher = searcher.get_fetcher(src)
                    if not fetcher:
                        yield json.dumps({'type': 'source_result', 'source': src, 'count': 0, 'error': '未找到抓取器'}, ensure_ascii=False) + '\n'
                        continue

                    # 使用扩展的关键词搜索
                    source_results = []
                    for kw in search_keywords:
                        try:
                            results = fetcher.search(kw, max_results)
                            source_results.extend(results)
                        except Exception as kw_err:
                            print(f"[WARN] 搜索 '{kw}' 失败: {kw_err}")

                    # 去重并保存
                    unique_results = []
                    for r in source_results:
                        u_hash = hashlib.sha1(r.get('url', '').encode()).hexdigest()[:16]
                        if u_hash not in url_hashes:
                            url_hashes.add(u_hash)
                            # 正确设置元数据
                            r['type'] = src
                            # 兼容前端 source 字段 (external_fetcher 返回 platform)
                            r['source'] = r.get('platform', r.get('source', 'Unknown'))
                            unique_results.append(r)
                            all_articles.append(r)
                    
                    # 告知结果
                    yield json.dumps({
                        'type': 'source_result', 
                        'source': src, 
                        'count': len(unique_results), 
                        'articles': unique_results
                    }, ensure_ascii=False) + '\n'
                    
                except Exception as e:
                    yield json.dumps({'type': 'source_result', 'source': src, 'count': 0, 'error': str(e)}, ensure_ascii=False) + '\n'

            # 按时间排序总结果
            all_articles.sort(key=lambda x: x.get('published', ''), reverse=True)

            # 2. 立场分析 (如果有 articles 且 Ollama 可用)
            stance_analysis = None
            if all_articles and ollama_ready:
                try:
                    analysis_res = ollama.analyze_stance(ollama_model, keyword, all_articles)
                    if 'response' in analysis_res:
                        resp_text = analysis_res['response'].strip()
                        # 尝试清理 JSON 标记
                        if resp_text.startswith('```json'):
                            resp_text = resp_text.strip('```json').strip('```').strip()
                        elif resp_text.startswith('```'):
                            resp_text = resp_text.strip('```').strip()
                            
                        try:
                            stance_analysis = json.loads(resp_text)
                        except:
                            # 如果解析失败，可能是 LLM 没按格式输出，设为包含错误提示的对象
                            stance_analysis = {"error": "AI 输出解析失败", "raw": resp_text}
                except:
                    pass

            # 3. 最终汇总结果
            yield json.dumps({
                'type': 'done',
                'total_count': len(all_articles),
                'articles': all_articles,
                'stance_analysis': stance_analysis
            }, ensure_ascii=False) + '\n'

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False) + '\n'

    return Response(generate(), mimetype='application/json')


@app.route('/api/ollama/keywords', methods=['POST'])
def api_ollama_keywords():
    """关键词扩展（用于搜索前预览）"""
    data = request.json or {}
    keyword = data.get('keyword', '').strip()
    max_keywords = data.get('max_keywords', 10)

    if not keyword:
        return jsonify({'error': '关键词不能为空'}), 400

    try:
        import sys
        sys.path.insert(0, str(SCRIPTS))
        from ollama_helper import OllamaClient

        ollama = OllamaClient()
        if not ollama.is_available():
            return jsonify({'keywords': [keyword], 'available': False})

        llm_cfg = _load_llm_cfg()
        model = llm_cfg.get('llm', {}).get('model', 'qwen2.5:7b')

        keywords = ollama.smart_search_keywords(model, keyword, max_keywords=max_keywords)
        return jsonify({
            'keywords': keywords,
            'original': keyword,
            'available': True
        })
    except Exception as e:
        return jsonify({'keywords': [keyword], 'error': str(e)})


@app.route('/api/ollama/translate', methods=['POST'])
def api_ollama_translate():
    """批量翻译标题"""
    data = request.json or {}
    titles = data.get('titles', [])
    model = data.get('model')
    
    if not titles:
        return jsonify({'translations': []})
        
    try:
        from ollama_helper import OllamaClient
        ollama = OllamaClient()
        
        # 如果没有指定模型，尝试从配置读取
        if not model:
            cfg = _load_llm_cfg()
            model = cfg.get('llm', {}).get('model', 'qwen2.5:7b')
            
        res = ollama.translate_titles(model, titles)
        return jsonify(res)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── 启动 ─────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════
# API v2: 统一数据接口（供 Playground 和外部应用调用）
# ══════════════════════════════════════════════════════════════════

@app.route('/api/v2/feed', methods=['GET'])
def api_v2_feed():
    """全量内容获取（RSS + 外部源合并去重）"""
    import hashlib as _hl
    now = datetime.now(TZ_BJ)

    hours = request.args.get('hours', type=float)
    start = request.args.get('start')
    end = request.args.get('end')
    limit = request.args.get('limit', 500, type=int)

    if start:
        q_start, q_end = start, end or now.isoformat()
    elif hours:
        q_start = (now - timedelta(hours=hours)).isoformat()
        q_end = now.isoformat()
    else:
        q_start = (now - timedelta(hours=6)).isoformat()
        q_end = now.isoformat()

    conn = get_conn()
    items = query_by_time(q_start, q_end, limit=limit, conn=conn)
    total = count_articles(start=q_start, end=q_end, conn=conn)
    conn.close()

    # 外部全量情报补充 (Google News)
    try:
        searcher = UnifiedSearcher()
        ext_results = searcher.search("", sources=['google_news'], max_results=50)
        items.extend(ext_results)
        # 自动入库
        valid_ext = [r for r in ext_results if not is_chinese_media(r.get('platform', r.get('source', '')), r.get('url', ''))]
        if valid_ext:
            upsert_external_articles(valid_ext, keyword_match='feed_supplement')
    except Exception as e:
        print(f"[WARN] 外部源获取失败: {e}")

    seen = set()
    unique = []
    internal = {'url_hash', 'title_hash', 'created_at'}
    for item in items:
        # 统一格式映射
        p = item.get('platform', item.get('source', ''))
        u = item.get('url', '')
        
        # 严格过滤中国媒体
        if is_chinese_media(p, u):
            continue
            
        uh = _hl.sha1(u.strip().lower().encode()).hexdigest()[:16]
        if uh not in seen:
            seen.add(uh)
            # 转换对象 (如果来自 Row, 转为 dict)
            d = dict(item) if not isinstance(item, dict) else item
            unique.append({k: v for k, v in d.items() if k not in internal and v not in ('', None)})

    return jsonify({
        'api': 'feed',
        'query': {'start': q_start, 'end': q_end, 'generated_at': now.isoformat()},
        'count': len(unique),
        'total': total,
        'items': unique
    })


@app.route('/api/v2/search', methods=['GET'])
def api_v2_search():
    """关键字+时间范围搜索"""
    now = datetime.now(TZ_BJ)
    keyword = request.args.get('keyword', '')
    hours = request.args.get('hours', type=float)
    start = request.args.get('start')
    end = request.args.get('end')
    media = request.args.get('media')
    country = request.args.get('country')
    limit = request.args.get('limit', 200, type=int)

    if not keyword:
        return jsonify({'error': '必须提供 keyword 参数'}), 400

    if start:
        q_start, q_end = start, end or now.isoformat()
    elif hours:
        q_start = (now - timedelta(hours=hours)).isoformat()
        q_end = now.isoformat()
    else:
        q_start, q_end = None, None

    conn = get_conn()
    items = query_by_keyword(
        keyword=keyword, start=q_start, end=q_end,
        media_group=media or None, country=country or None,
        limit=limit, conn=conn
    )
    conn.close()

    internal = {'url_hash', 'title_hash', 'created_at'}
    clean = [{k: v for k, v in dict(i).items() if k not in internal and v not in ('', None)} for i in items]

    return jsonify({
        'api': 'search',
        'query': {'keyword': keyword, 'start': q_start, 'end': q_end, 'generated_at': now.isoformat()},
        'count': len(clean),
        'items': clean
    })


@app.route('/api/v2/hotspot', methods=['GET'])
def api_v2_hotspot():
    """自动热点捕获"""
    sys.path.insert(0, str(SCRIPTS))
    from hotspot_detector import detect_hot_events
    hours = request.args.get('hours', 24, type=float)
    max_r = request.args.get('max', 15, type=int)
    keyword = request.args.get('keyword', '')
    print(f"[DEBUG] Hotspot API request: keyword='{keyword}', hours={hours}")
    start = request.args.get('start')
    end = request.args.get('end')
    no_ext = request.args.get('no_external', 'false').lower() == 'true'

    events = detect_hot_events(
        hours=int(hours),
        start_time=start or None,
        end_time=end or None,
        max_results=max_r,
        keyword=keyword if keyword.strip() else None
    )

    result_events = []
    for rank, ev in enumerate(events, 1):
        result_events.append({
            'rank': rank,
            'title': ev.get('title', ''),
            'score': ev.get('score', 0),
            'media_count': len(ev.get('platforms', [])),
            'article_count': ev.get('count', 0),
            'platforms': ev.get('platforms', []),
            'tags': ev.get('tags', []),
            'is_china_related': ev.get('is_china_related', False),
            'articles': [
                {
                    'id': a.get('id'),
                    'title': a.get('title', ''),
                    'url': a.get('url', ''),
                    'platform': a.get('platform', ''),
                    'published': a.get('published', ''),
                    'image': a.get('image', ''),
                    'summary': a.get('summary', '')
                }
                for a in ev.get('items', [])
            ]
        })

    return jsonify({
        'api': 'hotspot',
        'query': {'hours': hours, 'max': max_r, 'generated_at': datetime.now(TZ_BJ).isoformat()},
        'count': len(result_events),
        'events': result_events
    })


@app.route('/api/v2/research', methods=['POST'])
def api_v2_research():
    """深度研究 — 多源时间线"""
    sys.path.insert(0, str(SCRIPTS))
    from timeline import (
        generate_timeline, search_related_in_rss,
        extract_timeline_keywords, enrich_article_content,
        call_llm_for_timeline
    )
    data = request.get_json(force=True) or {}
    keyword = data.get('keyword', '')
    article_ids = data.get('article_ids', [])
    hours = data.get('hours', 72)
    days = data.get('days', 30)
    # 新增模式：deep_research
    mode = data.get('mode', 'local') 
    if not mode and data.get('deep_research'):
         mode = 'deep_research'
         
    now = datetime.now(TZ_BJ)

    # 1. 如果提供了具体文章 ID，按原逻辑生成（可能涉及本地扩充）
    if article_ids:
        result = generate_timeline(article_ids, search_days=days)
        return jsonify({'api': 'research', 'query': {'article_ids': article_ids}, 'timeline': result})

    # 2. 如果提供了关键字且是 deep_research 模式
    if keyword and mode == 'deep_research':
        from external_fetcher import UnifiedSearcher
        print(f"[INFO] 正在启动 AI 深度研究: {keyword}")
        
        # 使用 AI 搜索引擎进行外部搜索 (优先使用有效 Key 的源，兜底使用 Google News RSS)
        searcher = UnifiedSearcher()
        ext_sources = ['tavily', 'perplexity', 'brave', 'google_news', 'bing_news']
        ext_articles = searcher.search(keyword, sources=ext_sources, max_results=15)
        
        # 转换外部文章格式为时间线引擎兼容格式
        all_arts = []
        for a in ext_articles:
             all_arts.append({
                 'title': a.get('title', ''),
                 'url': a.get('url', ''),
                 'platform': a.get('platform', a.get('source_type', '')),
                 'published': a.get('published', ''),
                 'summary': a.get('summary', ''),
                 'content': '', # 遵循用户建议：对于外部源不强制抓取全文，减少耗时
                 'id': None     # 标记为外部文章
             })
             
        if not all_arts:
             return jsonify({'api': 'research', 'error': f'AI 搜索未找到与 "{keyword}" 相关的外部内容'})

        # 提取关键词在本地 RSS 库中也捞一下，做补充
        conn = get_conn()
        local_arts = query_by_keyword(keyword=keyword, start=(now - timedelta(days=days)).isoformat(), limit=20, conn=conn)
        conn.close()
        
        # 合并 (去重逻辑在 UnifiedSearcher 中已部分实现，这里简单合并)
        all_arts.extend(local_arts)
        
        # 生成时间线
        timeline_data = call_llm_for_timeline(all_arts)
        
        # --- 持久化到数据库 ---
        try:
            from scripts.timeline import create_timeline, update_timeline_events
            t_id = create_timeline(
                title=timeline_data.get('title', f'{keyword} AI 深度研究'),
                keywords=timeline_data.get('keywords', [keyword]),
                source_article_ids=[], # 外部文章没有本地 ID
                summary=timeline_data.get('summary', '')
            )
            # 为事件附带溯源信息并保存
            events = timeline_data.get('events', [])
            for evt in events:
                # 简单匹配：这里直接标记为 AI 深度研究来源
                evt['source_type'] = 'deep_research'
            update_timeline_events(t_id, events)
            timeline_data['id'] = t_id
        except Exception as e:
            print(f"[WARN] 深度研究时间线保存失败: {e}")

        # 确保包含溯源链接
        timeline_data['source_articles'] = [
            {'title': a.get('title', ''), 'url': a.get('url', ''),
             'platform': a.get('platform', ''), 'published': a.get('published', '')}
            for a in all_arts[:40]
        ]
        
        return jsonify({
            'api': 'research', 
            'mode': 'deep_research',
            'query': {'keyword': keyword}, 
            'timeline': timeline_data
        })

    # 3. 原有的本地关键字搜索逻辑
    if keyword:
        start = (now - timedelta(hours=hours)).isoformat()
        conn = get_conn()
        articles = query_by_keyword(keyword=keyword, start=start, end=now.isoformat(), limit=50, conn=conn)
        if not articles:
            conn.close()
            return jsonify({'api': 'research', 'error': f'未找到与 "{keyword}" 相关的文章'})

        keywords = extract_timeline_keywords(articles)
        related = search_related_in_rss(keywords, time_range_days=days,
                                         exclude_ids=[a['id'] for a in articles if a.get('id')], conn=conn)
        conn.close()
        all_arts = articles + related
        all_arts = enrich_article_content(all_arts)
        timeline_data = call_llm_for_timeline(all_arts)
        timeline_data['source_articles'] = [
            {'id': a.get('id'), 'title': a.get('title', ''), 'url': a.get('url', ''),
             'platform': a.get('platform', ''), 'published': a.get('published', '')}
            for a in all_arts[:30]
        ]
        return jsonify({'api': 'research', 'query': {'keyword': keyword, 'hours': hours}, 'timeline': timeline_data})

    return jsonify({'error': '必须提供 keyword 或 article_ids'}), 400


@app.route('/api/v2/value', methods=['POST'])
def api_v2_value():
    """价值分析 — 专家评估"""
    import re as _re
    sys.path.insert(0, str(SCRIPTS))
    data = request.get_json(force=True) or {}
    article_ids = data.get('article_ids', [])
    if data.get('article_id'):
        article_ids = [int(data['article_id'])]

    if not article_ids:
        return jsonify({'error': '必须提供 article_id 或 article_ids'}), 400

    conn = get_conn()
    from store import get_articles_by_ids as _get_arts
    articles = _get_arts([int(x) for x in article_ids], conn)
    conn.close()

    if not articles:
        return jsonify({'api': 'value', 'error': '未找到指定文章'})

    from llm_tagger import _call_deepseek, load_llm_config
    cfg = load_llm_config()
    results = []

    for art in articles:
        body = (art.get('content') or art.get('summary') or art.get('title', ''))[:2000]
        prompt = f"""你是一位资深的国际关系研究员和媒体分析专家。请从专家角度评估以下新闻文章的价值。

文章：{art.get('title', '')}
来源：{art.get('platform', '')}
时间：{art.get('published', '')}
内容：{body}

评估维度（每项 1-10 分）：
1. news_value（新闻价值）
2. research_value（研究价值）
3. publication_value（发表价值）
4. policy_value（政策价值）

输出格式（严格 JSON）：
{{"scores": {{"news_value": 8, "research_value": 7, "publication_value": 6, "policy_value": 9, "overall": 7.5}}, "assessment": "综合评估", "recommended_outlets": ["刊物1"], "research_angles": ["角度1"], "key_findings": ["发现1"]}}
只输出 JSON。"""

        assessment = None
        if cfg and cfg.get('llm', {}).get('enabled'):
            try:
                raw = _call_deepseek([{"role": "system", "content": "你是资深媒体分析专家。"}, {"role": "user", "content": prompt}], cfg)
                if raw:
                    cleaned = _re.sub(r'```json\n?|\n?```', '', raw).strip()
                    assessment = json.loads(cleaned)
            except Exception:
                pass

        if not assessment:
            assessment = {'scores': {'news_value': 5, 'research_value': 5, 'publication_value': 5, 'policy_value': 5, 'overall': 5.0},
                          'assessment': '本地算法评估（LLM 不可用）', 'recommended_outlets': [], 'research_angles': [], 'key_findings': []}

        results.append({
            'article': {'id': art.get('id'), 'title': art.get('title', ''), 'url': art.get('url', ''),
                        'platform': art.get('platform', ''), 'published': art.get('published', '')},
            **assessment
        })

    return jsonify({
        'api': 'value',
        'query': {'article_ids': article_ids, 'generated_at': datetime.now(TZ_BJ).isoformat()},
        'count': len(results),
        'assessments': results
    })


@app.route('/api/v2/search/unified', methods=['GET'])
def api_v2_search_unified():
    """
    统一搜索 - 本地数据库 + 外部搜索引擎并行搜索

    参数:
        - keyword: 搜索关键词 (必填)
        - hours: 时间窗口 (小时，默认6)
        - sources: 外部引擎列表 (逗号分隔，如 'google_news,bing_news,newsapi')
        - limit: 每类最大结果数 (默认50)
        - country: 国家过滤 (可选)

    返回:
        - main_media: 本地数据库结果 (主媒体)
        - web_supplement: 外部引擎结果 (Web补充)
    """
    sys.path.insert(0, str(SCRIPTS))
    from external_fetcher import UnifiedSearcher
    from concurrent.futures import ThreadPoolExecutor, as_completed

    now = datetime.now(TZ_BJ)
    keyword = request.args.get('keyword', '')
    hours = request.args.get('hours', default=6, type=float)
    sources_str = request.args.get('sources', '')
    limit = request.args.get('limit', 50, type=int)
    country = request.args.get('country', '')

    if not keyword:
        return jsonify({'error': '必须提供 keyword 参数'}), 400

    # 解析外部引擎列表
    external_sources = []
    if sources_str:
        external_sources = [s.strip() for s in sources_str.split(',') if s.strip()]

    # 计算时间范围
    q_start = (now - timedelta(hours=hours)).isoformat()
    q_end = now.isoformat()

    # === 并行搜索 ===
    main_media_results = []
    web_supplement_results = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}

        # 1. 本地数据库搜索
        def search_local():
            conn = get_conn()
            items = query_by_keyword(
                keyword=keyword, start=q_start, end=q_end,
                country=country or None, limit=limit, conn=conn
            )
            conn.close()
            return items

        futures[executor.submit(search_local)] = 'local'

        # 2. 外部引擎搜索
        if external_sources:
            searcher = UnifiedSearcher()

            def search_external(source):
                return searcher.search(keyword, sources=[source], max_results=limit, hours=hours)

            for source in external_sources:
                futures[executor.submit(search_external, source)] = source

    # 收集结果
    for future in as_completed(futures):
        source_type = futures[future]
        try:
            results = future.result()

            if source_type == 'local':
                # 本地数据库结果
                for item in results:
                    clean_item = {k: v for k, v in dict(item).items()
                                  if k not in ('url_hash', 'title_hash', 'created_at')
                                  and v not in ('', None)}
                    clean_item['source_category'] = 'main_media'
                    main_media_results.append(clean_item)
            else:
                # 外部引擎结果
                for item in results:
                    item['source_category'] = 'web_supplement'
                    item['engine'] = source_type
                    web_supplement_results.append(item)

        except Exception as e:
            print(f"[WARN] {source_type} 搜索失败: {e}")

    # 按时间排序
    main_media_results.sort(key=lambda x: x.get('published', ''), reverse=True)
    web_supplement_results.sort(key=lambda x: x.get('published', ''), reverse=True)

    # 过滤无效结果：没有 URL 或 URL 无效
    def has_valid_url(item: dict) -> bool:
        """检查是否有有效的 URL"""
        url = item.get('url', '')
        if not url:
            return False
        # 过滤掉 Google/Bing 内部聚合页面
        invalid_prefixes = [
            'https://news.google.com/',
            'https://www.google.com/',
            'https://www.bing.com/',
            'https://bing.com/'
        ]
        for prefix in invalid_prefixes:
            if url.startswith(prefix):
                return False
        return True

    # 先过滤无效 URL
    web_supplement_results = [item for item in web_supplement_results if has_valid_url(item)]

    # 外部引擎结果按时间段过滤（剔除超出时间窗口的结果）
    def is_within_time_window(published_str: str) -> bool:
        """检查发布时间是否在时间窗口内"""
        if not published_str:
            # 无时间信息，对于短时间窗口（<24h）默认过滤掉
            # 因为这些结果可能是旧文章
            return hours >= 24
        try:
            # 解析 ISO 格式时间
            pub_dt = datetime.fromisoformat(published_str.replace('Z', '+00:00'))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=TZ_BJ)
            else:
                pub_dt = pub_dt.astimezone(TZ_BJ)
            # 检查是否在时间窗口内
            return pub_dt >= (now - timedelta(hours=hours))
        except Exception:
            # 解析失败，对于短时间窗口默认过滤
            return hours >= 24

    # 过滤外部结果
    filtered_web_results = [item for item in web_supplement_results
                            if is_within_time_window(item.get('published', ''))]
    filter_removed_count = len(web_supplement_results) - len(filtered_web_results)
    if filter_removed_count > 0:
        print(f"[INFO] 外部引擎时间过滤: 移除 {filter_removed_count} 条超出时间窗口的结果")

    # 收集关键字优化信息
    keyword_optimization = {}
    if external_sources:
        try:
            sys.path.insert(0, str(SCRIPTS))
            from search_keyword_optimizer import optimize_search_query
            for source in external_sources:
                opt = optimize_search_query(keyword, hours=hours, engine=source, use_llm=False)
                keyword_optimization[source] = {
                    'original': opt.get('original_keyword'),
                    'optimized': opt.get('optimized_keyword'),
                    'engine_type': opt.get('engine_type')
                }
        except Exception as e:
            print(f"[WARN] 关键字优化信息获取失败: {e}")

    return jsonify({
        'api': 'search/unified',
        'query': {
            'keyword': keyword,
            'hours': hours,
            'sources': external_sources,
            'generated_at': now.isoformat(),
            'keyword_optimization': keyword_optimization
        },
        'main_media': {
            'count': len(main_media_results),
            'items': main_media_results[:limit]
        },
        'web_supplement': {
            'count': len(filtered_web_results),
            'items': filtered_web_results[:limit],
            'filtered_out': filter_removed_count  # 显示被过滤掉的数量
        },
        'total_count': len(main_media_results) + len(filtered_web_results)
    })


@app.route('/api/v2/external-engines', methods=['GET'])
def api_v2_external_engines():
    """
    获取可用外部搜索引擎列表

    返回每个引擎的:
        - name: 名称
        - type: 类型
        - category: 分类
        - has_key: 是否已配置密钥
        - enabled: 是否启用
    """
    sys.path.insert(0, str(SCRIPTS))
    from external_fetcher import UnifiedSearcher, get_api_key

    cfg = load_feeds_config()
    sources = cfg.get('external_sources', [])

    engines = []
    for src in sources:
        name = src.get('name', '')
        # 检查是否有密钥
        # 将名称转换为查找格式 (空格转下划线)
        lookup_name = name.lower().replace(' ', '_')
        has_key = bool(get_api_key(lookup_name))

        engines.append({
            'name': name,
            'type': src.get('type', ''),
            'category': src.get('category', ''),
            'has_key': has_key,
            'enabled': src.get('enabled', False),
            'description': src.get('description', '')
        })

    return jsonify({
        'engines': engines,
        'count': len(engines)
    })


def main():

    ap = argparse.ArgumentParser(description='RSS 管理面板')
    ap.add_argument('--port', type=int, default=5001, help='端口 (默认 5001)')
    ap.add_argument('--interval', type=int, default=None, help='自动抓取间隔/分钟 (默认配置文件或60)')
    ap.add_argument('--no-scheduler', action='store_true', help='不启动后台定时抓取')
    args = ap.parse_args()

    # 初始化数据库
    conn = get_conn()
    init_db(conn)
    conn.close()

    # 优先使用配置文件的 interval
    cfg = load_feeds_config()
    interval = args.interval or cfg.get('settings', {}).get('interval_min', 60)
    auto_fetch = cfg.get('settings', {}).get('auto_fetch', False)

    # 启动后台调度: 由前端配置或者参数决定
    if not args.no_scheduler and auto_fetch:
        start_scheduler(interval)
        print(f'[*] 后台调度已启动，每 {interval} 分钟自动抓取')
    else:
        print('[*] 后台调度未自动启动 (已设置为开机不自动抓取)')

    print(f'[*] 管理面板: http://localhost:{args.port}')
    app.run(host='0.0.0.0', port=args.port, debug=False)


if __name__ == '__main__':
    main()
