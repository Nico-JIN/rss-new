
import sys
import os
from pathlib import Path

# Add scripts to path
ROOT = Path(r"c:\Users\76539\.openclaw\skills\rss-news")
sys.path.append(str(ROOT / "scripts"))

from llm_tagger import is_english_text, batch_translate_to_chinese, translate_text, load_llm_config

def test():
    cfg = load_llm_config()
    print(f"LLM Config Loaded: {cfg.get('llm', {}).get('provider')}")
    
    # Test 1: Language Detection
    eng_title = "Behind the lobster merch, China’s latest tech obsession"
    chi_title = "特朗普总统图书馆的开发商勾勒出迈阿密一座高耸建筑的愿景"
    
    print(f"Detect English: '{eng_title}' -> {is_english_text(eng_title)}")
    print(f"Detect Chinese: '{chi_title}' -> {is_english_text(chi_title)}")
    
    # Test 2: Batch Translation
    titles = [
        "Behind the lobster merch, China’s latest tech obsession",
        "Taiwan AI chips to China in violation of export law",
        "已经有中文的标题不需要翻译"
    ]
    print("\nStarting batch translation...")
    translated_map = batch_translate_to_chinese(titles, cfg)
    for orig, trans in translated_map.items():
        print(f"  '{orig[:30]}...' -> '{trans}'")
        
    # Test 3: Full Text Translation
    eng_content = "This is a long news article about artificial intelligence developments in Silicon Valley. It discusses new models and venture capital funding."
    print("\nStarting full text translation...")
    translated_content = translate_text(eng_content, cfg)
    print(f"  Original: {eng_content}")
    print(f"  Translated: {translated_content}")

if __name__ == "__main__":
    test()
