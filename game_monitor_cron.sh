#!/bin/bash
# B站游戏热点监控 - Cron 定时脚本
# crontab -e 添加: 0 * * * * /path/to/game_monitor_cron.sh >> ~/monitor.log 2>&1

cd "$(dirname "$0")"
export PATH="/Users/zhaojiaqi/Library/Python/3.10/bin:$PATH"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
python3 game_monitor.py --mode once
