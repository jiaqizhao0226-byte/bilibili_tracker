#!/usr/bin/env python3
"""
增强采集脚本：翻多页综合热门 + 各分区排行榜，生成 latest.json
解决B站搜索API反爬、分区动态API失效的问题
"""
import json
import time
from datetime import datetime
from pathlib import Path

import requests
from loguru import logger

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

GAME_TAGS = {"单机游戏", "电子竞技", "手机游戏", "网络游戏", "音游", "游戏"}
GAME_KWS = {"游戏", "steam", "原神", "黑神话", "崩坏", "王者", "英雄联盟", "GTA", "鸣潮", "绝区零", "鸣"}


def api_get(url, params=None):
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data")
        logger.debug(f"API code={data.get('code')} msg={data.get('message')} url={url}")
    except Exception as e:
        logger.warning(f"API失败: {url} -> {e}")
    return None


def parse_popular_item(item):
    stat = item.get("stat", {})
    pub_ts = item.get("pubdate", 0)
    return {
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
        "pubdate": datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d %H:%M") if pub_ts else "",
        "pubdate_ts": pub_ts,
        "duration": item.get("duration", 0),
        "tname": item.get("tname", ""),
        "rcmd_reason": item.get("rcmd_reason", {}).get("content", ""),
    }


def parse_ranking_item(idx, item):
    stat = item.get("stat", {})
    pub_ts = item.get("pubdate", 0)
    return {
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
        "pubdate": datetime.fromtimestamp(pub_ts).strftime("%Y-%m-%d %H:%M") if pub_ts else "",
        "pubdate_ts": pub_ts,
        "tname": item.get("tname", ""),
    }


def is_game_related(v):
    if v.get("tname", "") in GAME_TAGS:
        return True
    title = v.get("title", "")
    return any(kw in title for kw in GAME_KWS)


def fetch_hot_search():
    results = []
    data = api_get("https://s.search.bilibili.com/main/hotword")
    if data and isinstance(data, dict):
        for item in data.get("list", []):
            results.append({
                "rank": item.get("pos", 0),
                "keyword": item.get("keyword", ""),
                "show_name": item.get("show_name", item.get("keyword", "")),
                "icon": item.get("icon", ""),
            })
    return results


def fetch_popular_multi(pages=10, ps=50):
    """翻多页综合热门，过滤游戏相关"""
    all_items = []
    for pn in range(1, pages + 1):
        data = api_get("https://api.bilibili.com/x/web-interface/popular", {"pn": pn, "ps": ps})
        if not data or not data.get("list"):
            logger.info(f"热门第{pn}页无数据，停止翻页")
            break
        all_items.extend(data["list"])
        logger.info(f"热门第{pn}页: {len(data['list'])} 条")
        time.sleep(0.4)
    game_videos = [parse_popular_item(v) for v in all_items if is_game_related(v)]
    return game_videos, len(all_items)


def fetch_region_ranking(rid, limit=30):
    results = []
    data = api_get("https://api.bilibili.com/x/web-interface/ranking/v2", {"rid": rid, "type": "all"})
    if data and data.get("list"):
        for idx, item in enumerate(data["list"][:limit], 1):
            results.append(parse_ranking_item(idx, item))
    return results


def main():
    logger.info("=== 增强采集开始 ===")
    all_data = {
        "meta": {
            "collect_time": datetime.now().isoformat(),
            "config": {
                "keywords": ["游戏新闻", "新游", "手游推荐", "steam", "原神", "崩坏星穹铁道",
                             "王者荣耀", "英雄联盟", "GTA6", "任天堂", "独立游戏", "黑神话悟空"],
                "game_regions": {"单机游戏": 17, "电子竞技": 171, "手机游戏": 172, "网络游戏": 65},
            },
        },
        "hot_search": [],
        "popular": [],
        "regions": {},
        "ranking": [],
        "search": {},
        "up_videos": {},
    }

    # 热搜
    all_data["hot_search"] = fetch_hot_search()
    logger.info(f"热搜: {len(all_data['hot_search'])} 条")

    # 综合热门 - 翻10页
    game_popular, total_popular = fetch_popular_multi(pages=10, ps=50)
    all_data["popular"] = game_popular
    logger.info(f"综合热门(游戏): {len(game_popular)}/{total_popular}")

    # 各分区排行榜
    region_map = {"单机游戏": 17, "电子竞技": 171, "手机游戏": 172, "网络游戏": 65}
    for name, rid in region_map.items():
        videos = fetch_region_ranking(rid, 20)
        all_data["regions"][name] = videos
        logger.info(f"分区 {name}(rid={rid}): {len(videos)} 条")
        time.sleep(0.3)

    # 游戏主分区排行
    all_data["ranking"] = fetch_region_ranking(4, 30)
    logger.info(f"排行榜(rid=4): {len(all_data['ranking'])} 条")

    # 保存 latest.json
    save_path = DATA_DIR / "latest.json"
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"已保存: {save_path}")

    # 保存历史快照
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    history_dir = DATA_DIR / "history"
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"snapshot_{ts}.json"
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"历史快照: {history_path}")

    # 汇总
    total = len(game_popular) + len(all_data["ranking"])
    for vs in all_data["regions"].values():
        total += len(vs)
    logger.info(f"=== 采集完成，共 {total} 条视频 ===")
    return all_data


if __name__ == "__main__":
    main()
