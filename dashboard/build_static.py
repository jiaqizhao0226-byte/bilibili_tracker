#!/usr/bin/env python3
"""
构建纯静态版本的看板 HTML，将所有数据内嵌，无需后端服务器。
生成 dist/index.html，可直接部署到 EdgeOne Pages 等静态托管。
"""
import json
import time
from pathlib import Path
from datetime import datetime

DASHBOARD_DIR = Path(__file__).parent
DATA_DIR = DASHBOARD_DIR / "data"
DIST_DIR = DASHBOARD_DIR / "dist"


def load_all_snapshots():
    """加载所有历史快照 + latest.json"""
    files = []
    latest = DATA_DIR / "latest.json"
    if latest.exists():
        files.append(latest)
    history_dir = DATA_DIR / "history"
    if history_dir.exists():
        files.extend(sorted(history_dir.glob("snapshot_*.json"), reverse=True))

    all_data = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                all_data.append(json.load(fh))
        except Exception:
            continue
    return all_data


def build_overview_data(snapshots):
    """构建近7天总览数据"""
    now_ts = time.time()
    cutoff_ts = now_ts - 7 * 86400

    all_videos = []
    seen_bvids = set()
    hot_search = []
    latest_meta = {}
    collect_times = []

    for data in snapshots:
        ct = data.get("meta", {}).get("collect_time", "")
        if ct:
            collect_times.append(ct)
        if not latest_meta and data.get("meta"):
            latest_meta = data["meta"]
        if not hot_search and data.get("hot_search"):
            hot_search = data["hot_search"]

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

    filtered = []
    for v in all_videos:
        pub_ts = v.get("pubdate_ts", 0)
        if pub_ts and pub_ts > 0 and pub_ts < cutoff_ts:
            continue
        age_hours = max((now_ts - pub_ts) / 3600, 1) if pub_ts else 9999
        v["freshness_score"] = round(v.get("play", 0) / age_hours)
        filtered.append(v)

    filtered.sort(key=lambda x: x.get("freshness_score", 0), reverse=True)

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

    return {
        "videos": filtered,
        "total": len(filtered),
        "hot_count": hot_count,
        "total_play": total_play,
        "hot_search": hot_search,
        "meta": latest_meta,
        "day_stats": day_stats,
        "collect_count": len(collect_times),
        "days": 7,
    }


def build_trending_data(snapshots):
    """构建爆款发现数据（近24h）"""
    now_ts = time.time()
    cutoff_ts = now_ts - 24 * 3600

    all_videos = []
    seen_bvids = set()

    for data in snapshots:
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

    filtered = []
    for v in all_videos:
        pub_ts = v.get("pubdate_ts", 0)
        if pub_ts and pub_ts > 0 and pub_ts < cutoff_ts:
            continue
        age_hours = max((now_ts - pub_ts) / 3600, 1) if pub_ts else 9999
        v["freshness_score"] = round(v.get("play", 0) / age_hours)
        filtered.append(v)

    filtered.sort(key=lambda x: x.get("freshness_score", 0), reverse=True)

    return {
        "videos": filtered,
        "total": len(filtered),
        "meta": snapshots[0].get("meta", {}) if snapshots else {},
    }


def build_pv_timeline_data(hours=48, limit_per_kw=20, include_creators=False):
    """构建静态模式下的新游PV时间轴数据。"""
    try:
        from server import DashboardHandler, fetch_search
    except Exception as e:
        print(f"⚠️ PV追踪静态数据构建跳过: {e}")
        return {
            "videos": [],
            "total": 0,
            "rejected_count": 0,
            "rejected_sample": [],
            "rejected_by_content_type": 0,
            "rejected_by_game_update": 0,
            "keywords_used": [],
            "hours": hours,
            "include_creators": include_creators,
            "search_time": datetime.now().isoformat(),
            "error": str(e),
        }

    pv_keywords = [
        "全新游戏 PV", "新游 首曝", "新游 PV", "新作 预告",
        "游戏 首曝", "游戏 新作公布", "游戏 全球首曝",
        "游戏 概念PV", "游戏 先导预告", "新游 宣传片",
        "游戏 announce trailer", "游戏 reveal trailer",
        "TGA 新游", "新游 实机演示",
    ]

    print(f"  PV追踪: 搜索 {len(pv_keywords)} 个关键词, 近{hours}h")

    handler = object.__new__(DashboardHandler)
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
                if not bvid or bvid in seen_bvids:
                    continue
                seen_bvids.add(bvid)

                pub_ts = v.get("pubdate_ts", 0)
                if not (pub_ts and pub_ts >= cutoff_ts):
                    continue

                classification = handler._classify_pv_video(v)
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

                content_info = handler._detect_content_type(v)
                v["content_type"] = content_info["content_type"]
                v["content_type_name"] = content_info["content_name"]
                v["content_confidence"] = content_info["confidence"]

                if content_info["content_type"] not in ("pv", "unknown") and content_info["confidence"] >= 0.4:
                    v["reject_reason"] = (
                        f"内容类型为「{content_info['content_name']}」"
                        f"（匹配: {'、'.join(content_info['matched_keywords'][:4])}）"
                    )
                    rejected.append(v)
                    continue

                if classification["source_trust"] == "creator" and not include_creators:
                    if classification["pv_score"] < 20:
                        v["reject_reason"] = "非权威来源（静态快照默认不展示）"
                        rejected.append(v)
                        continue

                all_results.append(v)
            time.sleep(0.8)
        except Exception as e:
            print(f"  ⚠️ PV搜索 [{kw}] 失败: {e}")

    for v in all_results:
        game_info = handler._enrich_game_info(v)
        v["studio"] = game_info.get("studio", "")
        v["game_genre"] = game_info.get("genre", "")

    all_results.sort(key=lambda x: (x.get("pv_score", 0), x.get("pubdate_ts", 0)), reverse=True)

    content_type_rejected = sum(1 for v in rejected if "内容类型" in (v.get("reject_reason") or ""))
    game_update_rejected = len(rejected) - content_type_rejected

    return {
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
    }


def build_static():
    print("📦 构建纯静态看板...")

    # 读取原始 HTML
    html_path = DASHBOARD_DIR / "index.html"
    html = html_path.read_text(encoding="utf-8")

    # 加载数据
    snapshots = load_all_snapshots()
    if not snapshots:
        print("❌ 没有找到数据文件，请先运行采集")
        return

    overview_data = build_overview_data(snapshots)
    trending_data = build_trending_data(snapshots)
    pv_timeline_data = build_pv_timeline_data(hours=48, include_creators=False)

    # 最新一次的原始数据（用于 trending 的 fetchData）
    latest_raw = snapshots[0] if snapshots else {}

    print(f"  总览: {overview_data['total']} 个视频, {overview_data['hot_count']} 个爆款")
    print(f"  爆款: {trending_data['total']} 个视频")
    print(f"  PV追踪: {pv_timeline_data['total']} 个候选PV, 过滤 {pv_timeline_data['rejected_count']} 条")

    # 构建内嵌数据脚本
    embed_script = f"""
<script>
// ── 静态模式：数据已内嵌，无需后端 ──
window.__STATIC_MODE__ = true;
window.__OVERVIEW_DATA__ = {json.dumps(overview_data, ensure_ascii=False)};
window.__TRENDING_DATA__ = {json.dumps(trending_data, ensure_ascii=False)};
window.__PV_TIMELINE_DATA__ = {json.dumps(pv_timeline_data, ensure_ascii=False)};
window.__LATEST_RAW__ = {json.dumps(latest_raw, ensure_ascii=False)};
window.__BUILD_TIME__ = "{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}";
</script>
"""

    # 在 </head> 前插入数据
    html = html.replace("</head>", embed_script + "</head>")

    # 替换 API 函数为静态版本
    static_overrides = """
<script>
// ── 静态模式覆盖 ──
if (window.__STATIC_MODE__) {
  // 覆盖 fetchData
  window._origFetchData = window.fetchData;
  window.fetchData = async function(params) {
    const raw = window.__LATEST_RAW__;
    const hours = parseInt(params?.hours) || 24;
    const minPlay = parseInt(params?.min_play) || 0;
    const nowTs = Date.now() / 1000;
    const cutoff = nowTs - hours * 3600;

    let allVids = [];
    const seen = new Set();
    for (const key of ['popular', 'ranking']) {
      for (const v of (raw[key] || [])) {
        if (v.bvid && !seen.has(v.bvid)) { seen.add(v.bvid); allVids.push(v); }
      }
    }
    for (const vs of Object.values(raw.regions || {})) {
      for (const v of vs) {
        if (v.bvid && !seen.has(v.bvid)) { seen.add(v.bvid); allVids.push(v); }
      }
    }
    for (const vs of Object.values(raw.search || {})) {
      for (const v of vs) {
        if (v.bvid && !seen.has(v.bvid)) { seen.add(v.bvid); allVids.push(v); }
      }
    }

    let filtered = allVids.filter(v => {
      const pt = v.pubdate_ts || 0;
      if (pt && pt < cutoff) return false;
      if (minPlay && (v.play || 0) < minPlay) return false;
      return true;
    });

    filtered.forEach(v => {
      const age = Math.max((nowTs - (v.pubdate_ts || 0)) / 3600, 1);
      v.freshness_score = Math.round((v.play || 0) / age);
    });
    filtered.sort((a, b) => (b.freshness_score || 0) - (a.freshness_score || 0));

    const kw = params?.keyword?.toLowerCase();
    if (kw) {
      filtered = filtered.filter(v =>
        (v.title || '').toLowerCase().includes(kw) ||
        (v.author || '').toLowerCase().includes(kw) ||
        (v.tag || '').toLowerCase().includes(kw)
      );
    }

    return { videos: filtered, total: filtered.length, meta: raw.meta || {}, hot_search: raw.hot_search || [] };
  };

  // 覆盖 fetchConfig / fetchStatus
  window.fetchConfig = async () => ({});
  window.fetchStatus = async () => ({ running: false, interval: 0, last_collect: '', next_collect: '' });

  // 静态模式下使用构建时预计算的新游PV数据，不再依赖后端接口
  window.loadPVTimeline = async function() {
    const hours = window.__PV_TIMELINE_DATA__?.hours || 48;
    const container = document.getElementById('pvTimeline');
    if (!container) return;
    const data = window.__PV_TIMELINE_DATA__ || { videos: [], total: 0, keywords_used: [], hours };
    pvTimelineData = data;

    if (!data.videos || !data.videos.length) {
      container.innerHTML = `
        <div class="empty-state" style="padding:40px">
          <div class="icon">🎬</div>
          <div class="title">本次静态快照暂无全新游戏PV</div>
          <p>已在构建时完成PV搜索；可稍后重新采集并部署刷新结果</p>
        </div>`;
      return;
    }

    renderPVTimelineView(data.videos, container, hours);
  };

  // 隐藏采集按钮，显示静态模式提示
  document.addEventListener('DOMContentLoaded', () => {
    const hdr = document.querySelector('.header');
    if (hdr) {
      const btns = hdr.querySelectorAll('button');
      btns.forEach(b => { if (b.textContent.includes('采集') || b.textContent.includes('设置')) b.style.display = 'none'; });
      const badge = document.createElement('span');
      badge.style.cssText = 'font-size:11px;padding:3px 10px;border-radius:10px;background:#a855f722;color:#a855f7;margin-left:8px';
      badge.textContent = '📷 静态快照 · ' + window.__BUILD_TIME__;
      const title = hdr.querySelector('h1') || hdr.querySelector('.logo');
      if (title) title.after(badge);
    }
  });
}
</script>
"""

    # 在 </body> 前插入静态覆盖脚本
    html = html.replace("</body>", static_overrides + "</body>")

    # 写入 dist
    DIST_DIR.mkdir(exist_ok=True)
    out_path = DIST_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")

    size_kb = out_path.stat().st_size / 1024
    print(f"✅ 构建完成: {out_path} ({size_kb:.0f} KB)")
    print(f"   可直接用浏览器打开，或部署到 EdgeOne Pages")


if __name__ == "__main__":
    build_static()
