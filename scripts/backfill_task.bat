@echo off
REM 原文补抓定时任务
REM 每天凌晨 2:30 运行
REM 补抓最近 1 天的数据

cd /d "C:\Users\76539\.openclaw\skills\rss-news"

echo [%date% %time%] 开始原文补抓任务 >> logs\backfill.log 2>&1

python scripts\backfill_original_content.py --days 1 --limit 500 >> logs\backfill.log 2>&1

echo [%date% %time%] 补抓任务完成 >> logs\backfill.log 2>&1
