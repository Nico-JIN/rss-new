import os, time, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent
FETCH_SCRIPT = BASE / "scripts/fetch.py"
RESULTS_DIR = BASE / "results"

def poll(interval_minutes=15, run_once=False, custom_hours=None):
    if not RESULTS_DIR.exists():
        RESULTS_DIR.mkdir()

    if custom_hours is not None:
        hours = custom_hours
    else:
        # 默认不指定小时数，让 fetch.py 使用 last_fetch_at 进行增量拉取
        hours = None

    if not run_once:
        print(f"[*] RSS 调度器已启动，每 {interval_minutes} 分钟运行一次，整点自动补位延迟 90s")
        print(f"[*] 结果将保存至: {RESULTS_DIR.absolute()}")

    while True:
        now_start = datetime.now()
        timestamp = now_start.strftime("%Y%m%d_%H%M%S")
        filename = RESULTS_DIR / f"fetch_{timestamp}.json"

        if not run_once:
            print(f"[{now_start.strftime('%Y-%m-%d %H:%M:%S')}] 正在抓取并保存至 {filename.name}...")
        else:
            print(f"[*] 执行单次抓取(时间窗口 {hours}h)... 结果存档至 {RESULTS_DIR.absolute()}", file=sys.stderr)

        try:
            cmd = [sys.executable, str(FETCH_SCRIPT)]
            if hours is not None and hours > 0:
                cmd.extend(['--hours', str(hours)])
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode == 0:
                try:
                    import json
                    data = json.loads(result.stdout)
                    
                    # 保存三种阶段的结果 (raw, time_filtered, final)
                    for key in ["raw", "time_filtered", "final"]:
                        with open(RESULTS_DIR / f"{key}_{timestamp}.json", "w", encoding="utf-8") as f:
                            json.dump(data.get(key, {}), f, ensure_ascii=False, indent=2)

                    if run_once:
                        print(json.dumps(data.get("final", {}), ensure_ascii=False, indent=2))
                        return

                    stats = data.get("stats", {})
                    print(f"    - 完成！新增 {stats.get('final_count', 0)} 条记录 (原始抓取 {stats.get('total_raw_count', 0)} 条)。")
                    
                except Exception as e:
                    if run_once:
                        print(f"[!] JSON解析失败 ({e})", file=sys.stderr)
                        return
                    print(f"    [!] JSON解析失败 ({e})")
            else:
                err_msg = result.stderr.decode(errors='replace')[:500]
                if run_once:
                    print(f"[!] 抓取脚本执行失败 (退出码 {result.returncode})", file=sys.stderr)
                    return
                print(f"    [!] 抓取脚本执行失败 (退出码 {result.returncode})")
                print(f"    - 错误信息: {err_msg}")
        except Exception as e:
            if run_once:
                print(f"[!] 调度执行异常: {e}", file=sys.stderr)
                return
            print(f"    [!] 调度执行异常: {e}")

        if run_once:
            break

        # --- 精确调度与整点补位逻辑 ---
        # 1. 计算理论下一次运行时间 (基于本次开始时间)
        theory_next = now_start + timedelta(minutes=interval_minutes)
        
        # 2. 计算下一个整点补位时刻 (Next Hour + 90s)
        # 获取当前小时起始，加1小时，再加90秒
        next_hour = (now_start + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        padding_target = next_hour + timedelta(seconds=90)

        # 3. 跨整点检测：如果理论下次运行时间跨过了整点补位点
        if theory_next > padding_target:
            actual_next = padding_target
            wait_reason = f"整点补位 (跨过了 {next_hour.strftime('%H:00')})"
        else:
            actual_next = theory_next
            wait_reason = f"常规间隔 ({interval_minutes}m)"

        # 4. 计算实际需要休眠的秒数
        sleep_sec = (actual_next - datetime.now()).total_seconds()
        if sleep_sec < 0:
            sleep_sec = 0.5 # 防止零点微小误差导致的负数

        print(f"[*] 下一次运行预计在: {actual_next.strftime('%H:%M:%S')} ({wait_reason})")
        time.sleep(sleep_sec)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60, help="抓取间隔（分钟）")
    parser.add_argument("--once", action="store_true", help="单次运行，并将结果存档，同时仅向 Agent 返回 final JSON")
    parser.add_argument("--hours", type=float, default=None, help="自定义单次运行抓取的时间窗口（仅对一次性抓取推荐使用）")
    args = parser.parse_args()
    
    try:
        poll(args.interval, run_once=args.once, custom_hours=args.hours)
    except KeyboardInterrupt:
        print("\n[*] 调度器已手动停止。")

