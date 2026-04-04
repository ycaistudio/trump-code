#!/usr/bin/env python3
"""
Musk X 推文 — 用 Full Archive Search 回填缺口。
/2/tweets/search/all endpoint（Pro tier）拉 2025-04 ~ 2026-02 的缺口。
"""

import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
DATA = BASE / "data"

BEARER_TOKEN = ""
env_file = BASE / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if line.startswith("X_BEARER_TOKEN="):
            BEARER_TOKEN = line.split("=", 1)[1].strip()

TWEET_FIELDS = "created_at,public_metrics,text,author_id,in_reply_to_user_id,lang"


def fetch_search_page(query, pagination_token=None, start_time=None, end_time=None):
    """用 full archive search 抓一頁。"""
    params = {
        "query": query,
        "max_results": "100",
        "tweet.fields": TWEET_FIELDS,
        "sort_order": "recency",
    }
    if pagination_token:
        params["next_token"] = pagination_token
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time

    url = f"https://api.x.com/2/tweets/search/all?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "User-Agent": "TrumpCode-MuskBackfill/1.0",
    })

    with urllib.request.urlopen(req, timeout=30) as resp:
        remaining = resp.headers.get("x-rate-limit-remaining", "?")
        reset = resp.headers.get("x-rate-limit-reset", "?")
        data = json.load(resp)
    return data, remaining, reset


def normalize(t: dict) -> dict:
    """統一欄位格式。"""
    pm = t.get("public_metrics", {})
    return {
        "content": t.get("text", ""),
        "created_at": t.get("created_at", ""),
        "likes": pm.get("like_count", 0),
        "retweets": pm.get("retweet_count", 0),
        "replies": pm.get("reply_count", 0),
        "quotes": pm.get("quote_count", 0),
        "impressions": pm.get("impression_count", 0),
        "tweet_id": t.get("id", ""),
        "lang": t.get("lang", ""),
        "is_reply": t.get("in_reply_to_user_id") is not None,
    }


def fetch_month(query, year, month):
    """抓某月份全部推文（含分頁）。"""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    tweets = []
    next_token = None
    page = 0
    remaining = "?"

    while True:
        page += 1
        try:
            data, remaining, reset = fetch_search_page(
                query=query,
                pagination_token=next_token,
                start_time=start_str,
                end_time=end_str,
            )
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Full archive search: 1 req/sec, 300 req/15min
                # 如果超了，等到 reset 時間
                try:
                    reset_ts = int(reset)
                    wait = max(reset_ts - int(time.time()), 5)
                except (ValueError, TypeError):
                    wait = 60
                print(f"\n  ⚠️ 429 Rate Limited — 等 {wait} 秒...", flush=True)
                time.sleep(wait)
                continue
            else:
                body = e.read().decode()[:300]
                print(f"\n  ❌ HTTP {e.code}: {body}")
                break
        except Exception as e:
            print(f"\n  ❌ {e}")
            break

        batch = data.get("data", [])
        if not batch:
            break

        for t in batch:
            tweets.append(normalize(t))

        next_token = data.get("meta", {}).get("next_token")
        if not next_token:
            break

        # Full archive search rate limit: 1 req/sec, 300/15min
        time.sleep(1.1)

    return tweets, remaining


def main():
    print("=" * 60)
    print("🐦 MUSK CODE — Full Archive Search 回填")
    print("=" * 60)

    # 載入現有
    fp = DATA / "musk_posts.json"
    existing = json.load(open(fp, encoding="utf-8")) if fp.exists() else []
    if isinstance(existing, dict):
        existing = existing.get("posts", existing.get("data", []))
    print(f"📦 現有: {len(existing)} 篇")

    # 建去重 set
    seen = set()
    for p in existing:
        key = (p.get("created_at", ""), p.get("content", "")[:50])
        seen.add(key)

    # 查: from:elonmusk -is:retweet（排除轉推，只要原創）
    query = "from:elonmusk -is:retweet"

    # 要回填的月份: 2025-04 ~ 2026-01
    months = []
    for m in range(4, 13):  # 2025-04 ~ 2025-12
        months.append((2025, m))
    months.append((2026, 1))  # 2026-01

    total_new = 0
    remaining = "?"

    for y, m in months:
        label = f"{y}-{m:02d}"
        print(f"\n📅 {label}...", end=" ", flush=True)

        tweets, remaining = fetch_month(query, y, m)

        added = 0
        for t in tweets:
            key = (t.get("created_at", ""), t.get("content", "")[:50])
            if key not in seen:
                existing.append(t)
                seen.add(key)
                added += 1

        total_new += added
        print(f"抓 {len(tweets)} / 新增 {added} | 額度剩 {remaining}")

    if total_new == 0:
        print("\n沒有新資料要補。")
        return

    # 排序 + 存檔
    existing.sort(key=lambda x: x.get("created_at", ""))
    print(f"\n🔀 總計新增: {total_new} 篇 → 合計 {len(existing)} 篇")

    with open(fp, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=None)

    # 月份分佈
    from collections import Counter
    all_months = Counter((p.get("created_at", "") or "")[:7] for p in existing)
    print(f"\n📊 現有資料月份分佈（回填後）:")
    for label in sorted(all_months.keys()):
        if label >= "2025-04":
            print(f"   {label}: {all_months[label]} 篇")

    print(f"\n💾 已存: {fp} ({len(existing)} 篇)")
    print(f"🏁 額度剩 {remaining}")


if __name__ == "__main__":
    main()
