#!/usr/bin/env python3
"""原文补抓守护进程

使用 Python schedule 库实现定时任务，不依赖 Windows 任务计划程序。

运行方式：
    python scripts/backfill_scheduler.py

会在每天凌晨 2:30 自动运行补抓任务。
"""

import schedule
import time
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from backfill_original_content import run_backfill


def run_backfill_task():
    """运行补抓任务"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n[{timestamp}] 开始执行补抓任务...")

    try:
        result = run_backfill(days=1, limit=500, trigger_type='scheduled')

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 补抓任务完成")
        print(f"  成功: {result['success']} 篇")
        print(f"  失败: {result['failed']} 篇")
        print(f"  跳过: {result['skipped']} 篇")
        print(f"  耗时: {result['duration_seconds']} 秒")

    except Exception as e:
        print(f"[ERROR] 补抓任务失败: {e}")


def main():
    print("=" * 60)
    print("原文补抓守护进程")
    print("=" * 60)
    print(f"定时: 每天凌晨 02:30")
    print()
    print("按 Ctrl+C 退出")
    print()

    # 设置定时任务：每天 02:30 运行
    schedule.every().day.at("02:30").do(run_backfill_task)

    # 显示下次运行时间
    next_run = schedule.next_run()
    print(f"下次运行时间: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

    # 主循环
    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] 守护进程已停止")
