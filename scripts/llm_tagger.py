#!/usr/bin/env python3
"""LLM 语义多标签批量打标模块 — 使用 DeepSeek (OpenAI 兼容接口)

职责：接收文章列表 + topics 配置 → 批量调用 LLM → 返回/写入每篇文章命中的标签
设计原则：
  1. 所有异常静默降级，绝不阻断主流程
  2. 批量打包请求，极致节省 Token
  3. 仅依赖 requests（项目已有）
"""

import json
import re
import sys
import threading
from pathlib import Path

import requests
import yaml

progress_lock = threading.Lock()

BASE = Path(__file__).parent.parent
LLM_CFG_PATH = BASE / "config" / "llm_topics.yaml"


def load_llm_config() -> dict:
    """加载 LLM 配置，失败返回空字典"""
    if not LLM_CFG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(LLM_CFG_PATH.read_text('utf-8')) or {}
    except Exception:
        return {}


def save_llm_config(cfg: dict):
    """保存 LLM 配置到 YAML"""
    LLM_CFG_PATH.write_text(
        yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
        'utf-8'
    )


def _build_system_prompt(topics: list[dict]) -> str:
    """构建系统提示词"""
    topic_defs = "\n".join(
        f"  - **{t['name']}**: {t['description']}"
        for t in topics
    )
    return f"""你是一个资深的国际新闻分析师。你的任务是对新闻进行语义分类。

根据以下主题定义，判断每条新闻属于哪些主题类别：

{topic_defs}

## 输出要求
- 严格返回有效的 json 格式数据
- 返回一个 json 数组，数组长度必须等于输入新闻条数
- 每个元素是该条新闻命中的主题名称数组（可以为空数组，也可以多选）
- 不要输出任何多余文字，只输出 json 数据

## 示例
输入 3 条新闻，输出 json 格式如下：
[["中国言论", "大国博弈"], [], ["国际热点"]]"""


def _build_user_prompt(articles: list[dict]) -> str:
    """构建用户消息：打包文章标题+摘要"""
    lines = []
    for i, a in enumerate(articles):
        title = a.get('title', '')
        summary = a.get('summary', '')[:100]
        platform = a.get('platform', '')
        line = f"{i+1}. [{platform}] {title}"
        if summary:
            line += f" — {summary}"
        lines.append(line)
    return f"请对以下 {len(articles)} 条新闻进行分类：\n\n" + "\n".join(lines)


def _call_llm_api(messages: list[dict], cfg: dict) -> str | None:
    """通用 LLM 调用路由，支持多模型动态分发"""
    llm_cfg = cfg.get('llm', {})
    provider = llm_cfg.get('provider', 'deepseek')

    # 定义支持 OpenAI 协议的新模型列表
    openai_compat_models = ['qwen3.5-plus', 'glm-5', 'glm-4.7', 'kimi-k2.5', 'MiniMax-M2.5']

    if provider == 'volcengine':
        return _call_volcengine_ark(messages, llm_cfg)
    elif provider in openai_compat_models:
        # 如果选择的是新模型，调用聚合器配置
        agg_cfg = llm_cfg.get('writing_aggregator', {})
        # 兜底：如果聚合器配置不存在，则尝试使用外层 llm_cfg
        final_cfg = agg_cfg if agg_cfg else llm_cfg
        return _call_openai_compatible_api(messages, final_cfg, model_override=provider)
    else:
        # 默认 DeepSeek 或其他透传逻辑
        return _call_openai_compatible_api(messages, llm_cfg)

def _call_openai_compatible_api(messages: list[dict], llm: dict, model_override: str = None) -> str | None:
    """标准 OpenAI 兼容调用 (支持 DeepSeek, Qwen, GLM, Kimi 等)"""
    api_key = llm.get('api_key', '')
    base_url = llm.get('base_url', 'https://api.deepseek.com').rstrip('/')
    # 逻辑修正：如果 base_url 已经包含 /v1，则不重复追加，防止 404
    if not base_url.endswith('/v1'):
        url = f"{base_url}/v1/chat/completions"
    else:
        url = f"{base_url}/chat/completions"
        
    model = model_override or llm.get('model', 'deepseek-chat')
    temperature = llm.get('temperature', 0.1)

    if not api_key or api_key.startswith('sk-YOUR'):
        print(f'[WARN] {model} API Key 未配置', file=sys.stderr)
        return None

    headers = { "Authorization": f"Bearer {api_key}", "Content-Type": "application/json" }
    payload = {
        "model": model, "messages": messages, "temperature": temperature
    }

    # 针对 Kimi 等对 JSON 模式要求极严的模型做动态判定
    # 只有当 prompt 中明确包含 'json' 且任务需要结构化输出时才开启 json_object
    prompt_str = str(messages).lower()
    if 'json' in prompt_str and ('tagging' in prompt_str or 'events' in prompt_str or '主题' in prompt_str):
        payload["response_format"] = {"type": "json_object"}

    timeout = llm.get('timeout', 120)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
        print(f'[WARN] {model} API 返回 {resp.status_code}: {resp.text[:200]} (URL: {url})', file=sys.stderr)
    except Exception as e:
        print(f'[WARN] {model} 请求失败: {e} (URL: {url})', file=sys.stderr)
    return None

def _call_volcengine_ark(messages: list[dict], llm: dict) -> str | None:
    """调用火山引擎 ARK API (豆包)"""
    v_cfg = llm.get('volcengine', {})
    api_key = v_cfg.get('api_key', '')
    model = v_cfg.get('model', '')
    base_url = v_cfg.get('base_url', 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/')
    temperature = llm.get('temperature', 0.1)

    if not api_key or api_key == 'ARK_API_KEY':
        print('[WARN] 豆包 API Key 未配置', file=sys.stderr)
        return None

    # 火山引擎的 OpenAI 兼容端点
    url = f"{base_url}/chat/completions"
    headers = { "Authorization": f"Bearer {api_key}", "Content-Type": "application/json" }
    payload = {
        "model": model, "messages": messages, "temperature": temperature
        # 豆包部分模型对 response_format: json_object 支持不同，此处保持通用
    }

    timeout = llm.get('timeout', 120)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content']
        print(f'[WARN] 豆包 API 返回 {resp.status_code}: {resp.text[:200]}', file=sys.stderr)
    except Exception as e:
        print(f'[WARN] 豆包请求失败: {e}', file=sys.stderr)
    return None

# 改回原名兼容 intelligence.py
def _call_deepseek(messages: list[dict], cfg: dict) -> str | None:
    return _call_llm_api(messages, cfg)

# --- 翻译与语言检测工具 (新增) ---

def is_english_text(text: str) -> bool:
    """简单启发式检测是否为英文/外语"""
    if not text: return False
    # 去除标点符号和数字
    clean = re.sub(r'[^\u4e00-\u9fa5a-zA-Z]', '', text)
    if not clean: return False
    
    # 计算中文字符和英文字符
    chinese_chars = len(re.findall(r'[\u4e00-\u9fa5]', clean))
    alpha_chars = len(re.findall(r'[a-zA-Z]', clean))
    
    total = chinese_chars + alpha_chars
    if total == 0: return False
    
    # 如果英文字符占比超过 70%，且中文极少，判定为外语
    return (alpha_chars / total > 0.7) and (chinese_chars < 5)

def batch_translate_to_chinese(texts: list[str], cfg: dict) -> dict:
    """
    批量翻译标题或摘要
    返回 {原始文本: 翻译后文本}
    """
    if not texts: return {}
    
    # 过滤真正需要翻译的（非中文）
    to_translate = []
    result_map = {}
    for t in texts:
        if is_english_text(t):
            to_translate.append(t)
        else:
            result_map[t] = t
            
    if not to_translate: return result_map
    
    # 分批翻译（每批 15 条，平衡效率与语义质量）
    batch_size = 15
    for i in range(0, len(to_translate), batch_size):
        batch = to_translate[i:i + batch_size]
        # 构造带序号的 prompt
        prompt_lines = "\n".join([f"{idx+1}. {t}" for idx, t in enumerate(batch)])
        
        messages = [
            {"role": "system", "content": "你是一个专业的新闻翻译。将以下外语标题/短句翻译成自然准确的中文。按输入顺序通过序号返回，不要输出任何多余的解释，仅输出翻译后的内容。"},
            {"role": "user", "content": f"请翻译：\n{prompt_lines}"}
        ]
        
        raw = _call_llm_api(messages, cfg)
        if raw:
            # 解析 "1. XXX" 格式
            lines = raw.strip().split('\n')
            for line in lines:
                match = re.match(r'^(\d+)[\.\s]+(.*)$', line.strip())
                if match:
                    idx = int(match.group(1)) - 1
                    if 0 <= idx < len(batch):
                        result_map[batch[idx]] = match.group(2).strip()
        
        # 兜底：如果某项翻译失败，保留原文
        for t in batch:
            if t not in result_map: result_map[t] = t
            
    return result_map

def translate_text(text: str, cfg: dict) -> str:
    """翻译长文本（如正文内容）"""
    if not text or not is_english_text(text[:500]): 
        return text
        
    messages = [
        {"role": "system", "content": "你是一个专业的新闻翻译助手。请将以下外语全文翻译成简洁、严谨、地道的中文。保持原意，但语言要自然。只返回翻译后的正文。"},
        {"role": "user", "content": text}
    ]
    
    translated = _call_llm_api(messages, cfg)
    return translated or text


def _parse_tags_response(raw: str, count: int) -> list[list[str]]:
    """解析模型返回的 JSON，容错处理"""
    try:
        # 尝试直接解析
        parsed = json.loads(raw)
        
        # 如果是 {"tags": [...]} 之类的包装格式
        if isinstance(parsed, dict):
            for key in ('tags', 'results', 'data', 'classifications'):
                if key in parsed and isinstance(parsed[key], list):
                    parsed = parsed[key]
                    break
            else:
                # 取第一个 list 类型的值
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break
        
        if isinstance(parsed, list) and len(parsed) == count:
            # 确保每个元素都是字符串列表
            result = []
            for item in parsed:
                if isinstance(item, list):
                    result.append([str(t) for t in item])
                elif isinstance(item, str):
                    result.append([item] if item else [])
                else:
                    result.append([])
            return result
    except json.JSONDecodeError:
        pass
    
    # 解析失败，返回全空
    print(f'[WARN] LLM 返回格式异常，降级为空标签', file=sys.stderr)
    return [[] for _ in range(count)]


def batch_tag_articles(articles: list[dict], cfg: dict, conn=None):
    """主入口：对文章列表进行批量 LLM 打标，结果写入数据库

    Args:
        articles: fetch.py 产出的 all_new 列表
        cfg: llm_topics.yaml 的完整内容
        conn: SQLite 连接（可选）
    """
    llm = cfg.get('llm', {})
    if not llm.get('enabled'):
        return

    topics = cfg.get('topics', [])
    if not topics:
        print('[INFO] 无 LLM 主题配置，跳过打标', file=sys.stderr)
        return

    if not articles:
        return

    max_batch = llm.get('max_batch', 20)
    topic_names = [t['name'] for t in topics]
    system_prompt = _build_system_prompt(topics)

    # 准备数据库连接
    own_conn = False
    if conn is None:
        try:
            from store import get_conn, init_db
            conn = get_conn()
            init_db(conn)
            own_conn = True
        except Exception as e:
            print(f'[WARN] LLM 打标无法连接数据库: {e}', file=sys.stderr)
            return

    total = len(articles)
    tagged_count = 0

    with progress_lock:
        print(f'[PROGRESS] ' + json.dumps({
            'type': 'llm_start',
            'total': total,
            'topics': topic_names
        }, ensure_ascii=False), file=sys.stderr)

    # 分批处理
    for batch_start in range(0, total, max_batch):
        batch = articles[batch_start:batch_start + max_batch]

        with progress_lock:
            print(f'[PROGRESS] ' + json.dumps({
                'type': 'llm_batch',
                'batch_start': batch_start + 1,
                'batch_end': min(batch_start + len(batch), total),
                'total': total
            }, ensure_ascii=False), file=sys.stderr)

        user_prompt = _build_user_prompt(batch)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        raw_response = _call_deepseek(messages, cfg)
        if raw_response is None:
            continue

        tags_list = _parse_tags_response(raw_response, len(batch))

        # 过滤：只保留在配置中定义过的合法标签
        valid_names = set(topic_names)
        for i, tags in enumerate(tags_list):
            tags_list[i] = [t for t in tags if t in valid_names]

        # 写入数据库
        for item, tags in zip(batch, tags_list):
            if tags:
                try:
                    uh = item.get('uh') or item.get('url_hash', '')
                    if uh:
                        conn.execute(
                            "UPDATE articles SET llm_tags = ? WHERE url_hash = ?",
                            [json.dumps(tags, ensure_ascii=False), uh]
                        )
                        tagged_count += 1
                except Exception as e:
                    print(f'[WARN] 写入标签失败: {e}', file=sys.stderr)

        conn.commit()

    with progress_lock:
        print(f'[PROGRESS] ' + json.dumps({
            'type': 'llm_done',
            'tagged': tagged_count,
            'total': total
        }, ensure_ascii=False), file=sys.stderr)

    if own_conn:
        conn.close()

    print(f'[INFO] LLM 打标完成: {tagged_count}/{total} 篇命中标签', file=sys.stderr)


def polish_topic_description(topic_name: str, current_desc: str, cfg: dict) -> str | None:
    """用 AI 润色/扩充一个主题的描述提示词

    Args:
        topic_name: 主题名称
        current_desc: 当前的描述文本
        cfg: llm_topics.yaml 配置

    Returns:
        润色后的描述文本，失败返回 None
    """
    messages = [
        {"role": "system", "content": """你是一个专业的新闻分类系统提示词工程师。
你的任务是优化和扩充用于新闻语义分类的主题描述提示词。

要求：
1. 保持原有语义不变，但让描述更加全面和精准
2. 补充可能遗漏的相关场景和关键词
3. 使用清晰的中文表述
4. 长度控制在 100-200 字
5. 只返回优化后的描述文本，不要加任何前缀或解释"""},
        {"role": "user", "content": f"请优化以下新闻分类主题的描述提示词：\n\n主题名称：{topic_name}\n当前描述：{current_desc}"}
    ]

    raw = _call_deepseek(messages, {**cfg, 'llm': {**cfg.get('llm', {}), 'temperature': 0.7}})
    if raw:
        # 清理可能的引号包裹
        raw = raw.strip().strip('"').strip("'")
        # 如果返回了 JSON，提取文本
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                raw = parsed.get('description', parsed.get('text', raw))
            elif isinstance(parsed, str):
                raw = parsed
        except json.JSONDecodeError:
            pass
        return raw.strip()
    return None


if __name__ == '__main__':
    # 测试：直接运行此文件可测试 API 连通性
    cfg = load_llm_config()
    if not cfg:
        print("请先配置 config/llm_topics.yaml")
        sys.exit(1)

    test_articles = [
        {'title': '中国外交部回应美国制裁：坚决反对', 'summary': '外交部发言人表示...', 'platform': '新华社'},
        {'title': '俄乌冲突最新：泽连斯基访问前线', 'summary': '乌克兰总统...', 'platform': 'BBC'},
        {'title': '苹果发布新款iPhone', 'summary': '科技产品发布...', 'platform': 'CNN'},
    ]

    print("测试 LLM 打标...")
    topics = cfg.get('topics', [])
    system_prompt = _build_system_prompt(topics)
    user_prompt = _build_user_prompt(test_articles)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    raw = _call_deepseek(messages, cfg)
    if raw:
        print(f"原始返回: {raw}")
        tags = _parse_tags_response(raw, len(test_articles))
        for article, t in zip(test_articles, tags):
            print(f"  [{', '.join(t) or '无标签'}] {article['title']}")
    else:
        print("API 调用失败")
