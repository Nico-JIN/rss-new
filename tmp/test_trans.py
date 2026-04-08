
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import api_cli

# Mock ext_results
ext_results = [
    {
        'title': 'SpaceX launches new rocket',
        'summary': 'The new Starship successfully reached orbit today.'
    },
    {
        'title': '中国成功发射神舟飞船',
        'summary': '今天上午，长征火箭搭载宇航员升空。'
    },
    {
        'title': 'こんにちは世界',
        'summary': '日本の最新ニュースをお届けします。'
    }
]

print("Before translation:")
for r in ext_results:
    print(r)

api_cli._translate_ext_results(ext_results)

print("\nAfter translation:")
for r in ext_results:
    print(r)
