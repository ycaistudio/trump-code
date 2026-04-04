#!/usr/bin/env python3
"""
Trump RSS 高頻監控器
每 30 秒掃 truthsocial.org RSS，偵測到新推文立刻觸發信號分析。

用法：
  python3 trump_rss_watcher.py          # 持續監控
  python3 trump_rss_watcher.py --once   # 跑一次就停
  python3 trump_rss_watcher.py --test   # 測試模式（用最新一篇模擬）
"""

import json
import sys
import time
import threading
import xml.etree.ElementTree as ET
import urllib.request
import re
from datetime import datetime, timezone
from pathlib import Path

# washin_llm 共用 LLM 模組
sys.path.insert(0, str(Path.home() / "Projects" / "washin-llm"))

# === 設定 ===
RSS_URL = "https://www.trumpstruth.org/feed"
POLL_INTERVAL = 30  # 秒
X_POLL_MULTIPLIER = 10  # 每 10 輪 RSS 掃一次 X（= 5 分鐘，省 API credit）
TRUMP_X_USER_ID = "25073877"  # @realDonaldTrump
SEEN_FILE = Path(__file__).parent / "data" / "rss_seen_ids.json"
X_SEEN_FILE = Path(__file__).parent / "data" / "x_seen_ids.json"
LOG_FILE = Path(__file__).parent / "data" / "rss_watcher.log"
LATENCY_LOG = Path(__file__).parent / "data" / "rss_latency_log.json"

# === 工具 ===

def log(msg):
    """寫 log + 印出。"""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fetch_rss() -> list[dict]:
    """抓 RSS，回傳推文列表（最新在前）。"""
    t0 = time.time()
    req = urllib.request.Request(RSS_URL, headers={
        "User-Agent": "TrumpCode-RSSWatcher/1.0",
        "Accept": "application/rss+xml",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
    fetch_ms = (time.time() - t0) * 1000

    root = ET.fromstring(data)
    items = []

    for item in root.findall(".//item"):
        # 標題（推文內容）
        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        # 內文（HTML 版）
        desc_el = item.find("description")
        desc_html = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        # 去 HTML tag
        content = re.sub(r'<[^>]+>', '', desc_html).strip()

        # 用 title 或 content（有些推文 title 是 [No Title]）
        text = title if title and "[No Title]" not in title else content

        # 時間
        pub_el = item.find("pubDate")
        pub_str = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

        # guid（去重用）
        guid_el = item.find("guid")
        guid = guid_el.text.strip() if guid_el is not None and guid_el.text else ""

        # originalId（Truth Social 原始 ID）
        ns = {"truth": "https://truthsocial.com/ns"}
        orig_id_el = item.find("truth:originalId", ns)
        orig_id = orig_id_el.text.strip() if orig_id_el is not None and orig_id_el.text else ""

        # originalUrl
        orig_url_el = item.find("truth:originalUrl", ns)
        orig_url = orig_url_el.text.strip() if orig_url_el is not None and orig_url_el.text else ""

        items.append({
            "id": orig_id or guid,
            "guid": guid,
            "content": text,
            "pub_date": pub_str,
            "original_url": orig_url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    return items, fetch_ms


def fetch_x_timeline() -> tuple[list[dict], float]:
    """抓川普 X 時間線，回傳推文列表。格式與 fetch_rss 一致。"""
    import os as _os
    from pathlib import Path as _Path

    # 讀 Bearer token
    env_path = _Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _os.environ.setdefault(k.strip(), v.strip())

    bearer = _os.environ.get("X_BEARER_TOKEN_TRUMPCODE", "")
    if not bearer:
        return [], 0

    t0 = time.time()
    url = (
        f"https://api.x.com/2/users/{TRUMP_X_USER_ID}/tweets"
        f"?max_results=10&tweet.fields=created_at,text,public_metrics"
        f"&exclude=retweets,replies"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log(f"  ⚠️ X timeline 抓取失敗: {e}")
        return [], (time.time() - t0) * 1000

    fetch_ms = (time.time() - t0) * 1000
    items = []
    for tweet in data.get("data", []):
        text = tweet.get("text", "")
        # X 推文常常只有一個 URL（指向 Truth Social），跳過這種
        if text.startswith("https://t.co/") and len(text) < 30:
            continue
        items.append({
            "id": f"x_{tweet['id']}",  # 加 x_ 前綴避免跟 TS ID 撞
            "guid": tweet["id"],
            "content": text,
            "pub_date": tweet.get("created_at", ""),
            "original_url": f"https://x.com/realDonaldTrump/status/{tweet['id']}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "x",  # 標記來源
        })

    return items, fetch_ms


def load_seen() -> set:
    """載入已看過的 ID（Truth Social）。"""
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def load_x_seen() -> set:
    """載入已看過的 X 推文 ID。"""
    if X_SEEN_FILE.exists():
        try:
            with open(X_SEEN_FILE) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen(seen: set):
    """存已看過的 ID（只保留最近 500 個）。"""
    recent = sorted(seen)[-500:]
    with open(SEEN_FILE, "w") as f:
        json.dump(recent, f)


def save_x_seen(seen: set):
    """存已看過的 X 推文 ID。"""
    recent = sorted(seen)[-200:]
    with open(X_SEEN_FILE, "w") as f:
        json.dump(recent, f)


def record_latency(post: dict, detect_time: float):
    """記錄偵測延遲。"""
    try:
        # 解析 RSS pubDate（格式：Thu, 19 Mar 2026 02:05:29 +0000）
        from email.utils import parsedate_to_datetime
        pub_dt = parsedate_to_datetime(post["pub_date"])
        detect_dt = datetime.fromtimestamp(detect_time, tz=timezone.utc)
        latency_sec = (detect_dt - pub_dt).total_seconds()

        entry = {
            "post_id": post["id"],
            "pub_time": pub_dt.isoformat(),
            "detect_time": detect_dt.isoformat(),
            "latency_sec": round(latency_sec, 1),
            "content_preview": post["content"][:80],
        }

        # 讀現有 log
        entries = []
        if LATENCY_LOG.exists():
            try:
                with open(LATENCY_LOG) as f:
                    entries = json.load(f)
            except Exception:
                pass
        entries.append(entry)
        entries = entries[-200:]  # 保留最近 200 筆

        with open(LATENCY_LOG, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

        return latency_sec
    except Exception as e:
        log(f"  ⚠️ 記錄延遲失敗: {e}")
        return None


def on_new_post(post: dict):
    """
    偵測到新推文時的處理。
    全鏈路計時：偵測 → 信號分類 → 模型預測 → (未來) Polymarket 下單
    """
    detect_time = time.time()
    latency = record_latency(post, detect_time)

    log(f"  🆕 新推文偵測！")
    log(f"     ID: {post['id']}")
    log(f"     時間: {post['pub_date']}")
    log(f"     偵測延遲: {latency:.1f}秒" if latency else "     偵測延遲: 無法計算")
    log(f"     內容: {post['content'][:100]}...")

    # === 步驟 3: 信號分類 + 模型預測 ===
    t_classify_start = time.time()
    try:
        from realtime_loop import classify_post
        signals = classify_post(post['content'])
    except Exception as e:
        log(f"     ⚠️ classify_post 失敗: {e}")
        signals = []

    t_classify_end = time.time()
    classify_ms = (t_classify_end - t_classify_start) * 1000

    if signals:
        sig_str = ', '.join(f"{s['type']}({s['confidence']:.0%})" for s in signals)
        log(f"     📡 信號: {sig_str} ({classify_ms:.0f}ms)")

        # 預測方向（輕量版：直接從信號判斷，不需 PM/stock snapshot）
        t_predict_start = time.time()
        try:
            bullish = [s for s in signals if s['type'] in ('DEAL', 'RELIEF', 'BULLISH', 'ACTION')]
            bearish = [s for s in signals if s['type'] in ('TARIFF', 'THREAT', 'BEARISH')]
            if len(bullish) > len(bearish):
                direction = 'UP'
                confidence = max(s['confidence'] for s in bullish)
            elif len(bearish) > len(bullish):
                direction = 'DOWN'
                confidence = max(s['confidence'] for s in bearish)
            else:
                direction = 'NEUTRAL'
                confidence = 0.3
            triggered = len(bullish) + len(bearish)
            t_predict_end = time.time()
            predict_ms = (t_predict_end - t_predict_start) * 1000
            log(f"     🎯 預測: {direction} ({confidence:.0%}) | {len(bullish)}多/{len(bearish)}空 ({predict_ms:.0f}ms)")
        except Exception as e:
            log(f"     ⚠️ 預測失敗: {e}")
            direction, confidence, triggered = None, 0, 0
            predict_ms = 0

        # 全鏈路計時
        total_ms = (time.time() - detect_time) * 1000
        log(f"     ⏱️ 全鏈路: 偵測{latency:.0f}s + 分類{classify_ms:.0f}ms + 預測{predict_ms:.0f}ms = 共{latency + total_ms/1000:.1f}s")

        # === 步驟 4: Polymarket 下單（等 tkman 錢包就緒）===
        # TODO: polymarket_trade(direction, confidence, signals)
        log(f"     💰 Polymarket: 等待錢包設定...")

        # 記錄完整鏈路到 latency log
        _append_pipeline_log(post, latency, classify_ms, predict_ms,
                             signals, direction, confidence, triggered)

        # === 步驟 5: 即時三語快報生成（背景執行，不阻塞監控）===
        _trigger_flash_article(post, signals, direction, confidence)
    else:
        # 無信號也寫 — 每篇都寫，累積一年就有信心了
        log(f"     ⚪ 無市場信號，仍生成快報 ({classify_ms:.0f}ms)")
        _trigger_flash_article(post, [], 'NEUTRAL', 0.0)


def _append_pipeline_log(post, detect_latency, classify_ms, predict_ms,
                         signals, direction, confidence, triggered):
    """記錄完整的流水線計時到 JSON。"""
    try:
        pipeline_log_file = Path(__file__).parent / "data" / "rss_pipeline_log.json"
        entries = []
        if pipeline_log_file.exists():
            try:
                with open(pipeline_log_file) as f:
                    entries = json.load(f)
            except Exception:
                pass

        entries.append({
            "post_id": post["id"],
            "pub_time": post["pub_date"],
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "detect_latency_sec": round(detect_latency, 1) if detect_latency else None,
            "classify_ms": round(classify_ms, 1),
            "predict_ms": round(predict_ms, 1),
            "total_sec": round((detect_latency or 0) + (classify_ms + predict_ms) / 1000, 1),
            "signals": [s['type'] for s in signals],
            "direction": direction,
            "confidence": round(confidence, 3) if confidence else None,
            "rules_triggered": triggered,
            "polymarket_order": None,  # 等錢包就緒才填
            "content_preview": post["content"][:80],
        })
        entries = entries[-200:]

        with open(pipeline_log_file, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"  ⚠️ pipeline log 寫入失敗: {e}")


# git 操作鎖 — 防止多個快報 thread 同時 commit 撞 index.lock
_git_lock = threading.Lock()

# X 發推失敗計數（跨 thread 共享）
_x_fail_count = 0
_x_fail_lock = threading.Lock()


def _trigger_flash_article(post: dict, signals: list, direction: str, confidence: float):
    """背景執行即時三語快報生成（不阻塞 RSS 監控迴圈）。"""
    def _run():
        try:
            log(f"     📝 即時快報生成中...")
            from article_generator import generate_flash
            meta = generate_flash(post, signals, direction, confidence)
            ok = sum(1 for v in meta.get('articles', {}).values() if v.get('status') == 'ok')
            log(f"     📝 即時快報完成：{ok}/3 語言成功")

            # 發 X 三語 Thread
            if ok > 0:
                global _x_fail_count
                try:
                    from x_poster import post_flash_thread
                    x_result = post_flash_thread(meta)
                    if x_result.get('ok'):
                        tweets = x_result.get('tweets', [])
                        langs = '/'.join(t['lang'] for t in tweets)
                        log(f"     🐦 X Thread {len(tweets)} 則 ({langs}): {x_result.get('main_url', '')}")
                        with _x_fail_lock:
                            _x_fail_count = 0
                    else:
                        with _x_fail_lock:
                            _x_fail_count += 1
                        log(f"     ⚠️ X 發推失敗 (連續{_x_fail_count}次): {x_result.get('error', '')[:80]}")
                except Exception as e:
                    with _x_fail_lock:
                        _x_fail_count += 1
                    log(f"     ⚠️ X 發推例外 (連續{_x_fail_count}次): {e}")

            # git commit（加鎖防止併發衝突）
            if ok > 0:
                import subprocess
                cwd = str(Path(__file__).parent)
                with _git_lock:
                    r1 = subprocess.run(["git", "add", "articles/"], cwd=cwd, capture_output=True)
                    r2 = subprocess.run(
                        ["git", "commit", "-m", f"flash: {post['id'][:20]} 即時快報 ({direction})"],
                        cwd=cwd, capture_output=True,
                    )
                    if r2.returncode == 0:
                        log(f"     📝 Git commit 完成")
                    else:
                        log(f"     ⚠️ Git commit 失敗: {r2.stderr.decode()[:100]}")
        except Exception as e:
            log(f"     ⚠️ 即時快報失敗: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# === 主程式 ===

_x_poll_counter = 0  # X 掃描計數器（每 X_POLL_MULTIPLIER 輪掃一次）


def run_once():
    """跑一次掃描（Truth Social RSS + 定期 X timeline）。"""
    global _x_poll_counter
    total_new = 0

    # === Truth Social RSS（每輪都掃）===
    seen = load_seen()
    try:
        items, fetch_ms = fetch_rss()
    except Exception as e:
        log(f"❌ RSS 抓取失敗: {e}")
        return -1  # 負數表示 RSS 失敗

    new_posts = []
    for item in items:
        if item["id"] and item["id"] not in seen:
            new_posts.append(item)
            seen.add(item["id"])

    if new_posts:
        log(f"📡 TS 掃描: {len(items)} 篇, {len(new_posts)} 篇新的 ({fetch_ms:.0f}ms)")
        for post in new_posts:
            on_new_post(post)
        save_seen(seen)
    total_new += len(new_posts)

    # === X Timeline（每 X_POLL_MULTIPLIER 輪掃一次 = 約 5 分鐘）===
    _x_poll_counter += 1
    if _x_poll_counter >= X_POLL_MULTIPLIER:
        _x_poll_counter = 0
        x_seen = load_x_seen()
        try:
            x_items, x_ms = fetch_x_timeline()

            # 首次掃描：靜默初始化（不觸發 on_new_post）
            if not x_seen and x_items:
                for item in x_items:
                    if item["id"]:
                        x_seen.add(item["id"])
                save_x_seen(x_seen)
                log(f"📡 X 初始化: 已知 {len(x_seen)} 篇，開始監控新推文")
            else:
                x_new = []
                for item in x_items:
                    if item["id"] and item["id"] not in x_seen:
                        x_new.append(item)
                        x_seen.add(item["id"])

                if x_new:
                    log(f"📡 X 掃描: {len(x_items)} 篇, {len(x_new)} 篇新的 ({x_ms:.0f}ms)")
                    for post in x_new:
                        log(f"  🐦 X 新推文: {post['content'][:80]}")
                        on_new_post(post)
                    save_x_seen(x_seen)
                    total_new += len(x_new)
        except Exception as e:
            log(f"  ⚠️ X 掃描失敗: {e}")

    return total_new


def run_loop():
    """持續監控（Truth Social + X 雙源）。"""
    log("=" * 55)
    log("🔴 Trump 雙源監控器（TS + X）")
    log(f"   Truth Social: {RSS_URL} (每 {POLL_INTERVAL}s)")
    log(f"   X Timeline: @realDonaldTrump (每 {POLL_INTERVAL * X_POLL_MULTIPLIER}s)")
    log("=" * 55)

    # 第一次掃描：初始化已看過的 ID（不觸發 on_new_post）
    seen = load_seen()
    if not seen:
        log("首次啟動，初始化已知推文...")
        try:
            items, _ = fetch_rss()
            for item in items:
                if item["id"]:
                    seen.add(item["id"])
            save_seen(seen)
            log(f"  已知 {len(seen)} 篇，開始監控新推文。")
        except Exception as e:
            log(f"  初始化失敗: {e}")

    heartbeat = 0
    last_article_date = None  # 追蹤上次生成文章的日期
    last_health_hour = -1     # 追蹤上次健康檢查的小時
    consecutive_rss_fails = 0 # 連續 RSS 失敗次數
    health_file = Path(__file__).parent / "data" / "health_status.json"

    def _write_health(status: str, details: dict = None):
        """寫入健康狀態檔 — 外部監控可讀。"""
        health = {
            "status": status,  # "ok" / "degraded" / "down"
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "uptime_min": heartbeat * POLL_INTERVAL // 60,
            "consecutive_rss_fails": consecutive_rss_fails,
            "consecutive_x_fails": _x_fail_count,
            "details": details or {},
        }
        try:
            health_file.write_text(json.dumps(health, ensure_ascii=False, indent=2))
        except Exception:
            pass

    def _self_check() -> list[str]:
        """自我健康檢查，回傳問題清單。"""
        issues = []

        # 1. RSS 連續失敗？
        if consecutive_rss_fails >= 5:
            issues.append(f"🔴 RSS 連續失敗 {consecutive_rss_fails} 次")

        # 2. X API key 有效？
        try:
            from x_poster import API_KEY, ACCESS_TOKEN
            if not API_KEY or not ACCESS_TOKEN:
                issues.append("🔴 X API key 未設定")
        except Exception:
            issues.append("🔴 x_poster import 失敗")

        # 3. X 連續發推失敗？
        if _x_fail_count >= 3:
            issues.append(f"🟡 X 連續發推失敗 {_x_fail_count} 次")

        # 4. LLM 可用？
        try:
            from realtime_loop import HAS_WASHIN_LLM
            if not HAS_WASHIN_LLM:
                issues.append("🟡 washin_llm 不可用（快報會降級）")
        except Exception:
            pass

        # 5. 磁碟空間（articles 目錄）
        try:
            import shutil
            usage = shutil.disk_usage(Path(__file__).parent)
            free_gb = usage.free / (1024**3)
            if free_gb < 1:
                issues.append(f"🟡 磁碟剩餘 {free_gb:.1f}GB")
        except Exception:
            pass

        return issues

    while True:
        try:
            new_count = run_once()
            if new_count >= 0:
                consecutive_rss_fails = 0
            else:
                consecutive_rss_fails += 1
            heartbeat += 1

            # 每 10 輪（5 分鐘）印一次心跳
            if heartbeat % 10 == 0 and new_count == 0:
                log(f"💓 心跳: 已監控 {heartbeat * POLL_INTERVAL // 60} 分鐘, 無新推文")

            # === 每小時自我健康檢查 ===
            now_utc = datetime.now(timezone.utc)
            if now_utc.hour != last_health_hour:
                last_health_hour = now_utc.hour
                issues = _self_check()
                if issues:
                    status = "down" if any("🔴" in i for i in issues) else "degraded"
                    log(f"🏥 健康檢查: {status}")
                    for issue in issues:
                        log(f"   {issue}")
                    _write_health(status, {"issues": issues})
                else:
                    _write_health("ok")
                    if heartbeat > 1:  # 不在啟動時印
                        log(f"🏥 健康檢查: ✅ 全部正常")

            # === 每日文章自動生成 ===
            # 美東 23:00-23:59（UTC 04:00-04:59）觸發當天文章
            today_str = now_utc.strftime('%Y-%m-%d')
            if now_utc.hour == 4 and last_article_date != today_str:
                # 生成前一天的文章（美東今天 = UTC 明天凌晨）
                from datetime import timedelta
                yesterday = (now_utc - timedelta(days=1)).strftime('%Y-%m-%d')
                log(f"📝 每日文章觸發：生成 {yesterday} 三語分析...")
                try:
                    from article_generator import full_pipeline
                    meta = full_pipeline(yesterday)
                    ok = sum(1 for v in meta.get('articles', {}).values() if v.get('status') == 'ok')
                    log(f"📝 文章完成：{ok}/3 語言成功")

                    # Git commit + push
                    import subprocess
                    cwd = str(Path(__file__).parent)
                    subprocess.run(["git", "add", "articles/"], cwd=cwd, capture_output=True)
                    subprocess.run(["git", "commit", "-m", f"daily: {yesterday} 三語分析文章"], cwd=cwd, capture_output=True)
                    subprocess.run(["git", "push"], cwd=cwd, capture_output=True)
                    log(f"📝 Git push 完成")
                except Exception as e:
                    log(f"📝 文章生成失敗: {e}")
                last_article_date = today_str

        except KeyboardInterrupt:
            log("停止。")
            _write_health("stopped", {"reason": "KeyboardInterrupt"})
            break
        except Exception as e:
            log(f"⚠️ 錯誤: {e}")
            # RSS 失敗計數（run_once 裡 exception 會到這）
            if "RSS" in str(e) or "urllib" in str(e):
                consecutive_rss_fails += 1

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        run_once()
    elif len(sys.argv) > 1 and sys.argv[1] == "--test":
        # 測試模式：拿最新一篇當新推文
        items, ms = fetch_rss()
        log(f"測試模式: RSS {len(items)} 篇, {ms:.0f}ms")
        if items:
            on_new_post(items[0])
    else:
        run_loop()
