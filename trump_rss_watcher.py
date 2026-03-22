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
SEEN_FILE = Path(__file__).parent / "data" / "rss_seen_ids.json"
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
    except:
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


def load_seen() -> set:
    """載入已看過的 ID。"""
    if SEEN_FILE.exists():
        try:
            return set(json.load(open(SEEN_FILE)))
        except:
            pass
    return set()


def save_seen(seen: set):
    """存已看過的 ID（只保留最近 500 個）。"""
    recent = sorted(seen)[-500:]
    with open(SEEN_FILE, "w") as f:
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
                entries = json.load(open(LATENCY_LOG))
            except:
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
        if confidence and confidence >= 0.5:
            _trigger_flash_article(post, signals, direction, confidence)
    else:
        log(f"     ⚪ 無信號 ({classify_ms:.0f}ms)")


def _append_pipeline_log(post, detect_latency, classify_ms, predict_ms,
                         signals, direction, confidence, triggered):
    """記錄完整的流水線計時到 JSON。"""
    try:
        pipeline_log_file = Path(__file__).parent / "data" / "rss_pipeline_log.json"
        entries = []
        if pipeline_log_file.exists():
            try:
                entries = json.load(open(pipeline_log_file))
            except:
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


def _trigger_flash_article(post: dict, signals: list, direction: str, confidence: float):
    """背景執行即時三語快報生成（不阻塞 RSS 監控迴圈）。"""
    def _run():
        try:
            log(f"     📝 即時快報生成中...")
            from article_generator import generate_flash
            meta = generate_flash(post, signals, direction, confidence)
            ok = sum(1 for v in meta.get('articles', {}).values() if v.get('status') == 'ok')
            log(f"     📝 即時快報完成：{ok}/3 語言成功")

            # 自動 git commit（快報）
            if ok > 0:
                import subprocess
                cwd = str(Path(__file__).parent)
                subprocess.run(["git", "add", "articles/"], cwd=cwd, capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", f"flash: {post['id'][:20]} 即時快報 ({direction})"],
                    cwd=cwd, capture_output=True,
                )
                log(f"     📝 Git commit 完成")
        except Exception as e:
            log(f"     ⚠️ 即時快報失敗: {e}")

    # 背景執行，不阻塞 RSS 監控
    t = threading.Thread(target=_run, daemon=True)
    t.start()


# === 主程式 ===

def run_once():
    """跑一次掃描。"""
    seen = load_seen()
    initial_count = len(seen)

    try:
        items, fetch_ms = fetch_rss()
    except Exception as e:
        log(f"❌ RSS 抓取失敗: {e}")
        return 0

    new_posts = []
    for item in items:
        if item["id"] and item["id"] not in seen:
            new_posts.append(item)
            seen.add(item["id"])

    if new_posts:
        log(f"📡 RSS 掃描: {len(items)} 篇, {len(new_posts)} 篇新的 ({fetch_ms:.0f}ms)")
        for post in new_posts:
            on_new_post(post)
        save_seen(seen)
    else:
        # 靜默 — 沒新推文不刷 log（每 10 輪印一次心跳）
        pass

    return len(new_posts)


def run_loop():
    """持續監控。"""
    log("=" * 55)
    log("🔴 Trump RSS 高頻監控器")
    log(f"   來源: {RSS_URL}")
    log(f"   間隔: {POLL_INTERVAL} 秒")
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

    while True:
        try:
            new_count = run_once()
            heartbeat += 1
            # 每 10 輪（5 分鐘）印一次心跳
            if heartbeat % 10 == 0 and new_count == 0:
                log(f"💓 心跳: 已監控 {heartbeat * POLL_INTERVAL // 60} 分鐘, 無新推文")

            # === 每日文章自動生成 ===
            # 美東 23:00-23:59（UTC 04:00-04:59）觸發當天文章
            now_utc = datetime.now(timezone.utc)
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
                    subprocess.run(["git", "add", f"articles/"], cwd=str(Path(__file__).parent), capture_output=True)
                    subprocess.run(["git", "commit", "-m", f"daily: {yesterday} 三語分析文章"], cwd=str(Path(__file__).parent), capture_output=True)
                    subprocess.run(["git", "push"], cwd=str(Path(__file__).parent), capture_output=True)
                    log(f"📝 Git push 完成")
                except Exception as e:
                    log(f"📝 文章生成失敗: {e}")
                last_article_date = today_str

        except KeyboardInterrupt:
            log("停止。")
            break
        except Exception as e:
            log(f"⚠️ 錯誤: {e}")

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
