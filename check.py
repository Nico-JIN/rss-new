import sqlite3
c = sqlite3.connect('data/news.db')
for r in c.execute("select url from articles where media_group='联合早报' order by published desc limit 5"):
    print(repr(r[0]))
