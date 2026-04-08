#!/usr/bin/env python3
"""
本地 LLM 客户端 - 支持 Ollama 和 LM Studio (OpenAI兼容接口)

LM Studio 是 macOS/Windows 上流行的本地模型推理工具，
提供 OpenAI兼容的 API 接口，默认端口 1234。

配置方式 (config/api_keys.json):
{
    "local_llm_provider": "lmstudio",  // 或 "ollama"
    "lmstudio_base_url": "http://localhost:1234",
    "lmstudio_model": "local-model",
    "ollama_base_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:7b"
}
"""

import requests
import json
import time
import re
from typing import List, Dict, Optional, Literal

# ═══════════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════════

def load_local_llm_config() -> dict:
    """从 api_keys.json 加载本地 LLM 配置"""
    try:
        from pathlib import Path
        config_file = Path(__file__).parent.parent / 'config' / 'api_keys.json'
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def get_local_llm_provider() -> str:
    """获取当前配置的本地 LLM 提供者"""
    config = load_local_llm_config()
    return config.get('local_llm_provider', 'ollama')


# ═══════════════════════════════════════════════════════════════════
# LM Studio 客户端 (OpenAI兼容接口)
# ═══════════════════════════════════════════════════════════════════

class LMStudioClient:
    """
    LM Studio 客户端 - 使用 OpenAI兼容接口

    LM Studio 默认运行在 localhost:1234
    API 格式与 OpenAI 一致：
    - POST /v1/chat/completions
    - GET /v1/models
    """

    def __init__(self, base_url: str = "http://localhost:1234", model: str = None):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self._available_models = None

    def is_available(self) -> bool:
        """检查 LM Studio 服务是否可用"""
        try:
            resp = requests.get(f"{self.base_url}/v1/models", timeout=2)
            return resp.status_code == 200
        except:
            return False

    def list_models(self) -> List[dict]:
        """获取可用模型列表"""
        try:
            resp = requests.get(f"{self.base_url}/v1/models", timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('data', [])
            return []
        except:
            return []

    def get_first_model(self) -> Optional[str]:
        """获取第一个可用模型名称"""
        if self._available_models is None:
            self._available_models = self.list_models()
        if self._available_models:
            return self._available_models[0].get('id', 'local-model')
        return self.model or 'local-model'

    def chat(self, messages: List[dict], model: str = None,
             temperature: float = 0.7, max_tokens: int = 2048,
             stream: bool = False) -> dict:
        """
        OpenAI兼容的聊天接口

        Args:
            messages: [{"role": "user/assistant/system", "content": "..."}]
            model: 模型名称(可选，默认使用已加载的模型)
            temperature: 温度参数
            max_tokens: 最大输出 tokens
            stream: 是否流式输出

        Returns:
            {"content": "..."} 或 {"error": "..."}
        """
        url = f"{self.base_url}/v1/chat/completions"

        # LM Studio 通常只运行一个模型，使用默认即可
        if model is None:
            model = self.get_first_model()

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }

        try:
            if stream:
                return requests.post(url, json=payload, stream=True, timeout=60)
            else:
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
                    return {"content": content}
                return {"error": f"Status {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    def generate(self, prompt: str, model: str = None, **kwargs) -> dict:
        """
        简化的生成接口（兼容Ollama风格）

        将单个 prompt 转换为 chat格式
        """
        messages = [{"role": "user", "content": prompt}]
        result = self.chat(messages, model=model, **kwargs)
        if 'content' in result:
            return {"response": result['content']}
        return result

    # ── 业务方法（与 OllamaClient 保持一致）────────────────────────

    def translate_titles(self, titles: List[str], model: str = None) -> dict:
        """批量翻译标题为中文"""
        if not titles:
            return {"translations": []}

        titles_str = "\n".join([f"{i+1}. {t}" for i, t in enumerate(titles)])
        prompt = f"""
你是一个专业的翻译助手。请将以下新闻标题翻译成中文。
要求：
1. 保持原意，语言简洁地道。
2. 仅输出翻译后的标题，每行一个。
3. 不要输出任何多余的解释、序号或前导词。

待翻译标题：
{titles_str}
"""
        res = self.generate(prompt, model=model)
        if 'response' in res:
            lines = [line.strip() for line in res['response'].strip().split('\n') if line.strip()]
            cleaned = []
            for l in lines:
                l = re.sub(r'^\d+[\.、\s]+', '', l)
                cleaned.append(l)
            return {"translations": cleaned}
        return {"error": res.get('error', 'Unknown error')}

    def analyze_stance(self, keyword: str, articles: List[dict], model: str = None) -> dict:
        """对文章进行立场分析"""
        if not articles:
            return {"error": "No articles to analyze"}

        context = ""
        for i, a in enumerate(articles[:15]):
            context += f"[{i+1}] {a.get('title', '')} ({a.get('source', '')})\n"

        prompt = f"""
你是一个国际媒体舆情分析助手。请分析以下关于"{keyword}"的新闻报道。
输出必须是合法的 JSON 格式，包含以下结构：
{
  "country_analysis": {
    "国家/地区名": {
      "stance": "媒体立场总结",
      "tone": "报道语调",
      "key_focus": "关注的核心点"
    }
  },
  "comparison": {
    "differences": "综合对比各方报道的主要差异和共同点"
  }
}

待分析报道：
{context}

注意：
1. 请按报道来源的国家或地区进行分类总结。
2. 请直接输出 JSON，不要包含任何前言、后记或 Markdown 代码块标记。
3. 使用中文回答。
"""
        return self.generate(prompt, model=model)

    def extract_keywords(self, keyword: str, context: str = "",
                         language: str = "mixed", model: str = None) -> dict:
        """从关键词提取/扩展相关搜索词"""
        lang_instruction = {
            "mixed": "同时输出中文和英文关键词",
            "zh": "仅输出中文关键词",
            "en": "仅输出英文关键词"
        }

        prompt = f"""
你是一个新闻搜索专家。用户要搜索关于 "{keyword}" 的新闻。
{context if context else ""}
请扩展生成相关的搜索关键词，以提高搜索覆盖率。

要求：
1. {lang_instruction.get(language, lang_instruction['mixed'])}
2. 生成 5-10 个相关关键词/搜索词
3. 包含同义词、相关人物/机构、相关事件
4. 输出格式为 JSON：
   {{
     "keywords": ["关键词1", "关键词2", ...],
     "translations": {{
       "中文词": "English equivalent",
       ...
     }}
   }}
5. 直接输出 JSON，不要包含任何解释或代码块标记。
"""
        res = self.generate(prompt, model=model)
        if 'response' in res:
            try:
                response_text = res['response'].strip()
                response_text = re.sub(r'^```json\s*', '', response_text)
                response_text = re.sub(r'^```\s*', '', response_text)
                response_text = re.sub(r'\s*```$', '', response_text)
                return json.loads(response_text)
            except json.JSONDecodeError:
                lines = response_text.strip().split('\n')
                keywords = []
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('{') and not line.startswith('}'):
                        kw = re.sub(r'[\"\'\,\[\]]', '', line)
                        if kw:
                            keywords.append(kw)
                return {"keywords": keywords, "translations": {}}
        return {"error": res.get('error', 'Unknown error'), "keywords": [keyword]}

    def smart_search_keywords(self, query: str, max_keywords: int = 10, model: str = None) -> List[str]:
        """智能提取搜索关键词"""
        prompt = f"""
用户要搜索新闻，主题是："{query}"
请生成 {max_keywords} 个相关搜索关键词（中英文混合），用于扩大搜索范围。
要求：
1. 包含原词的中文和英文版本
2. 包含同义词和相关词
3. 仅输出关键词，每行一个，不要序号或解释
"""
        res = self.generate(prompt, model=model)
        if 'response' in res:
            lines = [line.strip() for line in res['response'].strip().split('\n') if line.strip()]
            keywords = []
            for l in lines:
                l = re.sub(r'^\d+[\.、\s:]+', '', l)
                l = re.sub(r'^[\-\*]+\s*', '', l)
                if l:
                    keywords.append(l)
            return keywords[:max_keywords]
        return [query]


# ═══════════════════════════════════════════════════════════════════
# 统一客户端工厂
# ═══════════════════════════════════════════════════════════════════

class LocalLLMClient:
    """
    统一的本地 LLM 客户端

    根据 api_keys.json 中的 local_llm_provider 配置自动选择：
    - "ollama" → 使用 Ollama (默认端口 11434)
    - "lmstudio" → 使用 LM Studio (默认端口 1234)
    """

    def __init__(self, provider: str = None, base_url: str = None, model: str = None):
        config = load_local_llm_config()

        # 确定提供者
        self.provider = provider or config.get('local_llm_provider', 'ollama')

        # 根据提供者创建客户端
        if self.provider == 'lmstudio':
            url = base_url or config.get('lmstudio_base_url', 'http://localhost:1234')
            mdl = model or config.get('lmstudio_model')
            self._client = LMStudioClient(base_url=url, model=mdl)
        else:
            # 默认 Ollama
            url = base_url or config.get('ollama_base_url', 'http://localhost:11434')
            mdl = model or config.get('ollama_model', 'qwen2.5:7b')
            # 导入 OllamaClient
            from ollama_helper import OllamaClient
            self._client = OllamaClient(base_url=url)
            self._ollama_model = mdl

    def is_available(self) -> bool:
        """检查服务是否可用"""
        return self._client.is_available()

    def list_models(self) -> List[dict]:
        """获取可用模型列表"""
        return self._client.list_models()

    def generate(self, prompt: str, model: str = None, **kwargs) -> dict:
        """生成文本"""
        if self.provider == 'ollama':
            model = model or self._ollama_model
        return self._client.generate(prompt, model=model, **kwargs)

    def chat(self, messages: List[dict], model: str = None, **kwargs) -> dict:
        """聊天接口"""
        if self.provider == 'ollama':
            # Ollama 没有 chat 接口，转换为 generate
            content = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
            return self.generate(content, model=model, **kwargs)
        return self._client.chat(messages, model=model, **kwargs)

    # 业务方法
    def translate_titles(self, titles: List[str], model: str = None) -> dict:
        if self.provider == 'ollama':
            model = model or self._ollama_model
        return self._client.translate_titles(titles, model=model)

    def analyze_stance(self, keyword: str, articles: List[dict], model: str = None) -> dict:
        if self.provider == 'ollama':
            model = model or self._ollama_model
        return self._client.analyze_stance(keyword, articles, model=model)

    def extract_keywords(self, keyword: str, context: str = "",
                         language: str = "mixed", model: str = None) -> dict:
        if self.provider == 'ollama':
            model = model or self._ollama_model
        return self._client.extract_keywords(keyword, context, language, model=model)

    def smart_search_keywords(self, query: str, max_keywords: int = 10, model: str = None) -> List[str]:
        if self.provider == 'ollama':
            model = model or self._ollama_model
        return self._client.smart_search_keywords(query, max_keywords, model=model)


def get_local_llm() -> LocalLLMClient:
    """获取本地 LLM 客户端实例（根据配置自动选择）"""
    return LocalLLMClient()


# ═══════════════════════════════════════════════════════════════════
# CLI 测试
# ═══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='本地 LLM 客户端测试')
    parser.add_argument('--provider', choices=['ollama', 'lmstudio'],
                        help='指定提供者（默认从配置读取）')
    parser.add_argument('--url', help='自定义 API 地址')
    parser.add_argument('--model', help='模型名称')
    parser.add_argument('--check', action='store_true', help='检查服务状态')
    parser.add_argument('--translate', type=str, help='翻译测试（输入英文标题）')
    parser.add_argument('--generate', type=str, help='生成测试')

    args = parser.parse_args()

    client = LocalLLMClient(provider=args.provider, base_url=args.url, model=args.model)

    print(f"\n[INFO] 提供者: {client.provider}")

    if args.check:
        available = client.is_available()
        print(f"[INFO] 服务状态: {'可用' if available else '不可用'}")
        if available:
            models = client.list_models()
            print(f"[INFO] 可用模型: {[m.get('id', m.get('name', 'unknown')) for m in models]}")

    if args.translate:
        titles = [args.translate]
        print(f"\n[TEST] 翻译: {args.translate}")
        result = client.translate_titles(titles)
        if 'translations' in result:
            print(f"[结果] {result['translations']}")
        else:
            print(f"[错误] {result}")

    if args.generate:
        print(f"\n[TEST] 生成: {args.generate}")
        result = client.generate(args.generate)
        if 'response' in result:
            print(f"[结果]\n{result['response']}")
        else:
            print(f"[错误] {result}")