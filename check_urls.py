import sqlite3
c = sqlite3.connect('data/news.db')
with open('url_dump.txt', 'w', encoding='utf-8') as f:
    for r in c.execute("select url from articles where media_group='联合早报' order by published desc limit 5"):
        f.write(repr(r[0]) + "\n")
