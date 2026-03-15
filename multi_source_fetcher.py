#!/usr/bin/env python3
"""
川普密碼 — 多源資料抓取器
三個來源同時抓，互相比對，任何一個掛了其他的補上

來源 1: CNN Archive（CSV，每 5 分鐘更新）
來源 2: trumpstruth.org（HTML 爬取）
來源 3: Truth Social API（需帳號，預留接口）

用法:
  from multi_source_fetcher import fetch_all_sources
  posts, report = fetch_all_sources()
"""

import csv
import html
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent
DATA = BASE / "data"

NOW = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# 來源 1: CNN Archive
# ============================================================
def fetch_cnn_archive(since_date="2025-01-20"):
    """從 CNN Archive 下載完整 CSV"""
    url = "https://ix.cnn.io/data/truth-social/truth_archive.csv"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'TrumpCode/1.0 (research project)'
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode('utf-8')
            last_modified = resp.headers.get('Last-Modified', 'unknown')

        reader = csv.DictReader(raw.splitlines())
        posts = []
        for row in reader:
            if not row.get('content') or not row.get('created_at'):
                continue
            if not row['created_at'].startswith('20'):
                continue
            content = row['content'].strip()
            if not content:
                continue
            # 編碼修復
            try:
                content = content.encode('latin-1').decode('utf-8')
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
            content = html.unescape(content)

            if row['created_at'] >= since_date:
                posts.append({
                    'id': row.get('id', ''),
                    'created_at': row['created_at'],
                    'content': content,
                    'url': row.get('url', ''),
                    'source': 'cnn',
                })

        return {
            'status': 'ok',
            'source': 'cnn',
            'count': len(posts),
            'last_modified': last_modified,
            'posts': sorted(posts, key=lambda p: p['created_at']),
        }

    except Exception as e:
        return {
            'status': 'error',
            'source': 'cnn',
            'error': str(e),
            'count': 0,
            'posts': [],
        }


# ============================================================
# 來源 2: trumpstruth.org
# ============================================================
def fetch_trumpstruth(pages=5):
    """從 trumpstruth.org 爬取最新推文"""
    all_posts = []

    for page in range(1, pages + 1):
        try:
            url = f"https://trumpstruth.org/?page={page}"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'text/html',
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                page_html = resp.read().decode('utf-8')

            # 解析 HTML — trumpstruth.org 的結構
            # URL: data-status-url="https://trumpstruth.org/statuses/XXXXX"
            # 內容: <div class="status__content">...</div>
            # 時間: "March 14, 2026, 3:25 PM" 文字格式

            status_urls = re.findall(r'data-status-url="([^"]*)"', page_html)
            contents = re.findall(
                r'<div class="status__content">\s*(.*?)\s*</div>',
                page_html, re.DOTALL
            )
            times = re.findall(
                r'(\w+ \d{1,2}, \d{4},?\s*\d{1,2}:\d{2}\s*[AP]M)',
                page_html
            )

            n = min(len(status_urls), len(contents))
            for i in range(n):
                url_raw = status_urls[i].strip()
                pid_match = re.search(r'statuses/(\d+)', url_raw)
                pid = pid_match.group(1) if pid_match else str(i)

                content = re.sub(r'<[^>]+>', '', contents[i]).strip()

                post_time = ''
                if i < len(times):
                    try:
                        raw_time = re.sub(r'\s+', ' ', times[i].strip())
                        raw_time = raw_time.replace(',', '')
                        dt = datetime.strptime(raw_time, '%B %d %Y %I:%M %p')
                        post_time = dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                    except ValueError:
                        pass

                if content and len(content) > 10:
                    all_posts.append({
                        'id': pid,
                        'created_at': post_time,
                        'content': content,
                        'url': url_raw,
                        'source': 'trumpstruth',
                    })

        except Exception as e:
            log(f"   trumpstruth.org 第 {page} 頁失敗: {e}")
            continue

    # 去重
    seen_ids = set()
    unique = []
    for p in all_posts:
        if p['id'] not in seen_ids:
            seen_ids.add(p['id'])
            unique.append(p)

    return {
        'status': 'ok' if unique else 'partial',
        'source': 'trumpstruth',
        'count': len(unique),
        'posts': unique,
    }


# ============================================================
# 來源 3: Truth Social 直接 API（需帳號）
# ============================================================
def fetch_truthsocial_direct(username=None, password=None):
    """
    直接從 Truth Social API 抓取
    需要帳號密碼，目前預留接口

    啟用方式：
    1. 註冊 Truth Social 帳號
    2. 設定環境變數 TRUTHSOCIAL_USERNAME 和 TRUTHSOCIAL_PASSWORD
    3. 或直接傳入 username/password 參數
    """
    import os
    username = username or os.environ.get('TRUTHSOCIAL_USERNAME')
    password = password or os.environ.get('TRUTHSOCIAL_PASSWORD')

    if not username or not password:
        return {
            'status': 'not_configured',
            'source': 'truthsocial_direct',
            'count': 0,
            'posts': [],
            'note': '需要設定 TRUTHSOCIAL_USERNAME 和 TRUTHSOCIAL_PASSWORD 環境變數',
        }

    try:
        # Truth Social 用 Mastodon OAuth2 登入
        # 步驟 1: 取得 OAuth token
        login_data = json.dumps({
            'grant_type': 'password',
            'username': username,
            'password': password,
            'client_id': 'xxxxxxxxxx',  # 需要從 Truth Social app 取得
            'client_secret': 'xxxxxxxxxx',
            'scope': 'read',
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://truthsocial.com/oauth/token',
            data=login_data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            token_data = json.loads(resp.read().decode('utf-8'))
            access_token = token_data.get('access_token')

        if not access_token:
            return {
                'status': 'auth_failed',
                'source': 'truthsocial_direct',
                'count': 0,
                'posts': [],
            }

        # 步驟 2: 用 token 抓 Trump 的推文
        # Trump 的帳號 ID: 107780257626128497
        trump_id = '107780257626128497'
        api_url = f'https://truthsocial.com/api/v1/accounts/{trump_id}/statuses?exclude_replies=true&limit=40'

        req = urllib.request.Request(api_url, headers={
            'Authorization': f'Bearer {access_token}',
            'User-Agent': 'TrumpCode/1.0',
        })

        with urllib.request.urlopen(req, timeout=30) as resp:
            statuses = json.loads(resp.read().decode('utf-8'))

        posts = []
        for s in statuses:
            content = re.sub(r'<[^>]+>', '', s.get('content', '')).strip()
            if content:
                posts.append({
                    'id': s.get('id', ''),
                    'created_at': s.get('created_at', ''),
                    'content': content,
                    'url': s.get('url', ''),
                    'source': 'truthsocial_direct',
                })

        return {
            'status': 'ok',
            'source': 'truthsocial_direct',
            'count': len(posts),
            'posts': posts,
        }

    except Exception as e:
        return {
            'status': 'error',
            'source': 'truthsocial_direct',
            'error': str(e),
            'count': 0,
            'posts': [],
        }


# ============================================================
# 多源比對引擎
# ============================================================
def cross_check(sources):
    """
    比對多個來源的資料，找出差異
    回傳比對報告
    """
    report = {
        'timestamp': NOW,
        'sources': {},
        'cross_check': {},
    }

    # 記錄每個來源的狀態
    for src in sources:
        report['sources'][src['source']] = {
            'status': src['status'],
            'count': src['count'],
            'error': src.get('error', None),
        }

    # 找出有資料的來源
    active = [s for s in sources if s['status'] == 'ok' and s['count'] > 0]

    if len(active) < 2:
        report['cross_check'] = {
            'status': 'insufficient_sources',
            'active_count': len(active),
            'note': '需要至少 2 個來源才能交叉比對',
        }
        return report

    # 用最近 10 篇推文比對
    primary = active[0]  # CNN 通常最完整
    secondary = active[1]

    # 取最近的推文
    p_recent = sorted(primary['posts'], key=lambda p: p['created_at'], reverse=True)[:10]
    s_recent = sorted(secondary['posts'], key=lambda p: p['created_at'], reverse=True)[:10]

    # 比對方法：用內容前 50 字做指紋
    p_fingerprints = {p['content'][:50].lower().strip(): p for p in p_recent}
    s_fingerprints = {p['content'][:50].lower().strip(): p for p in s_recent}

    matched = 0
    p_only = 0
    s_only = 0

    for fp in p_fingerprints:
        if fp in s_fingerprints:
            matched += 1
        else:
            p_only += 1

    for fp in s_fingerprints:
        if fp not in p_fingerprints:
            s_only += 1

    total_checked = matched + p_only + s_only
    match_rate = matched / max(total_checked, 1) * 100

    report['cross_check'] = {
        'status': 'ok',
        'primary': primary['source'],
        'secondary': secondary['source'],
        'checked': total_checked,
        'matched': matched,
        'primary_only': p_only,
        'secondary_only': s_only,
        'match_rate': round(match_rate, 1),
        'verdict': 'CONSISTENT' if match_rate > 70 else ('PARTIAL' if match_rate > 40 else 'INCONSISTENT'),
    }

    return report


# ============================================================
# 主函數：三源同時抓 + 比對 + 合併
# ============================================================
def fetch_all_sources(since_date="2025-01-20"):
    """
    三個來源同時抓，比對後合併成最完整的資料集

    回傳:
      posts: 合併後的推文列表（以 CNN 為主，其他來源補漏）
      report: 比對報告
    """
    log("📡 多源資料抓取器啟動")
    log(f"   三個來源同時抓取中...")

    # 同時抓三個來源
    results = {}

    # 來源 1: CNN
    log("   [1/3] CNN Archive...")
    results['cnn'] = fetch_cnn_archive(since_date)
    log(f"        → {results['cnn']['status']}: {results['cnn']['count']} 篇")

    # 來源 2: trumpstruth.org
    log("   [2/3] trumpstruth.org...")
    results['trumpstruth'] = fetch_trumpstruth(pages=3)
    log(f"        → {results['trumpstruth']['status']}: {results['trumpstruth']['count']} 篇")

    # 來源 3: Truth Social 直接
    log("   [3/3] Truth Social API...")
    results['truthsocial'] = fetch_truthsocial_direct()
    log(f"        → {results['truthsocial']['status']}: {results['truthsocial']['count']} 篇")

    # 交叉比對
    log("   🔄 交叉比對中...")
    all_sources = list(results.values())
    report = cross_check(all_sources)

    # 合併：以最完整的來源為主
    # 優先順序：CNN（最完整）> truthsocial_direct > trumpstruth
    primary_posts = []
    primary_source = None

    for src_name in ['cnn', 'truthsocial_direct', 'trumpstruth']:
        src = results.get(src_name, {})
        if src.get('status') == 'ok' and src.get('count', 0) > 0:
            if len(src['posts']) > len(primary_posts):
                primary_posts = src['posts']
                primary_source = src_name

    if not primary_posts:
        log("   ❌ 所有來源都失敗！")
        return [], report

    # 從其他來源補漏
    # 用 content 前 50 字做指紋
    existing_fps = {p['content'][:50].lower().strip() for p in primary_posts}
    added_from_others = 0

    for src_name, src in results.items():
        if src_name == primary_source or src.get('status') != 'ok':
            continue
        for p in src.get('posts', []):
            fp = p['content'][:50].lower().strip()
            if fp not in existing_fps and len(fp) > 10:
                primary_posts.append(p)
                existing_fps.add(fp)
                added_from_others += 1

    # 排序
    primary_posts.sort(key=lambda p: p.get('created_at', ''))

    log(f"   ✅ 合併完成: {len(primary_posts)} 篇（主源: {primary_source}, 補漏: {added_from_others} 篇）")

    # 加入比對摘要
    report['merge'] = {
        'primary_source': primary_source,
        'total_posts': len(primary_posts),
        'added_from_others': added_from_others,
    }

    # 印出比對報告
    cc = report.get('cross_check', {})
    if cc.get('status') == 'ok':
        log(f"   📊 比對: {cc['primary']} vs {cc['secondary']}")
        log(f"      吻合: {cc['matched']}/{cc['checked']} ({cc['match_rate']}%)")
        log(f"      判定: {cc['verdict']}")
    elif cc.get('status') == 'insufficient_sources':
        log(f"   ⚠️ 只有 {cc['active_count']} 個來源有資料，無法交叉比對")

    # 存比對報告
    report_file = DATA / "source_check_report.json"
    try:
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return primary_posts, report


# ============================================================
# 獨立執行：測試所有來源
# ============================================================
def main():
    print("=" * 70)
    print("🔐 川普密碼 — 多源資料抓取測試")
    print("=" * 70)

    posts, report = fetch_all_sources()

    print(f"\n{'='*70}")
    print("📋 報告摘要")
    print("=" * 70)

    for src, info in report['sources'].items():
        emoji = "✅" if info['status'] == 'ok' else ("⚠️" if info['status'] == 'not_configured' else "❌")
        print(f"  {emoji} {src:20s} | {info['status']:15s} | {info['count']} 篇")

    cc = report.get('cross_check', {})
    if cc.get('status') == 'ok':
        print(f"\n  比對結果: {cc['verdict']}")
        print(f"  吻合率: {cc['match_rate']}%")

    merge = report.get('merge', {})
    print(f"\n  最終: {merge.get('total_posts', 0)} 篇（主源: {merge.get('primary_source')}）")
    print(f"  從其他來源補漏: {merge.get('added_from_others', 0)} 篇")

    # 顯示最新 3 篇
    if posts:
        print(f"\n  最新 3 篇:")
        for p in posts[-3:]:
            print(f"    [{p['source']}] {p['created_at'][:16]} | {p['content'][:80]}...")


if __name__ == '__main__':
    main()


# ============================================================
# 來源 4: X (Twitter) API — 用 Bearer Token 直接抓
# ============================================================
def fetch_x_api():
    """用 X API v2 抓 Trump 在 X 上的推文"""
    import os
    bearer = os.environ.get('X_BEARER_TOKEN', '')

    if not bearer:
        # 嘗試從 .env 讀
        env_file = Path(__file__).parent / '.env'
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    if line.startswith('X_BEARER_TOKEN='):
                        bearer = line.strip().split('=', 1)[1]

    if not bearer:
        return {
            'status': 'not_configured',
            'source': 'x_api',
            'count': 0,
            'posts': [],
            'note': '需要設定 X_BEARER_TOKEN 環境變數',
        }

    try:
        import urllib.request
        all_tweets = []
        next_token = None

        for _ in range(10):  # 最多 10 頁
            url = f'https://api.twitter.com/2/users/25073877/tweets?max_results=100&tweet.fields=created_at,text&start_time=2025-01-20T00:00:00Z'
            if next_token:
                url += f'&pagination_token={next_token}'

            req = urllib.request.Request(url, headers={
                'Authorization': f'Bearer {bearer}',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            if 'data' not in data:
                break

            all_tweets.extend(data['data'])
            next_token = data.get('meta', {}).get('next_token')
            if not next_token:
                break
            import time
            time.sleep(1)

        posts = []
        for t in all_tweets:
            if not t['text'].startswith('RT @'):
                posts.append({
                    'id': t['id'],
                    'created_at': t.get('created_at', ''),
                    'content': t['text'],
                    'url': f"https://x.com/realDonaldTrump/status/{t['id']}",
                    'source': 'x_api',
                })

        return {
            'status': 'ok',
            'source': 'x_api',
            'count': len(posts),
            'posts': posts,
        }

    except Exception as e:
        return {
            'status': 'error',
            'source': 'x_api',
            'error': str(e),
            'count': 0,
            'posts': [],
        }
