#!/usr/bin/env python3
"""
B站游戏热点监控看板 - 后端服务
- 每小时自动采集B站游戏热点数据
- 提供 REST API 给前端看板
- 支持飞书/企业微信/钉钉 Webhook 推送爆款提醒
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from loguru import logger

# ─── 路径 ────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "data" / "config.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ─── 默认配置 ─────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "keywords": [
        "游戏新闻", "新游", "手游推荐", "steam",
        "原神", "崩坏星穹铁道", "王者荣耀", "英雄联盟",
        "GTA6", "任天堂", "独立游戏", "黑神话悟空",
    ],
    "game_regions": {
        "单机游戏": 17,
        "电子竞技": 171,
        "手机游戏": 172,
        "网络游戏": 65,
    },
    "up_mids": {},
    "hot_limit": 30,
    "region_limit": 20,
    "search_limit": 15,
    "ranking_limit": 30,
    "max_age_days": 7,
    "interval_minutes": 60,
    "notify": {
        "enabled": False,
        "type": "feishu",
        "webhook_url": "",
        "play_threshold": 500000,
        "like_threshold": 50000,
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


# ─── 配置管理 ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 合并默认值
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
                elif isinstance(v, dict) and isinstance(cfg[k], dict):
                    for kk, vv in v.items():
                        if kk not in cfg[k]:
                            cfg[k][kk] = vv
            return cfg
        except Exception as e:
            logger.warning(f"配置加载失败，使用默认配置: {e}")
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ─── B站 API ──────────────────────────────────────────────────────────────

def api_get(url: str, params: dict = None) -> dict | None:
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data")
        else:
            logger.debug(f"API code={data.get('code')} msg={data.get('message')}")
    except Exception as e:
        logger.warning(f"API失败: {url} -> {e}")
    return None


def _ts_to_str(ts):
    if not ts or not isinstance(ts, (int, float)):
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text) if text else ""


def fetch_hot_search() -> list[dict]:
    results = []
    data = api_get("https://s.search.bilibili.com/main/hotword")
    if data and isinstance(data, dict):
        for item in data.get("list", []):
            results.append({
                "rank": item.get("pos", 0),
                "keyword": item.get("keyword", ""),
                "icon": item.get("icon", ""),
            })
    return results


def fetch_popular(limit: int = 30) -> list[dict]:
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
                "pubdate_ts": item.get("pubdate", 0),
                "duration": item.get("duration", 0),
                "tname": item.get("tname", ""),
                "rcmd_reason": item.get("rcmd_reason", {}).get("content", ""),
            })
    return results


def fetch_region_dynamic(rid: int, limit: int = 20) -> list[dict]:
    results = []
    data = api_get(
        "https://api.bilibili.com/x/web-interface/dynamic/region",
        {"rid": rid, "pn": 1, "ps": limit},
    )
    if data and data.get("archives"):
        for item in data["archives"][:limit]:
            stat = item.get("stat", {})
            results.append({
                "source": f"分区动态",
                "title": item.get("title", ""),
                "author": item.get("owner", {}).get("name", ""),
                "mid": item.get("owner", {}).get("mid", 0),
                "bvid": item.get("bvid", ""),
                "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                "cover": item.get("pic", ""),
                "play": stat.get("view", 0),
                "danmaku": stat.get("danmaku", 0),
                "like": stat.get("like", 0),
                "reply": stat.get("reply", 0),
                "desc": (item.get("desc") or "")[:200],
                "pubdate": _ts_to_str(item.get("pubdate")),
                "pubdate_ts": item.get("pubdate", 0),
                "tname": item.get("tname", ""),
            })
    return results


def fetch_region_ranking(rid: int = 4, limit: int = 30) -> list[dict]:
    results = []
    data = api_get(
        "https://api.bilibili.com/x/web-interface/ranking/v2",
        {"rid": rid, "type": "all"},
    )
    if data and data.get("list"):
        for idx, item in enumerate(data["list"][:limit], 1):
            stat = item.get("stat", {})
            results.append({
                "source": "排行榜",
                "rank": idx,
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
                "reply": stat.get("reply", 0),
                "score": item.get("score", 0),
                "pubdate": _ts_to_str(item.get("pubdate")),
                "pubdate_ts": item.get("pubdate", 0),
                "tname": item.get("tname", ""),
            })
    return results


def fetch_search(keyword: str, limit: int = 15, order: str = "pubdate") -> list[dict]:
    """
    B站关键词搜索
    order 默认改为 pubdate（最新发布），确保只拿到新视频
    可选: pubdate(最新) / click(播放) / dm(弹幕) / stow(收藏)
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
                "cover": item.get("pic", ""),
                "play": item.get("play", 0),
                "danmaku": item.get("video_review", 0),
                "like": item.get("like", 0),
                "favorites": item.get("favorites", 0),
                "desc": _clean_html(item.get("description", ""))[:200],
                "pubdate": _ts_to_str(item.get("pubdate")),
                "pubdate_ts": item.get("pubdate", 0),
                "tag": item.get("tag", ""),
            })
    return results


def fetch_up_videos(mid: int, limit: int = 10) -> list[dict]:
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
                "cover": item.get("pic", ""),
                "play": item.get("play", 0),
                "comment": item.get("comment", 0),
                "desc": (item.get("description") or "")[:200],
                "pubdate": _ts_to_str(item.get("created")),
                "pubdate_ts": item.get("created", 0),
            })
    return results


# ─── 推送通知 ──────────────────────────────────────────────────────────────

def send_notification(title: str, items: list[dict], cfg: dict):
    """发送 Webhook 通知"""
    notify_cfg = cfg.get("notify", {})
    if not notify_cfg.get("enabled") or not notify_cfg.get("webhook_url"):
        return

    webhook_url = notify_cfg["webhook_url"]
    notify_type = notify_cfg.get("type", "feishu")

    # 构造消息内容
    lines = [f"🎮 {title}", f"时间: {datetime.now():%Y-%m-%d %H:%M}", ""]
    for item in items[:10]:
        play = item.get("play", 0)
        play_str = f"{play/10000:.1f}万" if play >= 10000 else str(play)
        lines.append(f"▸ [{play_str}播放] {item['title']}")
        lines.append(f"  UP: {item.get('author', '')} | {item.get('url', '')}")
        lines.append("")

    text = "\n".join(lines)

    try:
        if notify_type == "feishu":
            payload = {
                "msg_type": "text",
                "content": {"text": text}
            }
        elif notify_type == "wecom":
            payload = {
                "msgtype": "text",
                "text": {"content": text}
            }
        elif notify_type == "dingtalk":
            payload = {
                "msgtype": "text",
                "text": {"content": text}
            }
        else:
            payload = {"text": text}

        resp = requests.post(webhook_url, json=payload, timeout=10)
        logger.info(f"通知发送成功: {resp.status_code}")
    except Exception as e:
        logger.warning(f"通知发送失败: {e}")


# ─── 数据采集 ──────────────────────────────────────────────────────────────

def run_collect(cfg: dict = None) -> dict:
    """执行一轮完整采集"""
    if cfg is None:
        cfg = load_config()

    logger.info("开始采集B站游戏热点...")
    all_data = {
        "meta": {
            "collect_time": datetime.now().isoformat(),
            "config": {
                "keywords": cfg["keywords"],
                "game_regions": cfg["game_regions"],
            }
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

    # 综合热门
    popular = fetch_popular(cfg.get("hot_limit", 30))
    game_tags = {"单机游戏", "电子竞技", "手机游戏", "网络游戏", "音游", "游戏"}
    game_kws = {"游戏", "steam", "原神", "黑神话", "崩坏", "王者", "英雄联盟", "GTA"}
    game_popular = [
        v for v in popular
        if v.get("tname", "") in game_tags
        or any(kw in v.get("title", "") for kw in game_kws)
    ]
    all_data["popular"] = game_popular
    logger.info(f"综合热门(游戏): {len(game_popular)}/{len(popular)}")

    # 分区动态
    for name, rid in cfg.get("game_regions", {}).items():
        videos = fetch_region_dynamic(rid, cfg.get("region_limit", 20))
        all_data["regions"][name] = videos
        logger.info(f"分区 {name}: {len(videos)} 条")
        time.sleep(0.3)

    # 排行榜
    all_data["ranking"] = fetch_region_ranking(4, cfg.get("ranking_limit", 30))
    logger.info(f"排行榜: {len(all_data['ranking'])} 条")

    # 关键词搜索
    for kw in cfg.get("keywords", []):
        videos = fetch_search(kw, cfg.get("search_limit", 15))
        all_data["search"][kw] = videos
        logger.info(f"搜索 [{kw}]: {len(videos)} 条")
        time.sleep(1.5)

    # UP主
    for name, mid in cfg.get("up_mids", {}).items():
        videos = fetch_up_videos(mid, 10)
        all_data["up_videos"][name] = videos
        logger.info(f"UP主 {name}: {len(videos)} 条")
        time.sleep(0.3)

    # 保存数据
    save_path = DATA_DIR / "latest.json"
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)

    # 保存历史快照
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    history_dir = DATA_DIR / "history"
    history_dir.mkdir(exist_ok=True)
    history_path = history_dir / f"snapshot_{ts}.json"
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2, default=str)

    # 清理7天前的历史
    cutoff = datetime.now() - timedelta(days=7)
    for p in history_dir.glob("snapshot_*.json"):
        try:
            file_ts = datetime.strptime(p.stem.replace("snapshot_", ""), "%Y%m%d_%H%M%S")
            if file_ts < cutoff:
                p.unlink()
        except Exception:
            pass

    # 推送通知 - 找出爆款视频
    play_threshold = cfg.get("notify", {}).get("play_threshold", 500000)
    like_threshold = cfg.get("notify", {}).get("like_threshold", 50000)

    # 汇总所有视频，找爆款
    all_videos = []
    all_videos.extend(all_data["popular"])
    all_videos.extend(all_data["ranking"])
    for vs in all_data["regions"].values():
        all_videos.extend(vs)
    for vs in all_data["search"].values():
        all_videos.extend(vs)

    # 去重 by bvid + 只保留7天内的视频
    now_ts = time.time()
    max_age = cfg.get("max_age_days", 7) * 86400
    seen = set()
    unique_videos = []
    dropped_old = 0
    for v in all_videos:
        bvid = v.get("bvid", "")
        if not bvid or bvid in seen:
            continue
        seen.add(bvid)
        pub_ts = v.get("pubdate_ts", 0)
        if pub_ts and pub_ts > 0 and (now_ts - pub_ts) > max_age:
            dropped_old += 1
            continue
        # 计算"新鲜爆款"分数: 播放量 / 视频年龄(小时)
        age_hours = max((now_ts - pub_ts) / 3600, 1) if pub_ts else 9999
        v["freshness_score"] = round(v.get("play", 0) / age_hours)
        unique_videos.append(v)

    if dropped_old:
        logger.info(f"过滤掉 {dropped_old} 条超过 {cfg.get('max_age_days', 7)} 天的老视频")

    # 24h 内的爆款 -> 推送通知
    hot_videos = [
        v for v in unique_videos
        if (v.get("play", 0) >= play_threshold or v.get("like", 0) >= like_threshold)
        and v.get("pubdate_ts", 0) > 0
        and (now_ts - v["pubdate_ts"]) < 86400
    ]
    hot_videos.sort(key=lambda x: x.get("play", 0), reverse=True)

    if hot_videos:
        logger.info(f"发现 {len(hot_videos)} 个爆款视频，准备推送")
        send_notification("B站游戏爆款速报", hot_videos, cfg)

    total = len(unique_videos)
    logger.info(f"采集完成，共 {total} 条唯一视频 (过滤老视频 {dropped_old} 条)")
    return all_data


# ─── 定时任务 ──────────────────────────────────────────────────────────────

class MonitorScheduler:
    def __init__(self):
        self.running = False
        self.thread = None
        self.last_collect_time = None
        self.next_collect_time = None
        self.status = "idle"
        self.collect_count = 0

    def start(self, interval_minutes: int = 60):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, args=(interval_minutes,), daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self, interval_minutes: int):
        while self.running:
            self.status = "collecting"
            self.collect_count += 1
            try:
                cfg = load_config()
                run_collect(cfg)
                self.last_collect_time = datetime.now().isoformat()
            except Exception as e:
                logger.error(f"采集出错: {e}")
                self.status = f"error: {e}"
            self.status = "waiting"
            self.next_collect_time = (
                datetime.now() + timedelta(minutes=interval_minutes)
            ).isoformat()
            # 等待，但可以被中断
            for _ in range(interval_minutes * 60):
                if not self.running:
                    break
                time.sleep(1)

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "status": self.status,
            "collect_count": self.collect_count,
            "last_collect_time": self.last_collect_time,
            "next_collect_time": self.next_collect_time,
        }


scheduler = MonitorScheduler()


# ─── HTTP 服务 ─────────────────────────────────────────────────────────────

class DashboardHandler(SimpleHTTPRequestHandler):
    """处理看板的 HTTP 请求"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/data":
            self._serve_data(parse_qs(parsed.query))
        elif path == "/api/overview":
            self._serve_overview(parse_qs(parsed.query))
        elif path == "/api/config":
            self._json_response(load_config())
        elif path == "/api/status":
            self._json_response(scheduler.get_status())
        elif path == "/api/collect":
            # 手动触发采集
            threading.Thread(target=self._do_collect, daemon=True).start()
            self._json_response({"ok": True, "message": "采集已启动"})
        elif path == "/api/search_live":
            self._serve_search_live(parse_qs(parsed.query))
        elif path == "/api/pv_timeline":
            self._serve_pv_timeline(parse_qs(parsed.query))
        elif path == "/api/history":
            self._serve_history()
        elif path == "/" or path == "/index.html":
            self._serve_file(BASE_DIR / "index.html", "text/html")
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else "{}"

        if path == "/api/config":
            try:
                new_cfg = json.loads(body)
                # 合并而非替换
                cfg = load_config()
                cfg.update(new_cfg)
                save_config(cfg)
                self._json_response({"ok": True, "config": cfg})
            except Exception as e:
                self._json_response({"ok": False, "error": str(e)}, 400)
        elif path == "/api/scheduler/start":
            cfg = load_config()
            interval = cfg.get("interval_minutes", 60)
            scheduler.start(interval)
            self._json_response({"ok": True, "interval": interval})
        elif path == "/api/scheduler/stop":
            scheduler.stop()
            self._json_response({"ok": True})
        else:
            self._json_response({"error": "Not found"}, 404)

    def _serve_data(self, query: dict):
        data_path = DATA_DIR / "latest.json"
        if not data_path.exists():
            self._json_response({"error": "暂无数据，请先执行采集"}, 404)
            return

        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 筛选参数
        min_play = int(query.get("min_play", [100000])[0])
        min_like = int(query.get("min_like", [0])[0])
        sort_by = query.get("sort", ["freshness"])[0]  # 默认按新鲜度排序
        source_filter = query.get("source", [None])[0]
        keyword_filter = query.get("keyword", [None])[0]
        # 默认只展示 72h 内的视频
        hours = int(query.get("hours", [72])[0])

        # 汇总所有视频
        all_videos = []
        all_videos.extend(data.get("popular", []))
        all_videos.extend(data.get("ranking", []))
        for vs in data.get("regions", {}).values():
            all_videos.extend(vs)
        for vs in data.get("search", {}).values():
            all_videos.extend(vs)
        for vs in data.get("up_videos", {}).values():
            all_videos.extend(vs)

        # 去重
        now_ts = time.time()
        seen = set()
        unique = []
        for v in all_videos:
            bvid = v.get("bvid", "")
            if bvid and bvid not in seen:
                seen.add(bvid)
                # 计算新鲜度分数（如果尚未计算）
                if "freshness_score" not in v:
                    pub_ts = v.get("pubdate_ts", 0)
                    age_hours = max((now_ts - pub_ts) / 3600, 1) if pub_ts else 9999
                    v["freshness_score"] = round(v.get("play", 0) / age_hours)
                unique.append(v)

        # 时间过滤 (hours=0 表示不限)
        if hours > 0:
            cutoff_ts = now_ts - hours * 3600
            unique = [v for v in unique
                      if v.get("pubdate_ts", 0) >= cutoff_ts
                      or v.get("pubdate_ts", 0) == 0]

        # 其他筛选
        if min_play:
            unique = [v for v in unique if v.get("play", 0) >= min_play]
        if min_like:
            unique = [v for v in unique if v.get("like", 0) >= min_like]
        if source_filter:
            unique = [v for v in unique if source_filter in v.get("source", "")]
        if keyword_filter:
            unique = [v for v in unique
                      if keyword_filter.lower() in v.get("title", "").lower()
                      or keyword_filter.lower() in v.get("desc", "").lower()
                      or keyword_filter.lower() in v.get("tag", "").lower()]

        # 排序
        if sort_by == "freshness":
            unique.sort(key=lambda x: x.get("freshness_score", 0), reverse=True)
        elif sort_by == "play":
            unique.sort(key=lambda x: x.get("play", 0), reverse=True)
        elif sort_by == "like":
            unique.sort(key=lambda x: x.get("like", 0), reverse=True)
        elif sort_by == "danmaku":
            unique.sort(key=lambda x: x.get("danmaku", 0), reverse=True)
        elif sort_by == "pubdate":
            unique.sort(key=lambda x: x.get("pubdate_ts", 0), reverse=True)
        elif sort_by == "reply":
            unique.sort(key=lambda x: x.get("reply", 0), reverse=True)

        result = {
            "videos": unique,
            "total": len(unique),
            "hot_search": data.get("hot_search", []),
            "meta": data.get("meta", {}),
        }

        self._json_response(result)

    def _serve_overview(self, query: dict):
        """总览 - 近一周监控汇总，聚合所有历史快照数据"""
        days = int(query.get("days", [7])[0])
        now_ts = time.time()
        cutoff_ts = now_ts - days * 86400

        # 收集所有历史快照 + latest.json
        history_dir = DATA_DIR / "history"
        data_files = []
        latest_path = DATA_DIR / "latest.json"
        if latest_path.exists():
            data_files.append(latest_path)
        if history_dir.exists():
            data_files.extend(sorted(history_dir.glob("snapshot_*.json"), reverse=True))

        all_videos = []
        seen_bvids = set()
        hot_search = []
        latest_meta = {}
        collect_times = []

        for fpath in data_files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            # 记录采集时间
            ct = data.get("meta", {}).get("collect_time", "")
            if ct:
                collect_times.append(ct)

            if not latest_meta and data.get("meta"):
                latest_meta = data["meta"]

            if not hot_search and data.get("hot_search"):
                hot_search = data["hot_search"]

            # 汇总所有视频
            for source_key in ["popular", "ranking"]:
                for v in data.get(source_key, []):
                    bvid = v.get("bvid", "")
                    if bvid and bvid not in seen_bvids:
                        seen_bvids.add(bvid)
                        all_videos.append(v)

            for vs in data.get("regions", {}).values():
                for v in vs:
                    bvid = v.get("bvid", "")
                    if bvid and bvid not in seen_bvids:
                        seen_bvids.add(bvid)
                        all_videos.append(v)

            for vs in data.get("search", {}).values():
                for v in vs:
                    bvid = v.get("bvid", "")
                    if bvid and bvid not in seen_bvids:
                        seen_bvids.add(bvid)
                        all_videos.append(v)

            for vs in data.get("up_videos", {}).values():
                for v in vs:
                    bvid = v.get("bvid", "")
                    if bvid and bvid not in seen_bvids:
                        seen_bvids.add(bvid)
                        all_videos.append(v)

        # 时间过滤 + 计算新鲜度
        filtered = []
        for v in all_videos:
            pub_ts = v.get("pubdate_ts", 0)
            if pub_ts and pub_ts > 0 and pub_ts < cutoff_ts:
                continue
            age_hours = max((now_ts - pub_ts) / 3600, 1) if pub_ts else 9999
            v["freshness_score"] = round(v.get("play", 0) / age_hours)
            filtered.append(v)

        # 按新鲜度排序
        filtered.sort(key=lambda x: x.get("freshness_score", 0), reverse=True)

        # 统计：每日爆款数趋势
        day_stats = {}
        for v in filtered:
            pub_ts = v.get("pubdate_ts", 0)
            if not pub_ts:
                continue
            day_key = datetime.fromtimestamp(pub_ts).strftime("%m-%d")
            if day_key not in day_stats:
                day_stats[day_key] = {"total": 0, "hot": 0, "max_play": 0}
            day_stats[day_key]["total"] += 1
            if v.get("play", 0) >= 500000:
                day_stats[day_key]["hot"] += 1
            day_stats[day_key]["max_play"] = max(day_stats[day_key]["max_play"], v.get("play", 0))

        hot_count = len([v for v in filtered if v.get("play", 0) >= 500000])
        total_play = sum(v.get("play", 0) for v in filtered)

        self._json_response({
            "videos": filtered,
            "total": len(filtered),
            "hot_count": hot_count,
            "total_play": total_play,
            "hot_search": hot_search,
            "meta": latest_meta,
            "day_stats": day_stats,
            "collect_count": len(collect_times),
            "days": days,
        })

    def _serve_search_live(self, query: dict):
        """实时搜索B站 - 前端手动输入关键词时调用"""
        keyword = query.get("keyword", [None])[0]
        if not keyword:
            self._json_response({"error": "缺少keyword参数"}, 400)
            return

        order = query.get("order", ["pubdate"])[0]
        limit = int(query.get("limit", [30])[0])
        min_play = int(query.get("min_play", [100000])[0])

        logger.info(f"实时搜索: [{keyword}] order={order} limit={limit}")
        results = fetch_search(keyword, limit=min(limit, 50), order=order)

        # 计算新鲜度分数
        now_ts = time.time()
        for v in results:
            pub_ts = v.get("pubdate_ts", 0)
            age_hours = max((now_ts - pub_ts) / 3600, 1) if pub_ts else 9999
            v["freshness_score"] = round(v.get("play", 0) / age_hours)

        # 过滤最低播放量
        if min_play > 0:
            results = [v for v in results if v.get("play", 0) >= min_play]

        self._json_response({
            "keyword": keyword,
            "videos": results,
            "total": len(results),
        })

    # ── 新游PV过滤规则 ──────────────────────────────────────────────────

    # 权威游戏媒体/官方账号关键词（UP主名中包含即命中）
    TRUSTED_PUBLISHERS = [
        # 国际游戏媒体
        "IGN", "GameSpot", "Fami通", "Game Informer", "Kotaku",
        "PlayStation", "Xbox", "Nintendo", "任天堂",
        "Steam", "Epic Games", "EA", "育碧", "Ubisoft",
        # 国内权威游戏媒体
        "机核", "游民星空", "3DM", "游侠网", "17173", "游研社",
        "篝火营地", "VGtime", "游戏时光", "触乐", "游戏葡萄",
        "GameLook", "游戏陀螺", "BB姬", "游戏日报",
        "A9VG", "电玩巴士", "游戏机实用技术", "UCG",
        "NGA玩家社区", "TapTap",
        # 游戏厂商官方
        "米哈游", "miHoYo", "HoYoverse", "网易游戏", "腾讯游戏",
        "完美世界", "西山居", "鹰角网络", "叠纸游戏", "莉莉丝",
        "散爆网络", "库洛游戏", "深蓝互动", "烛龙",
        "Capcom", "卡普空", "SE", "Square Enix", "史克威尔艾尼克斯",
        "Bandai Namco", "万代南梦宫", "SEGA", "世嘉",
        "FromSoftware", "Bethesda", "Rockstar", "CD Projekt",
        "Valve", "Blizzard", "暴雪", "Riot", "拳头",
        "KONAMI", "科乐美", "ATLUS", "Falcom",
        "索尼", "Sony", "微软", "Microsoft",
        # B站官方游戏频道
        "哔哩哔哩游戏", "B站游戏",
    ]

    # 已有知名游戏名（运营更新类，非全新游戏）
    EXISTING_GAME_NAMES = [
        # 米哈游
        "原神", "崩坏星穹铁道", "崩坏3", "崩铁", "绝区零",
        # 腾讯
        "王者荣耀", "英雄联盟", "LOL", "和平精英", "PUBG",
        "穿越火线", "CF", "逆战", "逆战未来", "QQ飞车", "QQ炫舞",
        "天涯明月刀", "火影忍者", "DNF", "地下城与勇士",
        "使命召唤手游", "金铲铲之战", "英雄联盟手游",
        # 网易
        "第五人格", "阴阳师", "梦幻西游", "大话西游", "逆水寒",
        "永劫无间", "蛋仔派对", "光遇", "荒野行动",
        "明日之后", "一梦江湖", "天谕", "倩女幽魂",
        # 其他国产
        "明日方舟", "碧蓝航线", "碧蓝幻想",
        "幻塔", "鸣潮", "尘白禁区", "战双帕弥什",
        "少女前线", "摩尔庄园", "奥比岛",
        "剑网3", "剑侠情缘", "诛仙", "完美世界",
        "梦幻新诛仙", "神武", "问道", "征途",
        "三国杀", "狼人杀", "率土之滨",
        # FPS/竞技
        "使命召唤", "COD", "Apex", "APEX", "Valorant", "无畏契约",
        "CS2", "CSGO", "Dota2", "DOTA2", "彩虹六号",
        "守望先锋", "Overwatch",
        # 体育
        "FIFA", "NBA2K", "NBA 2K", "EA FC",
        # 国际大作
        "Minecraft", "我的世界", "Roblox",
        "堡垒之夜", "Fortnite",
        "暗黑破坏神", "魔兽世界", "炉石传说", "星际争霸",
        "最终幻想14", "FF14", "最终幻想7",
        "怪物猎人", "荒野大镖客", "GTA5", "GTA Online",
        "艾尔登法环", "只狼", "黑暗之魂",
        "生化危机", "鬼泣", "怪物猎人世界",
        "宝可梦", "Pokemon", "塞尔达", "马里奥",
        "动物之森", "喷射战士", "异度神剑",
        # 手游/二次元
        "赛马娘", "FGO", "Fate", "公主连结",
        "BanG Dream", "偶像梦幻祭", "Love Live",
        "恋与制作人", "光与夜之恋", "未定事件簿",
        "坎公骑冠剑", "放置少女",
        # 策略
        "三国志", "信长之野望", "文明6", "Civilization",
        "全面战争", "欧陆风云", "十字军之王",
        "星穹铁道",
        # 射击/战术（近期热门）
        "三角洲行动", "三角洲", "Delta Force",
        "暗区突围", "PUBG Mobile", "无畏契约手游",
        "黑神话悟空", "黑神话",
        "绝地求生",
    ]

    # 标题中表示"已有游戏运营更新"的关键词
    EXISTING_GAME_UPDATE_KEYWORDS = [
        # 版本/赛季
        "新赛季", "赛季更新", "版本更新", "新版本", "周年庆",
        "S赛季", "大版本",
        # 角色/皮肤/装备
        "新角色", "新皮肤", "皮肤展示", "新英雄", "新武器",
        "新宠物", "宠物系统", "新坐骑", "坐骑系统",
        "新装备", "新道具", "新时装", "新外观",
        "至臻", "传说皮肤", "限定皮肤", "史诗皮肤",
        # 活动/抽卡
        "联动活动", "限时活动", "新活动",
        "卡池", "UP池", "抽卡", "返场", "复刻", "寻访",
        "战令", "通行证", "赛季手册",
        # 玩法更新
        "新地图更新", "排位赛", "段位", "新模式", "新玩法",
        "新副本", "新关卡", "新赛道",
        # 运营
        "签到", "福利", "兑换码", "礼包",
        "维护公告", "停机维护", "更新公告",
        "返场活动", "周年活动", "春节活动", "暑期活动",
        # 系统更新
        "系统更新", "系统升级", "新系统曝光", "系统曝光",
        "全新系统", "系统改版",
    ]

    # 标题中表示"全新游戏首曝/首发"的关键词（加分项）
    NEW_GAME_SIGNAL_KEYWORDS = [
        "首曝", "首发", "全新", "新作", "公布", "正式公开",
        "正式发布", "首次公开", "全球首曝", "世界首曝",
        "新IP", "原创", "自研",
        "announce", "reveal", "announcement", "Announce", "Reveal",
        "Trailer", "trailer", "Official",
        "E3", "TGA", "TGS", "Gamescom", "科隆",
        "State of Play", "Nintendo Direct", "Xbox Showcase",
        "开发中", "研发中", "制作中",
        "概念PV", "先导PV", "概念预告",
    ]

    def _is_trusted_publisher(self, author: str) -> bool:
        """判断UP主是否为权威游戏媒体/官方账号"""
        if not author:
            return False
        author_lower = author.lower()
        for pub in self.TRUSTED_PUBLISHERS:
            if pub.lower() in author_lower:
                return True
        # 包含"官方""official"的也算
        if "官方" in author or "official" in author_lower:
            return True
        return False

    # 内容类型识别关键词（用于二次分类矫正）
    # 当视频通过"新游PV"搜索进来，但实际上是攻略/教学/直播等内容时，需要识别并过滤
    CONTENT_TYPE_PATTERNS = {
        "guide": {
            "name": "攻略/教学",
            # 标题中的强信号词（出现即高概率是攻略教学）
            "strong": [
                "攻略", "教学", "教程", "通关", "全流程", "无伤", "打法", "配队",
                "养成", "毕业", "阵容", "入门", "进阶", "手把手", "详解", "解析",
                "连招", "出装", "铭文", "技巧", "思路", "上分", "段位",
                "怎么玩", "怎么打", "怎么过", "如何", "萌新",
                "零氪", "平民", "必备", "最强",
                "全收集", "隐藏", "彩蛋", "成就",
            ],
            # 标题中的弱信号词（需要组合判断）
            "weak": [
                "实战", "单人", "solo", "杀", "撤离", "吃鸡",
                "一人", "纯", "打满", "满星", "满配",
                "推荐", "心得", "分享", "体验",
            ],
        },
        "gameplay": {
            "name": "实况/游玩",
            "strong": [
                "实况", "流程", "剧情", "速通", "挑战", "生存",
                "整活", "搞笑", "鬼畜", "名场面", "沙雕", "离谱",
                "世界纪录", "试毒",
                "模组", "MOD", "mod", "整合包",
            ],
            "weak": [
                "体验", "游玩", "试玩",
            ],
        },
        "streamer": {
            "name": "主播/赛事",
            "strong": [
                "直播", "切片", "赛事", "总决赛", "半决赛", "淘汰赛",
                "冠军", "战队", "选手", "职业", "MVP", "五杀", "超神",
                "翻盘", "绝杀", "集锦", "高光", "解说", "复盘",
                "KPL", "LPL", "TI", "S赛", "MSI", "世界赛",
            ],
            "weak": [
                "主播", "比赛", "联赛",
            ],
        },
        "opinion": {
            "name": "讨论/测评",
            "strong": [
                "盘点", "吐槽", "为什么", "怎么了", "凉了", "炸了",
                "差评", "暴雷", "翻车", "塌房", "争议", "维权",
                "现状", "深度", "分析", "对比",
                "氪金", "骗氪", "退款", "停服",
            ],
            "weak": [
                "看法", "观点", "评价", "讨论",
            ],
        },
    }

    # 标题中同时出现这些游戏名+玩法词汇 → 大概率是游戏攻略/实况而非PV
    # 这些游戏可能不在EXISTING_GAME_NAMES中（如新游/小众游），但标题语义是攻略类
    GAMEPLAY_INDICATOR_WORDS = [
        # 角色/英雄/武器名相关的修饰词（暗示在讲已有游戏内容）
        "纯正", "单排", "单三", "单人", "solo", "SOLO",
        "双排", "三排", "四排", "五排", "满配", "满星",
        # 战斗/成绩相关
        "杀", "击杀", "连杀", "十杀", "二十杀", "三十杀", "百杀",
        "吃鸡", "撤离", "通关", "过关", "黑屋", "大佬",
        "一人进图", "进图", "刷图", "下副本",
        "上分", "上王者", "上钻石", "上大师",
        "翻盘", "逆袭", "碾压", "横扫",
        # 明确的实操类
        "手残", "零失误", "无伤", "全满",
    ]

    def _detect_content_type(self, v: dict) -> dict:
        """
        通过标题自然语言特征识别视频实际内容类型。
        返回 { "content_type": str, "content_name": str, "confidence": float }
        content_type: "pv" | "guide" | "gameplay" | "streamer" | "opinion" | "unknown"
        """
        title = (v.get("title") or "").lower()
        desc = (v.get("desc") or "").lower()
        tag = (v.get("tag") or "").lower()
        text = title + " " + desc + " " + tag

        scores = {}
        details = {}

        for ctype, patterns in self.CONTENT_TYPE_PATTERNS.items():
            strong_hits = [kw for kw in patterns["strong"] if kw.lower() in title]
            weak_hits = [kw for kw in patterns["weak"] if kw.lower() in title]
            # desc/tag中的命中权重低一些
            desc_strong = [kw for kw in patterns["strong"] if kw.lower() in desc + " " + tag and kw.lower() not in title]

            score = len(strong_hits) * 3 + len(weak_hits) * 1 + len(desc_strong) * 0.5
            scores[ctype] = score
            details[ctype] = strong_hits + weak_hits

        # 额外检查：标题中出现游戏玩法指示词
        gameplay_indicator_hits = [
            w for w in self.GAMEPLAY_INDICATOR_WORDS if w.lower() in title
        ]
        if gameplay_indicator_hits:
            # 增加 guide/gameplay 的得分
            scores["guide"] = scores.get("guide", 0) + len(gameplay_indicator_hits) * 2
            scores["gameplay"] = scores.get("gameplay", 0) + len(gameplay_indicator_hits) * 1

        # PV/预告片的正面信号
        pv_strong_kws = [
            "pv", "宣传片", "预告片", "预告", "trailer", "teaser",
            "cg", "过场动画", "概念片",
            "reveal", "announce",
        ]
        pv_positive = sum(1 for kw in pv_strong_kws if kw in title)
        pv_score = pv_positive * 3

        # 找最高分的非PV类型
        best_type = None
        best_score = 0
        for ctype, score in scores.items():
            if score > best_score:
                best_score = score
                best_type = ctype

        # 判断逻辑：
        # 如果非PV类型得分 >= 3（至少一个强信号或多个弱信号），且 > PV得分，则认为不是PV
        if best_score >= 3 and best_score > pv_score:
            confidence = min(best_score / (best_score + pv_score + 1), 0.95)
            return {
                "content_type": best_type,
                "content_name": self.CONTENT_TYPE_PATTERNS[best_type]["name"],
                "confidence": round(confidence, 2),
                "matched_keywords": details.get(best_type, []) + gameplay_indicator_hits,
            }

        # 否则认为是PV或无法判断
        if pv_score >= 3:
            return {
                "content_type": "pv",
                "content_name": "PV/预告",
                "confidence": round(min(pv_score / (pv_score + best_score + 1), 0.95), 2),
                "matched_keywords": [kw for kw in pv_strong_kws if kw in title],
            }

        return {
            "content_type": "unknown",
            "content_name": "未分类",
            "confidence": 0,
            "matched_keywords": [],
        }

    def _classify_pv_video(self, v: dict) -> dict:
        """
        对PV视频进行分类和评分：
        返回 { "is_new_game": bool, "source_trust": "official"|"media"|"creator"|"unknown",
               "pv_score": int, "reject_reason": str|None }
        """
        title_raw = v.get("title") or ""
        title = title_raw.lower()
        author = v.get("author") or ""
        desc = (v.get("desc") or "").lower()
        tag = (v.get("tag") or "").lower()
        text = title + " " + desc + " " + tag

        result = {
            "is_new_game": True,
            "source_trust": "unknown",
            "pv_score": 0,
            "reject_reason": None,
        }

        # 1) 判断来源可信度
        if self._is_trusted_publisher(author):
            author_lower = author.lower()
            is_official = any(kw in author_lower for kw in [
                "官方", "official", "游戏", "game", "studio", "工作室",
            ]) or any(pub.lower() in author_lower for pub in [
                "米哈游", "miHoYo", "HoYoverse", "网易", "腾讯", "完美世界",
                "西山居", "鹰角", "叠纸", "莉莉丝", "散爆", "库洛", "深蓝互动", "烛龙",
                "Capcom", "卡普空", "SE", "Square Enix", "Bandai", "SEGA",
                "FromSoftware", "Bethesda", "Rockstar", "CD Projekt",
                "Valve", "Blizzard", "暴雪", "Riot", "拳头", "KONAMI", "ATLUS",
                "PlayStation", "Xbox", "Nintendo", "任天堂", "索尼", "微软",
                "哔哩哔哩游戏", "B站游戏",
                "EA", "育碧", "Ubisoft", "Epic",
            ])
            result["source_trust"] = "official" if is_official else "media"
            result["pv_score"] += 30 if is_official else 20
        else:
            result["source_trust"] = "creator"

        # ── 统计关键词命中 ──
        has_new_signal = any(
            kw.lower() in text for kw in self.NEW_GAME_SIGNAL_KEYWORDS
        )
        new_signal_count = sum(
            1 for kw in self.NEW_GAME_SIGNAL_KEYWORDS if kw.lower() in text
        )
        update_hits = [
            kw for kw in self.EXISTING_GAME_UPDATE_KEYWORDS if kw.lower() in text
        ]
        update_hit_count = len(update_hits)

        # 2) 检查是否为已有游戏名单中的运营更新
        matched_game = None
        for game_name in self.EXISTING_GAME_NAMES:
            if game_name.lower() in title:
                matched_game = game_name
                break

        # "强新游信号"：明确表示这是一款全新游戏，而非老游戏功能首曝
        # "首曝""全新""首发"等词在老游戏语境下可能指功能首曝，不能作为强信号
        STRONG_NEW_GAME_SIGNALS = [
            "新作", "新IP", "原创", "自研",
            "announce", "reveal", "announcement",
            "E3", "TGA", "TGS", "Gamescom", "科隆",
            "State of Play", "Nintendo Direct", "Xbox Showcase",
            "概念PV", "先导PV", "概念预告",
            "正式公开", "正式发布", "首次公开",
            "全球首曝", "世界首曝",
            "开发中", "研发中", "制作中",
        ]
        has_strong_new_signal = any(kw.lower() in text for kw in STRONG_NEW_GAME_SIGNALS)

        if matched_game:
            if update_hit_count > 0 and not has_strong_new_signal:
                # 已有游戏 + 运营更新词 + 没有强新游信号 -> 拒绝
                # 即使标题含"首曝""全新"等弱信号也拒绝（"全新宠物首曝"≠新游首曝）
                result["is_new_game"] = False
                result["reject_reason"] = f"已有游戏「{matched_game}」运营更新"
                result["pv_score"] -= 50
                return result
            if update_hit_count == 0 and not has_new_signal:
                # 已有游戏名在标题中但无更新词也无新游信号 -> 降分
                result["pv_score"] -= 10
            elif has_strong_new_signal:
                # 有强信号（如"新作"） -> 可能是IP新作，保留加分
                result["pv_score"] += 10
            else:
                # 已有游戏 + 无运营词 + 仅弱信号 -> 小幅降分
                result["pv_score"] -= 5

        # 3) 通用运营更新检测（即使游戏不在名单中）
        #    核心思路：如果标题含有运营更新词汇，且没有新游首曝信号，
        #    大概率是某个老游戏的版本更新而非全新游戏首曝
        if not matched_game and update_hit_count >= 1 and not has_new_signal:
            # 一个运营更新词 + 标题中不含任何新游信号 -> 很可能是老游戏
            # 额外检查：标题是否像 "[游戏名] + 运营更新" 的模式
            # 使用正则提取可能的游戏名
            title_has_specific_feature = any(kw.lower() in title for kw in [
                "宠物", "坐骑", "皮肤", "新英雄", "新角色", "新武器",
                "新装备", "新道具", "新时装", "新外观", "赛季",
                "卡池", "抽卡", "返场", "通行证", "战令",
                "活动", "联动", "签到", "礼包", "福利",
                "维护", "更新", "改版", "系统曝光",
                "至臻", "传说", "限定", "史诗",
            ])
            if title_has_specific_feature:
                result["is_new_game"] = False
                result["reject_reason"] = f"运营更新内容（含「{'、'.join(update_hits[:3])}」）"
                result["pv_score"] -= 40
                return result

        # 4) 标题同时含"全新"但又带运营特征词 -> 仍是运营更新
        #    例如 "全新宠物系统曝光" 中的"全新"不代表新游戏
        if update_hit_count >= 1:
            # "全新" + 运营词 = 老游戏的新功能，不是新游戏
            false_new_signals = ["全新系统", "全新宠物", "全新皮肤", "全新坐骑",
                                 "全新武器", "全新模式", "全新玩法", "全新赛季",
                                 "全新活动", "全新地图", "全新角色", "全新英雄",
                                 "全新装备", "全新道具", "全新时装", "全新外观"]
            has_false_new = any(sig.lower() in title for sig in false_new_signals)
            if has_false_new and not any(kw.lower() in text for kw in [
                "首曝", "首发", "新作", "公布", "正式公开", "正式发布",
                "首次公开", "全球首曝", "世界首曝", "新IP",
                "announce", "reveal", "E3", "TGA", "TGS", "Gamescom",
                "State of Play", "Nintendo Direct", "Xbox Showcase",
                "概念PV", "先导PV", "概念预告",
            ]):
                result["is_new_game"] = False
                result["reject_reason"] = "游戏功能更新（非新游首曝）"
                result["pv_score"] -= 35
                return result

        # 5) 检查标题是否包含多个运营更新关键词（兜底）
        if update_hit_count >= 2 and not has_new_signal:
            result["is_new_game"] = False
            result["reject_reason"] = f"标题含多个运营更新关键词（{'、'.join(update_hits[:3])}）"
            result["pv_score"] -= 30
            return result

        # 6) 全新游戏信号加分
        result["pv_score"] += new_signal_count * 10

        # 7) 标题包含"PV""宣传片""预告"加分
        pv_title_kws = ["pv", "宣传片", "预告", "trailer", "cg", "实机", "演示"]
        pv_hit = sum(1 for kw in pv_title_kws if kw in title)
        result["pv_score"] += pv_hit * 5

        # 8) 非权威创作者如果没有新游信号，降分
        if result["source_trust"] == "creator" and new_signal_count == 0:
            result["pv_score"] -= 15
            if pv_hit == 0:
                result["is_new_game"] = False
                result["reject_reason"] = "非权威来源且无新游信号"

        return result

    # ── 游戏信息知识库（工作室+类型） ──────────────────────────────────
    # 格式: { "关键词(小写)": {"studio": "工作室", "genre": "类型"} }
    GAME_INFO_DB = {
        # 米哈游
        "原神": {"studio": "米哈游 / HoYoverse", "genre": "开放世界ARPG"},
        "崩坏星穹铁道": {"studio": "米哈游 / HoYoverse", "genre": "回合制RPG"},
        "崩铁": {"studio": "米哈游 / HoYoverse", "genre": "回合制RPG"},
        "崩坏3": {"studio": "米哈游 / HoYoverse", "genre": "动作"},
        "绝区零": {"studio": "米哈游 / HoYoverse", "genre": "动作ARPG"},
        "mihoyo": {"studio": "米哈游 / HoYoverse", "genre": ""},
        "hoyoverse": {"studio": "米哈游 / HoYoverse", "genre": ""},
        # 库洛
        "鸣潮": {"studio": "库洛游戏", "genre": "开放世界ARPG"},
        # GTA
        "gta6": {"studio": "Rockstar Games", "genre": "开放世界动作"},
        "gta 6": {"studio": "Rockstar Games", "genre": "开放世界动作"},
        "gta5": {"studio": "Rockstar Games", "genre": "开放世界动作"},
        "荒野大镖客": {"studio": "Rockstar Games", "genre": "开放世界动作"},
        # 任天堂
        "任天堂": {"studio": "Nintendo", "genre": ""},
        "nintendo": {"studio": "Nintendo", "genre": ""},
        "switch2": {"studio": "Nintendo", "genre": ""},
        "switch 2": {"studio": "Nintendo", "genre": ""},
        "塞尔达": {"studio": "Nintendo EPD", "genre": "开放世界动作冒险"},
        "马里奥": {"studio": "Nintendo EPD", "genre": "平台跳跃"},
        "宝可梦": {"studio": "Game Freak / Nintendo", "genre": "RPG"},
        "pokemon": {"studio": "Game Freak / Nintendo", "genre": "RPG"},
        "喷射战士": {"studio": "Nintendo EPD", "genre": "射击"},
        "异度神剑": {"studio": "Monolith Soft", "genre": "JRPG"},
        "动物之森": {"studio": "Nintendo EPD", "genre": "模拟经营"},
        # FromSoftware
        "艾尔登法环": {"studio": "FromSoftware", "genre": "ARPG/魂类"},
        "只狼": {"studio": "FromSoftware", "genre": "动作冒险/魂类"},
        "黑暗之魂": {"studio": "FromSoftware", "genre": "ARPG/魂类"},
        "fromsoftware": {"studio": "FromSoftware", "genre": "ARPG/魂类"},
        # Capcom
        "怪物猎人": {"studio": "Capcom", "genre": "动作RPG"},
        "monster hunter": {"studio": "Capcom", "genre": "动作RPG"},
        "怪猎荒野": {"studio": "Capcom", "genre": "动作RPG"},
        "生化危机": {"studio": "Capcom", "genre": "生存恐怖"},
        "鬼泣": {"studio": "Capcom", "genre": "动作"},
        "capcom": {"studio": "Capcom", "genre": ""},
        # Square Enix
        "最终幻想": {"studio": "Square Enix", "genre": "JRPG"},
        "ff14": {"studio": "Square Enix", "genre": "MMORPG"},
        "ff16": {"studio": "Square Enix", "genre": "ARPG"},
        "ff7": {"studio": "Square Enix", "genre": "ARPG"},
        "square enix": {"studio": "Square Enix", "genre": ""},
        "勇者斗恶龙": {"studio": "Square Enix", "genre": "JRPG"},
        "尼尔": {"studio": "Square Enix / PlatinumGames", "genre": "ARPG"},
        # 其他日厂
        "persona": {"studio": "Atlus", "genre": "JRPG"},
        "女神异闻录": {"studio": "Atlus", "genre": "JRPG"},
        "真女神转生": {"studio": "Atlus", "genre": "JRPG"},
        "atlus": {"studio": "Atlus", "genre": "JRPG"},
        "falcom": {"studio": "Nihon Falcom", "genre": "JRPG"},
        "轨迹": {"studio": "Nihon Falcom", "genre": "JRPG"},
        "伊苏": {"studio": "Nihon Falcom", "genre": "ARPG"},
        "konami": {"studio": "KONAMI", "genre": ""},
        "合金装备": {"studio": "KONAMI", "genre": "潜行动作"},
        "寂静岭": {"studio": "KONAMI", "genre": "恐怖"},
        "sega": {"studio": "SEGA", "genre": ""},
        "世嘉": {"studio": "SEGA", "genre": ""},
        "如龙": {"studio": "SEGA / Ryu Ga Gotoku Studio", "genre": "ARPG"},
        "索尼克": {"studio": "SEGA / Sonic Team", "genre": "平台动作"},
        # Valve/FPS
        "valve": {"studio": "Valve", "genre": ""},
        "cs2": {"studio": "Valve", "genre": "FPS"},
        "dota2": {"studio": "Valve", "genre": "MOBA"},
        "半衰期": {"studio": "Valve", "genre": "FPS"},
        "half-life": {"studio": "Valve", "genre": "FPS"},
        # EA
        "ea sports": {"studio": "EA", "genre": "体育"},
        "ea fc": {"studio": "EA Sports", "genre": "体育"},
        "fifa": {"studio": "EA Sports", "genre": "体育"},
        "apex": {"studio": "EA / Respawn", "genre": "FPS大逃杀"},
        "titanfall": {"studio": "EA / Respawn", "genre": "FPS"},
        # 暴雪
        "暴雪": {"studio": "Blizzard", "genre": ""},
        "blizzard": {"studio": "Blizzard", "genre": ""},
        "暗黑破坏神": {"studio": "Blizzard", "genre": "ARPG"},
        "魔兽世界": {"studio": "Blizzard", "genre": "MMORPG"},
        "守望先锋": {"studio": "Blizzard", "genre": "FPS"},
        "overwatch": {"studio": "Blizzard", "genre": "FPS"},
        "炉石传说": {"studio": "Blizzard", "genre": "卡牌"},
        # Riot
        "英雄联盟": {"studio": "Riot Games", "genre": "MOBA"},
        "lol": {"studio": "Riot Games", "genre": "MOBA"},
        "无畏契约": {"studio": "Riot Games", "genre": "FPS"},
        "valorant": {"studio": "Riot Games", "genre": "FPS"},
        # 育碧
        "育碧": {"studio": "Ubisoft", "genre": ""},
        "ubisoft": {"studio": "Ubisoft", "genre": ""},
        "刺客信条": {"studio": "Ubisoft", "genre": "开放世界动作"},
        "彩虹六号": {"studio": "Ubisoft", "genre": "FPS"},
        # CD Projekt
        "赛博朋克": {"studio": "CD Projekt RED", "genre": "开放世界ARPG"},
        "巫师": {"studio": "CD Projekt RED", "genre": "ARPG"},
        "cd projekt": {"studio": "CD Projekt RED", "genre": "ARPG"},
        # 腾讯
        "王者荣耀": {"studio": "腾讯天美工作室", "genre": "MOBA"},
        "和平精英": {"studio": "腾讯光子工作室", "genre": "FPS大逃杀"},
        "穿越火线": {"studio": "腾讯 / Smilegate", "genre": "FPS"},
        "三角洲行动": {"studio": "腾讯天美工作室", "genre": "FPS"},
        "delta force": {"studio": "腾讯天美工作室", "genre": "FPS"},
        "dnf": {"studio": "腾讯 / Neople", "genre": "动作RPG"},
        "地下城与勇士": {"studio": "腾讯 / Neople", "genre": "动作RPG"},
        # 网易
        "永劫无间": {"studio": "网易 24 Entertainment", "genre": "动作大逃杀"},
        "蛋仔派对": {"studio": "网易", "genre": "派对"},
        "逆水寒": {"studio": "网易雷火工作室", "genre": "MMORPG"},
        "第五人格": {"studio": "网易", "genre": "非对称对抗"},
        "阴阳师": {"studio": "网易", "genre": "回合制RPG"},
        # 其他国产
        "黑神话": {"studio": "游戏科学", "genre": "ARPG/魂类"},
        "黑神话悟空": {"studio": "游戏科学", "genre": "ARPG/魂类"},
        "明日方舟": {"studio": "鹰角网络", "genre": "塔防策略"},
        "碧蓝航线": {"studio": "勇仕网络 / Yostar", "genre": "射击RPG"},
        "少女前线": {"studio": "散爆网络", "genre": "策略RPG"},
        "战双帕弥什": {"studio": "库洛游戏", "genre": "动作RPG"},
        "尘白禁区": {"studio": "库洛游戏", "genre": "TPS"},
        "幻塔": {"studio": "完美世界 Hotta Studio", "genre": "开放世界ARPG"},
        "剑网3": {"studio": "西山居", "genre": "MMORPG"},
        "光遇": {"studio": "thatgamecompany / 网易", "genre": "社交冒险"},
        # Bethesda
        "bethesda": {"studio": "Bethesda", "genre": ""},
        "上古卷轴": {"studio": "Bethesda Game Studios", "genre": "开放世界RPG"},
        "辐射": {"studio": "Bethesda Game Studios", "genre": "开放世界RPG"},
        "starfield": {"studio": "Bethesda Game Studios", "genre": "开放世界RPG"},
        "星空": {"studio": "Bethesda Game Studios", "genre": "开放世界RPG"},
        # Sony
        "playstation": {"studio": "Sony Interactive Entertainment", "genre": ""},
        "索尼": {"studio": "Sony Interactive Entertainment", "genre": ""},
        "战神": {"studio": "Santa Monica Studio", "genre": "ARPG"},
        "地平线": {"studio": "Guerrilla Games", "genre": "开放世界ARPG"},
        "漫威蜘蛛侠": {"studio": "Insomniac Games", "genre": "动作冒险"},
        "最后的生还者": {"studio": "Naughty Dog", "genre": "动作冒险"},
        "美国末日": {"studio": "Naughty Dog", "genre": "动作冒险"},
        # Xbox
        "xbox": {"studio": "Xbox Game Studios", "genre": ""},
        "微软": {"studio": "Xbox Game Studios", "genre": ""},
        "光环": {"studio": "343 Industries", "genre": "FPS"},
        "halo": {"studio": "343 Industries", "genre": "FPS"},
        "帝国时代": {"studio": "World's Edge / Relic", "genre": "RTS"},
        # 独立 / 知名
        "我的世界": {"studio": "Mojang Studios", "genre": "沙盒"},
        "minecraft": {"studio": "Mojang Studios", "genre": "沙盒"},
        "堡垒之夜": {"studio": "Epic Games", "genre": "大逃杀/射击"},
        "fortnite": {"studio": "Epic Games", "genre": "大逃杀/射击"},
        "空洞骑士": {"studio": "Team Cherry", "genre": "银河恶魔城"},
        "hollow knight": {"studio": "Team Cherry", "genre": "银河恶魔城"},
        "哈迪斯": {"studio": "Supergiant Games", "genre": "Roguelike"},
        "hades": {"studio": "Supergiant Games", "genre": "Roguelike"},
        "博德之门": {"studio": "Larian Studios", "genre": "CRPG"},
        "baldur's gate": {"studio": "Larian Studios", "genre": "CRPG"},
        "星露谷": {"studio": "ConcernedApe", "genre": "模拟经营"},
        "stardew valley": {"studio": "ConcernedApe", "genre": "模拟经营"},
        "死亡搁浅": {"studio": "小岛秀夫工作室", "genre": "动作冒险"},
        "death stranding": {"studio": "Kojima Productions", "genre": "动作冒险"},
        "小岛秀夫": {"studio": "Kojima Productions", "genre": ""},
        # PUBG
        "绝地求生": {"studio": "Krafton / PUBG Corp", "genre": "FPS大逃杀"},
        "pubg": {"studio": "Krafton / PUBG Corp", "genre": "FPS大逃杀"},
        # Bandai Namco
        "万代南梦宫": {"studio": "Bandai Namco", "genre": ""},
        "bandai namco": {"studio": "Bandai Namco", "genre": ""},
        "噬神者": {"studio": "Bandai Namco", "genre": "动作RPG"},
        "铁拳": {"studio": "Bandai Namco", "genre": "格斗"},
        "高达": {"studio": "Bandai Namco", "genre": "动作"},
    }

    # 游戏类型关键词推断（从标题中猜测类型）
    GENRE_HINTS = [
        # (关键词列表, 类型)
        (["fps", "射击", "枪战", "狙击", "突击", "战术射击"], "射击/FPS"),
        (["moba", "moba"], "MOBA"),
        (["大逃杀", "吃鸡", "battle royale"], "大逃杀"),
        (["开放世界", "open world"], "开放世界"),
        (["arpg", "动作角色扮演", "动作rpg"], "ARPG"),
        (["rpg", "角色扮演"], "RPG"),
        (["mmorpg", "网游", "mmo"], "MMORPG"),
        (["回合制", "策略", "战棋", "slg"], "策略/SLG"),
        (["格斗", "fighting", "对战"], "格斗"),
        (["恐怖", "horror", "生存恐怖"], "恐怖"),
        (["模拟", "经营", "sim", "种田", "养成"], "模拟经营"),
        (["平台", "横版", "银河城", "metroidvania"], "平台动作"),
        (["roguelike", "roguelite", "肉鸽"], "Roguelike"),
        (["赛车", "竞速", "racing"], "竞速"),
        (["卡牌", "card", "tcg", "ccg"], "卡牌"),
        (["塔防", "tower defense"], "塔防"),
        (["沙盒", "sandbox", "生存"], "沙盒/生存"),
        (["冒险", "adventure", "解谜"], "冒险"),
        (["动作", "action", "act"], "动作"),
        (["音游", "节奏", "rhythm"], "音游"),
        (["体育", "足球", "篮球", "sports"], "体育"),
        (["派对", "party"], "派对"),
    ]

    def _enrich_game_info(self, v: dict) -> dict:
        """从标题和UP主信息中提取游戏工作室和类型"""
        title = (v.get("title") or "").lower()
        author = (v.get("author") or "").lower()
        desc = (v.get("desc") or "").lower()
        tag = (v.get("tag") or "").lower()
        text = title + " " + desc + " " + tag + " " + author

        studio = ""
        genre = ""

        # 1) 从知识库匹配
        best_match_len = 0
        for key, info in self.GAME_INFO_DB.items():
            if key.lower() in text:
                # 优先匹配更长的关键词（更精确）
                if len(key) > best_match_len:
                    best_match_len = len(key)
                    if info.get("studio"):
                        studio = info["studio"]
                    if info.get("genre"):
                        genre = info["genre"]

        # 2) 从UP主名推断工作室
        if not studio:
            for pub in self.TRUSTED_PUBLISHERS:
                if pub.lower() in author:
                    studio = pub
                    break
            # 如果UP主名含"官方"且不在知识库 → 用UP主名作为工作室
            if not studio and ("官方" in v.get("author", "") or "official" in author):
                studio = v.get("author", "")

        # 3) 如果没有匹配到类型，从标题关键词推断
        if not genre:
            for hints, g in self.GENRE_HINTS:
                if any(h in text for h in hints):
                    genre = g
                    break

        return {"studio": studio, "genre": genre}

    def _serve_pv_timeline(self, query: dict):
        """新游PV时间轴 - 聚焦全新游戏首曝/首发PV，过滤已有游戏运营更新"""
        # 搜索关键词聚焦"全新游戏"
        pv_keywords = [
            "全新游戏 PV", "新游 首曝", "新游 PV", "新作 预告",
            "游戏 首曝", "游戏 新作公布", "游戏 全球首曝",
            "游戏 概念PV", "游戏 先导预告", "新游 宣传片",
            "游戏 announce trailer", "游戏 reveal trailer",
            "TGA 新游", "新游 实机演示",
        ]
        # 允许前端追加自定义关键词
        extra = query.get("extra_keywords", [None])[0]
        if extra:
            pv_keywords.extend([kw.strip() for kw in extra.split(",") if kw.strip()])

        hours = int(query.get("hours", [24])[0])
        limit_per_kw = int(query.get("limit", [20])[0])
        # 是否包含非权威来源（默认不包含，只展示官方/媒体）
        include_creators = query.get("include_creators", ["0"])[0] == "1"

        logger.info(f"PV时间轴: 搜索 {len(pv_keywords)} 个关键词, 近{hours}h, 含创作者={include_creators}")

        all_results = []
        rejected = []
        seen_bvids = set()
        now_ts = time.time()
        cutoff_ts = now_ts - hours * 3600

        for kw in pv_keywords:
            try:
                videos = fetch_search(kw, limit=min(limit_per_kw, 30), order="pubdate")
                for v in videos:
                    bvid = v.get("bvid", "")
                    if bvid in seen_bvids:
                        continue
                    seen_bvids.add(bvid)
                    pub_ts = v.get("pubdate_ts", 0)
                    if not (pub_ts and pub_ts >= cutoff_ts):
                        continue

                    # 分类评分
                    classification = self._classify_pv_video(v)
                    v["pv_keyword"] = kw
                    v["source_trust"] = classification["source_trust"]
                    v["pv_score"] = classification["pv_score"]
                    v["is_new_game"] = classification["is_new_game"]

                    age_hours = max((now_ts - pub_ts) / 3600, 1)
                    v["freshness_score"] = round(v.get("play", 0) / age_hours)

                    if not classification["is_new_game"]:
                        v["reject_reason"] = classification["reject_reason"]
                        rejected.append(v)
                        continue

                    # ── 二次分类矫正：识别实际内容类型 ──
                    content_info = self._detect_content_type(v)
                    v["content_type"] = content_info["content_type"]
                    v["content_type_name"] = content_info["content_name"]
                    v["content_confidence"] = content_info["confidence"]

                    # 如果高置信度识别为非PV内容（攻略/教学/直播/讨论），过滤掉
                    if content_info["content_type"] not in ("pv", "unknown") and content_info["confidence"] >= 0.4:
                        v["reject_reason"] = (
                            f"内容类型为「{content_info['content_name']}」"
                            f"（匹配: {'、'.join(content_info['matched_keywords'][:4])}）"
                        )
                        rejected.append(v)
                        continue

                    # 非权威创作者根据开关决定是否包含
                    if classification["source_trust"] == "creator" and not include_creators:
                        # 但如果pv_score很高（有明确新游信号），还是保留
                        if classification["pv_score"] < 20:
                            v["reject_reason"] = "非权威来源（可在设置中开启）"
                            rejected.append(v)
                            continue

                    all_results.append(v)
                time.sleep(0.8)
            except Exception as e:
                logger.warning(f"PV搜索 [{kw}] 失败: {e}")

        # 为每个结果补充游戏工作室和类型信息
        for v in all_results:
            game_info = self._enrich_game_info(v)
            v["studio"] = game_info.get("studio", "")
            v["game_genre"] = game_info.get("genre", "")

        # 按 pv_score 降序，再按发布时间倒序
        all_results.sort(key=lambda x: (x.get("pv_score", 0), x.get("pubdate_ts", 0)), reverse=True)

        # 统计过滤原因分类
        content_type_rejected = sum(1 for v in rejected if "内容类型" in (v.get("reject_reason") or ""))
        game_update_rejected = len(rejected) - content_type_rejected

        self._json_response({
            "videos": all_results,
            "total": len(all_results),
            "rejected_count": len(rejected),
            "rejected_sample": rejected[:20],
            "rejected_by_content_type": content_type_rejected,
            "rejected_by_game_update": game_update_rejected,
            "keywords_used": pv_keywords,
            "hours": hours,
            "include_creators": include_creators,
            "search_time": datetime.now().isoformat(),
        })

    def _serve_history(self):
        history_dir = DATA_DIR / "history"
        if not history_dir.exists():
            self._json_response([])
            return
        files = sorted(history_dir.glob("snapshot_*.json"), reverse=True)
        result = []
        for f in files[:48]:  # 最近48个快照
            try:
                ts = f.stem.replace("snapshot_", "")
                dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
                result.append({
                    "filename": f.name,
                    "time": dt.isoformat(),
                    "size": f.stat().st_size,
                })
            except Exception:
                pass
        self._json_response(result)

    def _do_collect(self):
        try:
            cfg = load_config()
            run_collect(cfg)
        except Exception as e:
            logger.error(f"手动采集出错: {e}")

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))

    def _serve_file(self, path, content_type):
        if not path.exists():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.end_headers()
        with open(path, "rb") as f:
            self.wfile.write(f.read())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        # 只记录 API 请求
        if "/api/" in (args[0] if args else ""):
            logger.debug(f"{self.address_string()} - {format % args}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="B站游戏热点监控看板")
    parser.add_argument("--port", type=int, default=8765, help="服务端口")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--no-auto-collect", action="store_true", help="不自动启动定时采集")
    parser.add_argument("--collect-now", action="store_true", help="启动时立即采集一次")
    args = parser.parse_args()

    # 初始化配置
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        logger.info("已创建默认配置")

    # 立即采集
    if args.collect_now:
        logger.info("启动时执行一次采集...")
        run_collect()

    # 启动定时采集
    if not args.no_auto_collect:
        cfg = load_config()
        interval = cfg.get("interval_minutes", 60)
        scheduler.start(interval)
        logger.info(f"定时采集已启动，间隔 {interval} 分钟")

    # 启动 HTTP 服务
    server = HTTPServer((args.host, args.port), DashboardHandler)
    logger.info(f"看板服务启动: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("服务停止")
        scheduler.stop()
        server.server_close()


if __name__ == "__main__":
    main()
