#!/ antisocial/env python3
import requests
import json
import time

class OllamaClient:
    def __init__(self, base_url="http://localhost:11434"):
        self.base_url = base_url

    def is_available(self):
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return resp.status_code == 200
        except:
            return False

    def list_models(self):
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('models', [])
            return []
        except:
            return []

    def generate(self, model, prompt, stream=False):
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": stream
        }
        try:
            if stream:
                return requests.post(url, json=payload, stream=True, timeout=60)
            else:
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"Status {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    def translate_titles(self, model, titles):
        """
        批量翻译标题为中文
        """
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
        res = self.generate(model, prompt)
        if 'response' in res:
            # 简单按行分割
            lines = [line.strip() for line in res['response'].strip().split('\n') if line.strip()]
            # 去除可能的序号 (如 "1. ", "1、")
            import re
            cleaned = []
            for l in lines:
                l = re.sub(r'^\d+[\.、\s]+', '', l)
                cleaned.append(l)
            return {"translations": cleaned}
        return {"error": res.get('error', 'Unknown error')}

    def analyze_stance(self, model, keyword, articles):
        """
        对文章进行立场分析，返回结构化 JSON
        """
        if not articles:
            return {"error": "No articles to analyze"}
            
        # 构建摘要作为上下文
        context = ""
        for i, a in enumerate(articles[:15]):
            context += f"[{i+1}] {a.get('title', '')} ({a.get('source', '')})\n"
            
        prompt = f"""
你是一个国际媒体舆情分析助手。请分析以下关于“{keyword}”的新闻报道。
输出必须是合法的 JSON 格式，包含以下结构：
{{
  "country_analysis": {{
    "国家/地区名": {{
      "stance": "媒体立场总结",
      "tone": "报道语调",
      "key_focus": "关注的核心点"
    }}
  }},
  "comparison": {{
    "differences": "综合对比各方报道的主要差异和共同点"
  }}
}}

待分析报道：
{context}

注意：
1. 请按报道来源的国家或地区（如中国、美国、日本、欧洲等）进行分类总结。
2. 请直接输出 JSON，不要包含任何前言、后记或 Markdown 代码块标记（如 ```json）。
3. 使用中文回答。
"""
        return self.generate(model, prompt)

    def extract_keywords(self, model, keyword, context="", language="mixed"):
        """
        从关键词提取/扩展相关搜索词

        Args:
            model: Ollama 模型名称
            keyword: 主关键词/主题
            context: 可选的上下文信息
            language: 输出语言 (mixed/zh/en)

        Returns:
            dict: {"keywords": [...], "translations": {...}}
        """
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
        res = self.generate(model, prompt)
        if 'response' in res:
            try:
                # 清理可能的 markdown 标记
                import re
                response_text = res['response'].strip()
                # 移除 ```json 和 ``` 标记
                response_text = re.sub(r'^```json\s*', '', response_text)
                response_text = re.sub(r'^```\s*', '', response_text)
                response_text = re.sub(r'\s*```$', '', response_text)

                result = json.loads(response_text)
                return result
            except json.JSONDecodeError as e:
                # 尝试简单解析
                import re
                lines = response_text.strip().split('\n')
                keywords = []
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('{') and not line.startswith('}'):
                        # 移除引号和逗号
                        kw = re.sub(r'[\"\'\,\[\]]', '', line)
                        if kw:
                            keywords.append(kw)
                return {"keywords": keywords, "translations": {}}
        return {"error": res.get('error', 'Unknown error'), "keywords": [keyword]}

    def smart_search_keywords(self, model, query, max_keywords=10):
        """
        智能提取搜索关键词（简化版，用于快速搜索）

        Args:
            model: Ollama 模型
            query: 用户搜索查询
            max_keywords: 最大关键词数量

        Returns:
            list: 关键词列表
        """
        prompt = f"""
用户要搜索新闻，主题是："{query}"
请生成 {max_keywords} 个相关搜索关键词（中英文混合），用于扩大搜索范围。
要求：
1. 包含原词的中文和英文版本
2. 包含同义词和相关词
3. 仅输出关键词，每行一个，不要序号或解释
"""
        res = self.generate(model, prompt)
        if 'response' in res:
            import re
            lines = [line.strip() for line in res['response'].strip().split('\n') if line.strip()]
            # 清理序号
            keywords = []
            for l in lines:
                l = re.sub(r'^\d+[\.、\s:]+', '', l)
                l = re.sub(r'^[\-\*]+\s*', '', l)
                if l:
                    keywords.append(l)
            return keywords[:max_keywords]
        return [query]
