#!/usr/bin/env python3
"""
B站游戏行业热点监控系统
全天候采集B站游戏区的热门内容、搜索趋势、UP主动态
"""

import json
import os
import re
import time
from datetime import datetime

import requests
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# ─── 配置 ───────────────────────────────────────────────────────────────

CONFIG = {
    # 游戏行业监控关键词
    "keywords": [
        "游戏新闻", "新游", "手游推荐", "端游", "steam",
        "原神", "崩坏星穹铁道", "王者荣耀", "英雄联盟",
        "GTA6", "任天堂", "PS5", "Xbox", "独立游戏", "版号",
    ],
    # B站游戏相关分区 ID
    # https://github.com/SocialSisterYi/bilibili-API-collect (已归档)
    "game_regions": {
        "游戏(主分区)": 4,
        "单机游戏": 17,
        "电子竞技": 171,
        "手机游戏": 172,
        "网络游戏": 65,
    },
    # 关注的游戏UP主 (mid)，可按需添加
    "up_mids": {
        # "UP主名": mid,
    },
    # 采集数量
    "hot_limit": 20,       # 综合热门取多少条
    "region_limit": 15,    # 分区动态取多少条
    "search_limit": 10,    # 每个关键词搜索多少条
    "ranking_limit": 20,   # 排行榜取多少条
    # 输出
    "output_dir": "./monitor_output",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


def _ts_to_str(ts):
    """时间戳转字符串"""
    if not ts or not isinstance(ts, (int, float)):
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _clean_html(text: str) -> str:
    """清理HTML标签"""
    return re.sub(r"<[^>]+>", "", text) if text else ""


def _fmt_num(n) -> str:
    """格式化大数字: 12345 -> 1.2万"""
    if not isinstance(n, (int, float)):
        return str(n)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


def api_get(url: str, params: dict = None) -> dict | None:
    """统一的B站API请求"""
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data")
        else:
            logger.debug(f"API返回非0: code={data.get('code')} msg={data.get('message')} url={url}")
    except Exception as e:
        logger.warning(f"API请求失败: {url} -> {e}")
    return None


# ─── 数据采集模块 ────────────────────────────────────────────────────────

def fetch_hot_search() -> list[dict]:
    """获取B站热搜榜"""
    results = []
    data = api_get("https://s.search.bilibili.com/main/hotword")
    if data and isinstance(data, dict):
        for item in data.get("list", []):
            results.append({
                "rank": item.get("pos", 0),
                "keyword": item.get("keyword", ""),
                "show_name": item.get("show_name", item.get("keyword", "")),
                "icon": item.get("icon", ""),  # 热/新/爆 标记
            })
    return results


def fetch_popular(limit: int = 20) -> list[dict]:
    """获取综合热门视频"""
    results = []
    data = api_get("https://api.bilibili.com/x/web-interface/popular", {"pn": 1, "ps": limit})
    if data:
        for item in data.get("list", []):
            stat = item.get("stat", {})
            results.append({
                "source": "综合热门",
                "title": item.get("title", ""),
                "author": item.get("owner", {}).get("name", ""),
                "mid": item.get("owner", {}).get("mid", 0),
                "bvid": item.get("bvid", ""),
                "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                "cover": item.get("pic", ""),
                "play": stat.get("view", 0),
                "danmaku": stat.get("danmaku", 0),
                "like": stat.get("like", 0),
                "coin": stat.get("coin", 0),
                "favorite": stat.get("favorite", 0),
                "reply": stat.get("reply", 0),
                "share": stat.get("share", 0),
                "desc": (item.get("desc") or "")[:200],
                "pubdate": _ts_to_str(item.get("pubdate")),
                "duration": item.get("duration", 0),
                "tname": item.get("tname", ""),
                "rcmd_reason": item.get("rcmd_reason", {}).get("content", ""),
            })
    return results


def fetch_region_dynamic(rid: int, limit: int = 15) -> list[dict]:
    """获取指定分区的最新动态"""
    results = []
    data = api_get(
        "https://api.bilibili.com/x/web-interface/dynamic/region",
        {"rid": rid, "pn": 1, "ps": limit},
    )
    if data and data.get("archives"):
        for item in data["archives"][:limit]:
            stat = item.get("stat", {})
            results.append({
                "source": f"分区动态(rid={rid})",
                "title": item.get("title", ""),
                "author": item.get("owner", {}).get("name", ""),
                "mid": item.get("owner", {}).get("mid", 0),
                "bvid": item.get("bvid", ""),
                "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                "play": stat.get("view", 0),
                "danmaku": stat.get("danmaku", 0),
                "like": stat.get("like", 0),
                "reply": stat.get("reply", 0),
                "desc": (item.get("desc") or "")[:200],
                "pubdate": _ts_to_str(item.get("pubdate")),
                "tname": item.get("tname", ""),
            })
    return results


def fetch_region_ranking(rid: int = 4, limit: int = 20) -> list[dict]:
    """
    获取分区排行榜 (3日热门)
    使用分区搜索接口按综合热度排序
    """
    results = []
    data = api_get(
        "https://api.bilibili.com/x/web-interface/ranking/v2",
        {"rid": rid, "type": "all"},
    )
    if data and data.get("list"):
        for idx, item in enumerate(data["list"][:limit], 1):
            stat = item.get("stat", {})
            results.append({
                "source": "分区排行",
                "rank": idx,
                "title": item.get("title", ""),
                "author": item.get("owner", {}).get("name", ""),
                "mid": item.get("owner", {}).get("mid", 0),
                "bvid": item.get("bvid", ""),
                "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                "play": stat.get("view", 0),
                "danmaku": stat.get("danmaku", 0),
                "like": stat.get("like", 0),
                "coin": stat.get("coin", 0),
                "reply": stat.get("reply", 0),
                "score": item.get("score", 0),
                "pubdate": _ts_to_str(item.get("pubdate")),
                "tname": item.get("tname", ""),
            })
    return results


def fetch_search(keyword: str, limit: int = 10, order: str = "totalrank") -> list[dict]:
    """
    B站关键词搜索
    order: totalrank(综合) / click(播放) / pubdate(最新) / dm(弹幕) / stow(收藏)
    """
    results = []
    data = api_get(
        "https://api.bilibili.com/x/web-interface/search/type",
        {
            "keyword": keyword,
            "search_type": "video",
            "order": order,
            "page": 1,
            "pagesize": limit,
        },
    )
    if data and data.get("result"):
        for item in data["result"][:limit]:
            results.append({
                "source": f"搜索:{keyword}",
                "title": _clean_html(item.get("title", "")),
                "author": item.get("author", ""),
                "mid": item.get("mid", 0),
                "bvid": item.get("bvid", ""),
                "url": item.get("arcurl", ""),
                "play": item.get("play", 0),
                "danmaku": item.get("video_review", 0),
                "like": item.get("like", 0),
                "favorites": item.get("favorites", 0),
                "desc": _clean_html(item.get("description", ""))[:200],
                "pubdate": _ts_to_str(item.get("pubdate")),
                "tag": item.get("tag", ""),
            })
    return results


def fetch_up_videos(mid: int, limit: int = 10) -> list[dict]:
    """获取指定UP主的最新投稿"""
    results = []
    data = api_get(
        "https://api.bilibili.com/x/space/wbi/arc/search",
        {"mid": mid, "pn": 1, "ps": limit, "order": "pubdate"},
    )
    if data and data.get("list", {}).get("vlist"):
        for item in data["list"]["vlist"][:limit]:
            results.append({
                "source": f"UP主:{item.get('author', '')}",
                "title": item.get("title", ""),
                "author": item.get("author", ""),
                "mid": mid,
                "bvid": item.get("bvid", ""),
                "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                "play": item.get("play", 0),
                "comment": item.get("comment", 0),
                "desc": (item.get("description") or "")[:200],
                "pubdate": _ts_to_str(item.get("created")),
            })
    return results


# ─── 报告输出 ────────────────────────────────────────────────────────────

def print_hot_search(items: list[dict]):
    """打印热搜榜"""
    if not items:
        return
    table = Table(title="[bold magenta]B站实时热搜[/bold magenta]", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("热搜词", style="white")
    table.add_column("标记", style="red", width=6)

    for item in items:
        icon = item.get("icon", "")
        tag = ""
        if "hot" in icon:
            tag = "🔥热"
        elif "new" in icon:
            tag = "🆕新"
        table.add_row(str(item["rank"]), item["keyword"], tag)

    console.print(table)
    console.print()


def print_video_table(title: str, items: list[dict], max_rows: int = 15):
    """打印视频列表表格"""
    if not items:
        return

    table = Table(title=f"[bold]{title}[/bold] ({len(items)} 条)", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("标题", style="white", max_width=40)
    table.add_column("UP主", style="green", max_width=12)
    table.add_column("播放", style="cyan", width=8, justify="right")
    table.add_column("点赞", style="yellow", width=8, justify="right")
    table.add_column("弹幕", style="magenta", width=8, justify="right")
    table.add_column("发布时间", style="dim", width=16)

    for idx, item in enumerate(items[:max_rows], 1):
        rank = str(item.get("rank", idx))
        table.add_row(
            rank,
            str(item.get("title", ""))[:40],
            str(item.get("author", ""))[:12],
            _fmt_num(item.get("play", 0)),
            _fmt_num(item.get("like", 0)),
            _fmt_num(item.get("danmaku", item.get("video_review", 0))),
            str(item.get("pubdate", "")),
        )

    console.print(table)
    console.print()


def save_report(all_data: dict, output_dir: str) -> str:
    """保存JSON报告"""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"bilibili_game_{timestamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    return path


# ─── 主流程 ──────────────────────────────────────────────────────────────

def run_monitor(cfg: dict = None):
    """执行一轮B站游戏热点采集"""
    cfg = cfg or CONFIG
    all_data = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    console.print(Panel(
        f"[bold cyan]B站游戏行业热点监控[/bold cyan]\n时间: {now}",
        border_style="cyan",
    ))

    # 1️⃣ 热搜榜
    console.print("[yellow]▶ 获取B站热搜榜...[/yellow]")
    hot_search = fetch_hot_search()
    all_data["热搜榜"] = hot_search
    console.print(f"  ✓ 热搜: {len(hot_search)} 条")
    print_hot_search(hot_search)

    # 过滤出游戏相关热搜
    game_hot = [
        h for h in hot_search
        if any(kw in h["keyword"] for kw in ["游戏", "原神", "崩坏", "王者", "英雄联盟", "steam",
                                               "手游", "端游", "GTA", "任天堂", "PS", "Xbox",
                                               "绝区零", "鸣潮", "黑神话", "LOL", "赛事"])
    ]
    if game_hot:
        console.print(f"  [bold green]🎮 游戏相关热搜: {', '.join(h['keyword'] for h in game_hot)}[/bold green]")
    all_data["游戏相关热搜"] = game_hot

    # 2️⃣ 综合热门(筛选游戏相关)
    console.print("\n[yellow]▶ 获取综合热门...[/yellow]")
    popular = fetch_popular(cfg["hot_limit"])
    game_popular = [
        v for v in popular
        if v.get("tname", "") in ("单机游戏", "电子竞技", "手机游戏", "网络游戏", "音游", "游戏")
        or any(kw in v.get("title", "") for kw in ["游戏", "steam", "原神", "黑神话"])
    ]
    all_data["综合热门(游戏)"] = game_popular
    console.print(f"  ✓ 热门: {len(popular)} 条, 其中游戏相关: {len(game_popular)} 条")
    if game_popular:
        print_video_table("综合热门 · 游戏相关", game_popular, 10)

    # 3️⃣ 各游戏分区动态
    console.print("[yellow]▶ 获取游戏分区动态...[/yellow]")
    for name, rid in cfg["game_regions"].items():
        if rid == 4:
            continue  # 主分区跳过，用排行榜替代
        videos = fetch_region_dynamic(rid, cfg["region_limit"])
        all_data[f"分区-{name}"] = videos
        console.print(f"  ✓ {name}: {len(videos)} 条")
        time.sleep(0.3)

    # 4️⃣ 游戏区排行榜
    console.print("\n[yellow]▶ 获取游戏区排行榜...[/yellow]")
    ranking = fetch_region_ranking(4, cfg["ranking_limit"])
    all_data["游戏区排行榜"] = ranking
    console.print(f"  ✓ 排行榜: {len(ranking)} 条")
    print_video_table("游戏区排行榜 TOP20", ranking, 20)

    # 5️⃣ 关键词搜索
    console.print("[yellow]▶ 关键词搜索...[/yellow]")
    for kw in cfg["keywords"]:
        videos = fetch_search(kw, cfg["search_limit"])
        all_data[f"搜索-{kw}"] = videos
        console.print(f"  ✓ 「{kw}」: {len(videos)} 条")
        time.sleep(1.5)  # B站搜索接口有风控，间隔需>1秒

    # 6️⃣ UP主追踪
    if cfg.get("up_mids"):
        console.print("\n[yellow]▶ UP主最新投稿...[/yellow]")
        for name, mid in cfg["up_mids"].items():
            videos = fetch_up_videos(mid, 5)
            all_data[f"UP主-{name}"] = videos
            console.print(f"  ✓ {name}: {len(videos)} 条")
            time.sleep(0.3)

    # 📊 汇总
    total = sum(len(v) for v in all_data.values() if isinstance(v, list))
    report_path = save_report(all_data, cfg["output_dir"])

    # 打印各分区的热门视频
    for name in ["单机游戏", "电子竞技", "手机游戏", "网络游戏"]:
        key = f"分区-{name}"
        if key in all_data and all_data[key]:
            print_video_table(f"分区 · {name}", all_data[key], 10)

    console.print(Panel(
        f"[bold green]采集完成[/bold green]\n"
        f"总条目: {total}\n"
        f"报告: {report_path}",
        border_style="green",
    ))

    return all_data, report_path


def run_daemon(interval_minutes: int = 60):
    """全天候循环监控"""
    console.print(Panel(
        f"[bold]全天候监控模式[/bold]\n"
        f"间隔: {interval_minutes} 分钟 | Ctrl+C 停止",
        border_style="green",
    ))

    cycle = 0
    while True:
        cycle += 1
        console.print(f"\n[bold]═══ 第 {cycle} 轮 ({datetime.now():%H:%M:%S}) ═══[/bold]")
        try:
            run_monitor()
        except Exception as e:
            logger.error(f"第 {cycle} 轮出错: {e}")
        console.print(f"[dim]下次: {interval_minutes} 分钟后[/dim]")
        time.sleep(interval_minutes * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="B站游戏行业热点监控")
    parser.add_argument("--mode", choices=["once", "daemon"], default="once",
                        help="once=单次 | daemon=循环")
    parser.add_argument("--interval", type=int, default=60,
                        help="循环间隔(分钟)")
    parser.add_argument("--keywords", nargs="+",
                        help="自定义关键词")
    parser.add_argument("--output", default="./monitor_output",
                        help="输出目录")
    args = parser.parse_args()

    if args.keywords:
        CONFIG["keywords"] = args.keywords
    CONFIG["output_dir"] = args.output

    if args.mode == "daemon":
        run_daemon(args.interval)
    else:
        run_monitor()


if __name__ == "__main__":
    main()
