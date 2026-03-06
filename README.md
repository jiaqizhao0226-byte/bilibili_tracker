<h1 align="center">🎮 Bilibili Game Tracker</h1>

<p align="center">
  <strong>B站游戏行业热点监控系统 — 全天候采集游戏区热门内容、搜索趋势、UP主动态</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-green.svg?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge" alt="MIT License">
</p>

---

## 功能概览

- 🔥 **热搜榜监控** — 实时获取B站热搜，自动筛选游戏相关话题
- 📺 **综合热门追踪** — 抓取全站热门视频中的游戏内容
- 🏆 **游戏区排行榜** — 游戏分区 TOP20 排名（3日热门）
- 📊 **分区动态** — 单机游戏、电子竞技、手机游戏、网络游戏四大分区实时动态
- 🔍 **关键词搜索** — 自定义关键词批量搜索（原神、崩铁、王者、Steam、GTA6 等）
- 👤 **UP主追踪** — 关注指定UP主的最新投稿
- 📈 **可视化看板** — 内置 Web Dashboard，数据一目了然
- ⏰ **定时采集** — 支持单次运行和全天候循环监控模式

## 快速开始

### 安装依赖

```bash
pip install requests loguru rich
```

### 单次采集

```bash
python game_monitor.py
```

### 全天候循环监控

```bash
python game_monitor.py --mode daemon --interval 60
```

### 自定义关键词

```bash
python game_monitor.py --keywords "黑神话" "绝区零" "鸣潮"
```

### 启动可视化看板

```bash
cd dashboard
python server.py
```

## 数据输出

采集结果保存在 `monitor_output/` 目录，JSON 格式，包含：

- 热搜榜（含游戏相关标记）
- 综合热门视频（标题、UP主、播放量、点赞、弹幕等）
- 各分区动态
- 游戏区排行榜
- 关键词搜索结果
- UP主最新投稿

## 监控覆盖的分区

| 分区 | 分区ID |
|------|--------|
| 单机游戏 | 17 |
| 电子竞技 | 171 |
| 手机游戏 | 172 |
| 网络游戏 | 65 |

## 默认监控关键词

`游戏新闻` `新游` `手游推荐` `端游` `Steam` `原神` `崩坏星穹铁道` `王者荣耀` `英雄联盟` `GTA6` `任天堂` `PS5` `Xbox` `独立游戏` `版号`

## 项目结构

```
bilibili_tracker/
├── game_monitor.py          # 核心监控脚本
├── game_monitor_cron.sh     # 定时任务脚本
├── constraints.txt          # 约束条件
├── dashboard/
│   ├── server.py            # Dashboard 后端
│   ├── index.html           # Dashboard 前端
│   ├── build_static.py      # 静态页面生成
│   ├── dist/                # 静态部署版本
│   └── data/                # 监控数据（JSON）
└── monitor_output/          # 采集结果输出
```

## 定时任务配置

使用 cron 定时采集：

```bash
# 每小时采集一次
0 * * * * cd /path/to/bilibili_tracker && bash game_monitor_cron.sh
```

## License

MIT
