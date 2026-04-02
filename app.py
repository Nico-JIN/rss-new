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

from flask import Flask, jsonify, request, send_from_directory, abort
import yaml

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
    log_fetch_start, log_fetch_end, get_fetch_logs, get_latest_fetch_status
)
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
                    'articles_new': stats_data.get('final_count', 0),
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
        # 默认今天
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        items = query_by_time(
            today_start, now.isoformat(),
            media_group=media or None, platform=platform or None, country=country or None,
            limit=limit, offset=offset_val, conn=conn)
        total = count_articles(
            start=today_start, end=now.isoformat(),
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


# ── 启动 ─────────────────────────────────────────────────

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
