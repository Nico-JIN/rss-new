import json, sys, os
sys.path.insert(0, 'scripts')
os.environ['PYTHONIOENCODING'] = 'utf-8'
from store import get_conn
conn = get_conn()

# 1. 统计
total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
tagged = conn.execute("SELECT COUNT(*) FROM articles WHERE llm_tags IS NOT NULL AND llm_tags != '' AND llm_tags != '[]'").fetchone()[0]
empty_tags = conn.execute("SELECT COUNT(*) FROM articles WHERE llm_tags = '[]' OR llm_tags IS NULL OR llm_tags = ''").fetchone()[0]

result = {"total": total, "tagged_non_empty": tagged, "empty_tags": empty_tags}

# 2. 标签分布
rows = conn.execute("SELECT llm_tags FROM articles WHERE llm_tags IS NOT NULL AND llm_tags != '' AND llm_tags != '[]'").fetchall()
tag_count = {}
for r in rows:
    try:
        tags = json.loads(r['llm_tags'])
        if isinstance(tags, list):
            for t in tags:
                tag_count[t] = tag_count.get(t, 0) + 1
    except: pass

result["tag_distribution"] = tag_count

# 3. country 分布  
rows2 = conn.execute("SELECT country, COUNT(*) as cnt FROM articles WHERE country IS NOT NULL AND country != '' GROUP BY country ORDER BY cnt DESC LIMIT 20").fetchall()
result["country_distribution"] = {r['country']: r['cnt'] for r in rows2}

# 4. 最新 8 篇有标签的样本
samples = conn.execute("SELECT llm_tags, country, platform, title FROM articles WHERE llm_tags IS NOT NULL AND llm_tags != '' AND llm_tags != '[]' ORDER BY published DESC LIMIT 8").fetchall()
result["samples"] = [{"tags": s['llm_tags'], "country": s['country'], "platform": s['platform'], "title": s['title'][:60]} for s in samples]

# 5. 最新 8 篇无标签的样本
samples2 = conn.execute("SELECT llm_tags, country, platform, title FROM articles WHERE llm_tags = '[]' OR llm_tags IS NULL ORDER BY published DESC LIMIT 8").fetchall()
result["untagged_samples"] = [{"tags": s['llm_tags'], "country": s['country'], "platform": s['platform'], "title": s['title'][:60]} for s in samples2]

# Write JSON to file
with open('scripts/check_result.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("Done - see scripts/check_result.json")
conn.close()
