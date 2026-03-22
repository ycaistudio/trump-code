#!/usr/bin/env python3
"""
川普密碼 — 即時閉環引擎（Real-Time Closed Loop）

不是每天跑一次的批次作業，是持續運行的即時引擎：

  Trump 發推文 → 幾分鐘內偵測
      ↓
  分類信號 + 快照 Polymarket 價格
      ↓
  做出即時預測（PM 會漲還是跌）
      ↓
  1h/3h/6h 後回來查 → 價格真的動了嗎？
      ↓
  學習：哪些信號能預測 PM 的短期走勢

用法：
  python3 realtime_loop.py              # 持續監控（每 5 分鐘）
  python3 realtime_loop.py --once       # 只跑一次
  python3 realtime_loop.py --verify     # 只跑驗證（追蹤過去的預測）
"""

from __future__ import annotations

import csv
import html
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# washin_llm 共用 LLM 模組（本機 Opus + Gemini 3 Key）
sys.path.insert(0, str(Path.home() / "Projects" / "washin-llm"))
try:
    from washin_llm import call_ai as _washin_call_ai
    HAS_WASHIN_LLM = True
except ImportError:
    HAS_WASHIN_LLM = False

BASE = Path(__file__).parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

ARCHIVE_URL = "https://ix.cnn.io/data/truth-social/truth_archive.csv"
LAST_SEEN_FILE = DATA / "rt_last_seen.txt"
RT_PREDICTIONS_FILE = DATA / "rt_predictions.json"      # 即時預測紀錄
RT_LEARNING_FILE = DATA / "rt_learning.json"             # 即時學習結果
POSTS_ALL_FILE = DATA / "trump_posts_all.json"           # 全量推文（前端讀取用）
POLL_INTERVAL = 300  # 5 分鐘

# === 事件門檻 — 從歷史數據計算（288 個交易日的統計）===
# 平均日波動 ±0.71%，標準差 0.88%
# 「事件」= 超過 95th 百分位 = 大約 ±2% 以上（一年只有 ~15 天）
# 「有感」= 超過 75th 百分位 = 大約 ±0.8% 以上
# 「噪音」= 低於中位數 = ±0.5% 以下

# 股市（SPY）
SPY_EVENT = 2.0         # ±2% 以上 = 大事（一年 ~15 天）
SPY_NOTABLE = 0.8       # ±0.8% 以上 = 有感波動（追蹤但權重低）
SPY_NOISE = 0.5         # ±0.5% 以下 = 噪音（不學）

# 預測市場（Polymarket）
PM_EVENT = 0.10         # ±10¢ 以上 = 大事（市場共識方向改變）
PM_NOTABLE = 0.05       # ±5¢ 以上 = 有感
PM_NOISE = 0.03         # ±3¢ 以下 = 噪音

# 學習權重：大事的經驗值 10 倍，有感的 3 倍，噪音的 0 倍
EVENT_WEIGHT = 10
NOTABLE_WEIGHT = 3
NOISE_WEIGHT = 0


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def _merge_into_posts_all(new_posts: list[dict]) -> int:
    """
    把新偵測到的推文合併進 trump_posts_all.json，讓前端即時顯示。
    用 created_at + content 前 80 字做去重，避免重複寫入。
    回傳實際新增的篇數。
    """
    if not new_posts:
        return 0

    # 讀取現有資料
    data = {}
    if POSTS_ALL_FILE.exists():
        try:
            with open(POSTS_ALL_FILE, encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log(f"   ⚠️ 讀取 trump_posts_all.json 失敗: {e}")
            return 0

    existing_posts = data.get('posts', [])

    # 建立指紋索引（created_at + content 前 80 字）做去重
    existing_fps = set()
    for p in existing_posts:
        fp = (p.get('created_at', '')[:19] + '|' +
              (p.get('content', '') or '')[:80].strip().lower())
        existing_fps.add(fp)

    # 過濾出真正的新推文
    added = 0
    for post in new_posts:
        fp = (post.get('created_at', '')[:19] + '|' +
              post['content'][:80].strip().lower())
        if fp not in existing_fps:
            # 組合成 trump_posts_all.json 的格式
            entry = {
                'id': post.get('id', f"rt_{int(time.time())}_{added}"),
                'created_at': post['created_at'],
                'content': post['content'],
                'url': post.get('url', ''),
                'source': 'realtime_loop',
                'is_retweet': False,
            }
            existing_posts.append(entry)
            existing_fps.add(fp)
            added += 1

    if added == 0:
        return 0

    # 按時間排序（新的在前）
    existing_posts.sort(key=lambda p: p.get('created_at', ''), reverse=True)

    # 更新 metadata
    latest_date = existing_posts[0].get('created_at', '')[:10] if existing_posts else ''
    earliest_date = existing_posts[-1].get('created_at', '')[:10] if existing_posts else ''
    data['total'] = len(existing_posts)
    data['date_range'] = {'earliest': earliest_date, 'latest': latest_date}
    data['posts'] = existing_posts
    data['last_rt_update'] = now_str()

    # 原子寫入（避免中斷損壞）
    try:
        from utils import safe_json_write
        safe_json_write(POSTS_ALL_FILE, data)
    except ImportError:
        # fallback：直接寫（沒有 utils 的情況）
        with open(POSTS_ALL_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    log(f"   📥 寫入 {added} 篇新推文到 trump_posts_all.json（總計 {len(existing_posts)} 篇）")
    return added


def now_str() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# =====================================================================
# ① 偵測新推文
# =====================================================================

def _fetch_from_cnn(limit: int = 20) -> list[dict]:
    """來源 1: CNN Archive — CSV 下載，最穩定。"""
    try:
        req = urllib.request.Request(ARCHIVE_URL, headers={
            "User-Agent": "TrumpCode-RT/1.0",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode('utf-8')

        reader = csv.DictReader(raw.splitlines())
        posts = []
        for row in reader:
            content = (row.get('content') or '').strip()
            created = (row.get('created_at') or '')
            if not content or not created or not created[:4].isdigit():
                continue
            if created < '2025-01-20' or content.startswith('RT @'):
                continue
            try:
                content = content.encode('latin-1').decode('utf-8')
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
            content = html.unescape(content)
            posts.append({
                'created_at': created,
                'content': content,
                'url': row.get('url', ''),
                'source': 'cnn',
            })

        posts.sort(key=lambda p: p['created_at'], reverse=True)
        return posts[:limit]

    except Exception as e:
        log(f"   ⚠️ CNN Archive 失敗: {e}")
        return []


def _fetch_from_trumpstruth(limit: int = 20) -> list[dict]:
    """來源 2: trumpstruth.org — HTML 爬取，CNN 更新慢時靠它補。"""
    import re
    try:
        posts = []
        # 只爬 2 頁（即時引擎不需要太多）
        for page in range(1, 3):
            url = f"https://trumpstruth.org/?page={page}"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'text/html',
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                page_html = resp.read().decode('utf-8')

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
                pid = pid_match.group(1) if pid_match else ''

                content = re.sub(r'<[^>]+>', '', contents[i]).strip()

                post_time = ''
                if i < len(times):
                    try:
                        raw_time = re.sub(r'\s+', ' ', times[i].strip()).replace(',', '')
                        dt = datetime.strptime(raw_time, '%B %d %Y %I:%M %p')
                        post_time = dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')
                    except ValueError:
                        pass

                if content and len(content) > 10 and post_time >= '2025-01-20':
                    posts.append({
                        'created_at': post_time,
                        'content': content,
                        'url': url_raw,
                        'source': 'trumpstruth',
                    })

        # 去重（用內容前 50 字）
        seen = set()
        unique = []
        for p in posts:
            fp = p['content'][:50].lower().strip()
            if fp not in seen:
                seen.add(fp)
                unique.append(p)

        unique.sort(key=lambda p: p['created_at'], reverse=True)
        return unique[:limit]

    except Exception as e:
        log(f"   ⚠️ trumpstruth.org 失敗: {e}")
        return []


def _fetch_from_x_api(limit: int = 10) -> list[dict]:
    """來源 3: X (Twitter) API — 川普在 X 上的獨家貼文，其他來源抓不到。"""
    import os
    bearer = os.environ.get('X_BEARER_TOKEN', '')

    # 嘗試從 .env 讀
    if not bearer:
        env_file = BASE / '.env'
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    if line.startswith('X_BEARER_TOKEN='):
                        bearer = line.strip().split('=', 1)[1]

    if not bearer:
        return []

    try:
        url = (
            'https://api.twitter.com/2/users/25073877/tweets'
            '?max_results=20'
            '&tweet.fields=created_at,text'
            '&start_time=2025-01-20T00:00:00Z'
        )
        req = urllib.request.Request(url, headers={
            'Authorization': f'Bearer {bearer}',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)

        if 'data' not in data:
            return []

        posts = []
        for t in data['data']:
            text = t.get('text', '')
            # 跳過 RT 和純連結推文
            if text.startswith('RT @'):
                continue
            posts.append({
                'created_at': t.get('created_at', ''),
                'content': text,
                'url': f"https://x.com/realDonaldTrump/status/{t['id']}",
                'source': 'x_api',
            })

        posts.sort(key=lambda p: p['created_at'], reverse=True)
        return posts[:limit]

    except Exception as e:
        log(f"   ⚠️ X API 失敗: {e}")
        return []


def fetch_latest_posts(limit: int = 20) -> list[dict]:
    """
    三源抓取：CNN + trumpstruth.org + X API 同時抓，互相補漏。
    CNN 當主源（Truth Social 最完整），trumpstruth 補漏，X API 抓獨家。
    """
    # 來源 1: CNN（主源，Truth Social 推文）
    cnn_posts = _fetch_from_cnn(limit=50)

    # 來源 2: trumpstruth.org（補漏）
    ts_posts = _fetch_from_trumpstruth(limit=30)

    # 來源 3: X API（X 平台獨家貼文）
    x_posts = _fetch_from_x_api(limit=10)

    if not cnn_posts and not ts_posts and not x_posts:
        log("⚠️ 三個來源都抓不到推文！")
        return []

    # 合併：CNN 為主，其他兩個補漏
    # 用 content 前 50 字做指紋去重
    merged = list(cnn_posts)
    existing_fps = {p['content'][:50].lower().strip() for p in merged}

    added_from_ts = 0
    for p in ts_posts:
        fp = p['content'][:50].lower().strip()
        if fp not in existing_fps and len(fp) > 10:
            merged.append(p)
            existing_fps.add(fp)
            added_from_ts += 1

    added_from_x = 0
    for p in x_posts:
        fp = p['content'][:50].lower().strip()
        if fp not in existing_fps and len(fp) > 10:
            merged.append(p)
            existing_fps.add(fp)
            added_from_x += 1

    # 報告來源狀況
    src_parts = [f"CNN:{len(cnn_posts)}"]
    if ts_posts:
        src_parts.append(f"trumpstruth:{len(ts_posts)}(補漏{added_from_ts})")
    if x_posts:
        src_parts.append(f"X:{len(x_posts)}(獨家{added_from_x})")
    log(f"   📡 三源抓取: {' + '.join(src_parts)} → 合計 {len(merged)} 篇")

    # 排序，取最新的
    merged.sort(key=lambda p: p['created_at'], reverse=True)
    return merged[:limit]


def get_new_posts(posts: list[dict]) -> list[dict]:
    """比對上次看到的，回傳新的推文。"""
    last_seen = ""
    if LAST_SEEN_FILE.exists():
        last_seen = LAST_SEEN_FILE.read_text().strip()

    new = [p for p in posts if p['created_at'] > last_seen]

    if posts:
        LAST_SEEN_FILE.write_text(posts[0]['created_at'])

    return new


# =====================================================================
# ② 即時信號分類
# =====================================================================

SIGNAL_KEYWORDS: dict[str, list[str]] = {
    'TARIFF': ['tariff', 'tariffs', 'duty', 'duties', 'reciprocal'],
    'DEAL': ['deal', 'agreement', 'negotiate', 'signed', 'talks'],
    'RELIEF': ['pause', 'delay', 'exempt', 'exception', 'suspend', 'waiver'],
    'ACTION': ['immediately', 'effective', 'hereby', 'executive order', 'just signed'],
    'THREAT': ['ban', 'block', 'restrict', 'sanction', 'punish', 'retaliate'],
    'BULLISH': ['stock market', 'all time high', 'record high', 'great economy', 'jobs'],
    'BEARISH': ['disaster', 'terrible', 'worst', 'crash', 'collapse'],
}


def _classify_post_keywords(content: str) -> list[dict]:
    """關鍵字信號分類（快速 fallback，<1ms）。"""
    cl = content.lower()
    signals = []

    for sig_type, keywords in SIGNAL_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in cl]
        if matched:
            confidence = min(0.95, 0.5 + 0.15 * len(matched))
            signals.append({
                'type': sig_type,
                'confidence': round(confidence, 2),
                'matched_keywords': matched,
                'method': 'keyword',
            })

    # 情緒強化
    caps_ratio = sum(1 for c in content if c.isupper()) / max(sum(1 for c in content if c.isalpha()), 1)
    excl_count = content.count('!')
    if caps_ratio > 0.3 or excl_count > 3:
        for sig in signals:
            sig['confidence'] = min(0.95, sig['confidence'] + 0.1)

    return signals


def _classify_post_llm(content: str) -> list[dict] | None:
    """LLM 因果推理分類（深度分析，約 5-15 秒）。

    川普發文 → LLM 分析：
      1. 他在說什麼（事實提取）
      2. 為什麼重要（因果推理）
      3. 對市場的影響（方向預測 + 信心度）
      4. 跟過去的模式比較

    回傳跟 keyword 版相同格式的 signals list，多一個 reasoning 欄位。
    失敗回傳 None（讓 caller 用 keyword fallback）。
    """
    if not HAS_WASHIN_LLM:
        return None

    prompt = f"""你是川普推文的市場信號分析師。分析以下推文，判斷對金融市場的影響。

推文內容：
---
{content[:500]}
---

請用 JSON 格式回答（只回 JSON，不要其他文字）：
{{
  "signals": [
    {{
      "type": "TARIFF|DEAL|RELIEF|ACTION|THREAT|BULLISH|BEARISH",
      "confidence": 0.0-1.0,
      "reasoning": "一句話：為什麼這個信號重要、預期影響什麼"
    }}
  ],
  "overall_direction": "UP|DOWN|NEUTRAL",
  "overall_confidence": 0.0-1.0,
  "causal_chain": "因為X → 所以Y → 預期Z",
  "historical_pattern": "跟過去哪個事件類似（如果有的話）"
}}

信號類型說明：
- TARIFF：關稅相關（提高/新增/威脅），通常利空
- DEAL：談判/簽約/達成協議，通常利多
- RELIEF：暫緩/豁免/延期，通常利多
- ACTION：即刻行動/行政命令/已簽署，影響大但方向不定
- THREAT：制裁/封鎖/報復，通常利空
- BULLISH：正面經濟評論/股市創高，利多
- BEARISH：負面經濟評論/災難描述，利空

如果推文跟市場無關（日常問候、攻擊政敵但不涉經濟），signals 給空陣列。
confidence 越高代表信號越明確。普通推文 0.3-0.5，明確政策宣布 0.7-0.9。"""

    try:
        result = _washin_call_ai(prompt, json_mode=True, max_tokens=800, temperature=0.2)
        if not result.ok:
            log(f"   ⚠️ LLM 分類失敗: {result.error[:80]}")
            return None

        data = result.json()
        if not data or 'signals' not in data:
            log(f"   ⚠️ LLM 回傳格式不正確")
            return None

        # 轉換成跟 keyword 版相同的格式
        signals = []
        valid_types = {'TARIFF', 'DEAL', 'RELIEF', 'ACTION', 'THREAT', 'BULLISH', 'BEARISH'}
        for s in data['signals']:
            sig_type = str(s.get('type', '')).upper()
            if sig_type not in valid_types:
                continue
            confidence = max(0.1, min(0.95, float(s.get('confidence', 0.5))))
            signals.append({
                'type': sig_type,
                'confidence': round(confidence, 2),
                'reasoning': str(s.get('reasoning', ''))[:200],
                'method': 'llm',
                'llm_model': result.model,
            })

        # 附加 LLM 的整體判斷
        if signals:
            signals[0]['causal_chain'] = str(data.get('causal_chain', ''))[:300]
            signals[0]['historical_pattern'] = str(data.get('historical_pattern', ''))[:200]
            signals[0]['llm_direction'] = data.get('overall_direction', 'NEUTRAL')
            signals[0]['llm_confidence'] = max(0.1, min(0.95, float(data.get('overall_confidence', 0.5))))

        log(f"   🧠 LLM 分類完成: {len(signals)} 信號 ({result.model}, {result.elapsed_ms}ms)")
        return signals

    except Exception as e:
        log(f"   ⚠️ LLM 分類例外: {e}")
        return None


def classify_post(content: str) -> list[dict]:
    """即時分類一篇推文的信號。

    策略：LLM 推理優先 → 關鍵字 fallback。
    LLM 提供因果推理（為什麼→影響→預測），關鍵字提供速度保障。
    兩者都跑時，取 LLM 結果但用關鍵字交叉驗證。
    """
    # 先跑關鍵字（<1ms，永遠有結果）
    kw_signals = _classify_post_keywords(content)

    # 再跑 LLM 推理（5-15 秒，可能失敗）
    llm_signals = _classify_post_llm(content)

    if llm_signals is not None:
        # LLM 成功：用 LLM 結果，但用關鍵字交叉驗證
        kw_types = {s['type'] for s in kw_signals}
        for sig in llm_signals:
            if sig['type'] in kw_types:
                # 關鍵字也偵測到 → 信心度加成
                sig['confidence'] = min(0.95, sig['confidence'] + 0.1)
                sig['cross_validated'] = True
            else:
                sig['cross_validated'] = False
        return llm_signals
    else:
        # LLM 失敗：用關鍵字結果
        return kw_signals


# =====================================================================
# ③ 雙快照：Polymarket + 美股，同時抓
# =====================================================================

def snapshot_sp500() -> dict[str, Any]:
    """
    即時抓美股四指標：
      SPY  — S&P 500 ETF（盤中主力，最精準）
      ES=F — S&P 500 期貨（幾乎 24h，盤外靠它）
      NQ=F — NASDAQ 期貨（科技股方向）
      VIX  — 恐慌指數（越高 = 市場越怕）

    盤中用 SPY，盤外用 ES 期貨，VIX 永遠有值。
    """
    try:
        import yfinance as yf
        result: dict[str, Any] = {'timestamp': now_str(), 'source': 'yfinance'}

        # SPY
        try:
            spy = yf.Ticker("SPY")
            info = spy.fast_info
            spy_price = float(getattr(info, 'last_price', 0) or 0)
            spy_prev = float(getattr(info, 'previous_close', 0) or 0)
            if spy_price and spy_prev:
                result['spy_price'] = round(spy_price, 2)
                result['spy_prev_close'] = round(spy_prev, 2)
                result['spy_change_pct'] = round((spy_price - spy_prev) / spy_prev * 100, 3)
        except Exception:
            pass

        # ES=F（S&P 500 期貨 — 盤外主力）
        try:
            es = yf.Ticker("ES=F")
            es_price = float(getattr(es.fast_info, 'last_price', 0) or 0)
            if es_price:
                result['es_futures'] = round(es_price, 2)
                # 如果 SPY 沒數據（盤外），用 ES 替代
                if 'spy_price' not in result:
                    result['spy_price'] = round(es_price / 10, 2)  # ES ≈ SPY × 10
                    result['source'] = 'es_futures_fallback'
        except Exception:
            pass

        # NQ=F（NASDAQ 期貨）
        try:
            nq = yf.Ticker("NQ=F")
            nq_price = float(getattr(nq.fast_info, 'last_price', 0) or 0)
            if nq_price:
                result['nq_futures'] = round(nq_price, 2)
        except Exception:
            pass

        # VIX（恐慌指數 — 永遠有值）
        try:
            vix = yf.Ticker("^VIX")
            vix_val = float(getattr(vix.fast_info, 'last_price', 0) or 0)
            if vix_val:
                result['vix'] = round(vix_val, 2)
                result['vix_level'] = (
                    'PANIC' if vix_val > 30 else
                    'FEAR' if vix_val > 20 else
                    'NORMAL' if vix_val > 15 else
                    'CALM'
                )
        except Exception:
            pass

        return result

    except ImportError:
        return {'error': 'yfinance not installed', 'timestamp': now_str()}
    except Exception as e:
        return {'error': str(e), 'timestamp': now_str()}


def snapshot_trump_coin() -> dict[str, Any]:
    """
    即時抓 $TRUMP 幣（Official Trump）的價格。
    CoinGecko Free API，不需 API key。
    """
    try:
        url = (
            'https://api.coingecko.com/api/v3/simple/price'
            '?ids=official-trump'
            '&vs_currencies=usd'
            '&include_24hr_change=true'
            '&include_market_cap=true'
        )
        req = urllib.request.Request(url, headers={
            'User-Agent': 'TrumpCode-RT/1.0',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)

        coin = data.get('official-trump', {})
        price = coin.get('usd')
        if price is None:
            return {'error': 'no price data', 'timestamp': now_str()}

        return {
            'price': round(float(price), 4),
            'change_24h': round(float(coin.get('usd_24h_change', 0)), 2),
            'market_cap': round(float(coin.get('usd_market_cap', 0)), 0),
            'timestamp': now_str(),
        }
    except Exception as e:
        log(f"   ⚠️ $TRUMP 幣價快照失敗: {e}")
        return {'error': str(e), 'timestamp': now_str()}


def snapshot_pm_prices() -> dict[str, Any]:
    """
    即時抓 Polymarket 的 Trump 相關市場價格。
    用 /public-search API（跟前端用的一樣，確認能用）。
    """
    try:
        import urllib.parse
        search_params = urllib.parse.urlencode({
            'q': 'trump',
            'limit_per_type': 20,
            'events_status': 'active',
        })
        url = f'https://gamma-api.polymarket.com/public-search?{search_params}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'TrumpCode-RT/1.0',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)

        snapshot = {
            'timestamp': now_str(),
            'markets': [],
        }

        # public-search 回傳: {events: [{title, slug, markets: [{outcomePrices, clobTokenIds, ...}]}]}
        events = data.get('events') or []
        for ev in events:
            title = ev.get('title', '?')
            slug = ev.get('slug', '')
            mkts = ev.get('markets', [])

            for m in mkts:
                question = m.get('question', title)
                outcomes_raw = m.get('outcomePrices', '[]')
                clob_raw = m.get('clobTokenIds', '[]')

                # 這些欄位是 JSON 字串，需要 parse
                try:
                    prices = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                except (json.JSONDecodeError, ValueError):
                    prices = []

                try:
                    clob_ids = json.loads(clob_raw) if isinstance(clob_raw, str) else clob_raw
                except (json.JSONDecodeError, ValueError):
                    clob_ids = []

                outcomes = m.get('outcomes', '["Yes","No"]')
                try:
                    outcome_names = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
                except (json.JSONDecodeError, ValueError):
                    outcome_names = ['Yes', 'No']

                for j, outcome in enumerate(outcome_names):
                    price = float(prices[j]) if j < len(prices) else 0.5
                    tid = clob_ids[j] if j < len(clob_ids) else ''
                    snapshot['markets'].append({
                        'question': question[:100],
                        'token_id': tid[:30] if tid else '',
                        'outcome': outcome,
                        'price': round(price, 4),
                        'slug': slug,
                    })

        log(f"   📊 Polymarket 快照: {len(snapshot['markets'])} 個市場 ({len(events)} 事件)")
        return snapshot

    except Exception as e:
        log(f"   ⚠️ Polymarket 快照失敗: {e}")
        return {'error': str(e), 'timestamp': now_str(), 'markets': []}


# =====================================================================
# ④ 做出即時預測
# =====================================================================

def make_prediction(
    post: dict,
    signals: list[dict],
    pm_snapshot: dict,
    stock_snapshot: dict | None = None,
    coin_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    根據推文信號 + PM 價格 + 美股價格，做出即時雙軌預測。

    同時預測：
      - 預測市場（Polymarket）的方向
      - 美股（S&P 500）的方向
      - 兩者的反應可能不同 → 差異本身是套利信號
    """
    if not signals:
        return None

    # 決定主方向
    bullish_signals = [s for s in signals if s['type'] in ('DEAL', 'RELIEF', 'BULLISH', 'ACTION')]
    bearish_signals = [s for s in signals if s['type'] in ('TARIFF', 'THREAT', 'BEARISH')]

    if len(bullish_signals) > len(bearish_signals):
        direction = 'UP'
        confidence = max(s['confidence'] for s in bullish_signals)
    elif len(bearish_signals) > len(bullish_signals):
        direction = 'DOWN'
        confidence = max(s['confidence'] for s in bearish_signals)
    else:
        direction = 'NEUTRAL'
        confidence = 0.3

    # 找相關的 PM 市場來追蹤
    tracked_markets = []
    sig_types = [s['type'] for s in signals]

    for m in pm_snapshot.get('markets', []):
        question = m.get('question', '').lower()
        # 信號和市場的相關性
        relevant = False
        if 'TARIFF' in sig_types and any(w in question for w in ['tariff', 'trade', 'import']):
            relevant = True
        if 'DEAL' in sig_types and any(w in question for w in ['deal', 'agreement', 'negotiat']):
            relevant = True
        if any(w in question for w in ['trump', 'president']):
            relevant = True

        if relevant:
            tracked_markets.append({
                'token_id': m.get('token_id', ''),
                'question': m.get('question', ''),
                'price_at_signal': m.get('price', 0.5),
            })

    prediction = {
        'id': f"rt_{int(time.time())}",
        'created_at': now_str(),
        'post_time': post['created_at'],
        'post_preview': post['content'][:200],
        'signals': signals,
        'signal_types': sig_types,
        'predicted_direction': direction,
        'confidence': round(confidence, 2),

        # === 雙軌追蹤 ===

        # 預測市場軌
        'tracked_markets': tracked_markets[:5],
        'pm_price_at_signal': tracked_markets[0]['price_at_signal'] if tracked_markets else None,
        'pm_verify_1h': None,
        'pm_verify_3h': None,
        'pm_verify_6h': None,
        'pm_verify_12h': None,   # 持續追蹤：川普效應可能延續好幾天
        'pm_verify_24h': None,
        'pm_verify_48h': None,
        'pm_correct_1h': None,
        'pm_correct_3h': None,

        # 美股軌
        'spy_at_signal': stock_snapshot.get('spy_price') if stock_snapshot else None,
        'es_at_signal': stock_snapshot.get('es_futures') if stock_snapshot else None,
        'spy_change_at_signal': stock_snapshot.get('spy_change_pct') if stock_snapshot else None,
        'spy_verify_1h': None,
        'spy_verify_3h': None,
        'spy_verify_12h': None,
        'spy_verify_24h': None,
        'spy_verify_48h': None,
        'spy_correct_1h': None,
        'spy_correct_3h': None,

        # 雙軌比較（驗證後回填）
        'pm_vs_stock_divergence': None,  # PM 和美股反應是否不同
        'divergence_detail': None,       # 具體差異

        # $TRUMP 幣軌
        'trump_coin_at_signal': coin_snapshot.get('price') if coin_snapshot and 'price' in coin_snapshot else None,
        'trump_coin_24h_change': coin_snapshot.get('change_24h') if coin_snapshot and 'change_24h' in coin_snapshot else None,
        'trump_coin_verify_1h': None,
        'trump_coin_verify_3h': None,
        'trump_coin_verify_6h': None,

        'status': 'LIVE',
    }

    return prediction


# =====================================================================
# ⑤ 驗證過去的即時預測
# =====================================================================

def verify_predictions() -> dict[str, Any]:
    """
    回去查過去的即時預測，看 PM 價格有沒有往預測的方向動。
    """
    if not RT_PREDICTIONS_FILE.exists():
        return {'checked': 0}

    with open(RT_PREDICTIONS_FILE, encoding='utf-8') as f:
        predictions = json.load(f)

    live = [p for p in predictions if p.get('status') == 'LIVE']
    if not live:
        return {'checked': 0}

    log(f"驗證 {len(live)} 個即時預測...")

    try:
        from polymarket_client import get_market_price, PolymarketAPIError
        api_ok = True
    except ImportError:
        api_ok = False

    # 抓一次 $TRUMP 幣價，所有預測共用（避免 rate limit）
    coin_now = snapshot_trump_coin()
    coin_price_now = coin_now.get('price')

    verified_count = 0
    correct_1h = 0
    correct_3h = 0

    for pred in live:
        created = pred.get('created_at', '')
        try:
            created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            hours_elapsed = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            continue

        if hours_elapsed < 1:
            continue  # 還沒過 1 小時

        direction = pred.get('predicted_direction', 'NEUTRAL')
        tracked = pred.get('tracked_markets', [])

        if not tracked or not api_ok:
            if hours_elapsed > 24:
                pred['status'] = 'EXPIRED'
            continue

        # 查最新價格
        price_changes = []
        for tm in tracked:
            tid = tm.get('token_id', '')
            orig_price = tm.get('price_at_signal', 0.5)
            if not tid:
                continue

            try:
                current = get_market_price(tid)
                new_price = float(current.get('price', orig_price))
                change = new_price - orig_price
                price_changes.append(change)
            except Exception:
                continue

        if not price_changes:
            if hours_elapsed > 24:
                pred['status'] = 'EXPIRED'
            continue

        avg_pm_change = sum(price_changes) / len(price_changes)

        # --- PM 軌驗證 ---
        if hours_elapsed >= 1 and pred.get('pm_verify_1h') is None:
            pred['pm_verify_1h'] = round(avg_pm_change, 4)
            if direction == 'UP':
                pred['pm_correct_1h'] = avg_pm_change > 0
            elif direction == 'DOWN':
                pred['pm_correct_1h'] = avg_pm_change < 0
            if coin_price_now and pred.get('trump_coin_verify_1h') is None:
                pred['trump_coin_verify_1h'] = coin_price_now

        if hours_elapsed >= 3 and pred.get('pm_verify_3h') is None:
            pred['pm_verify_3h'] = round(avg_pm_change, 4)
            if direction == 'UP':
                pred['pm_correct_3h'] = avg_pm_change > 0
            elif direction == 'DOWN':
                pred['pm_correct_3h'] = avg_pm_change < 0
            if hours_elapsed >= 3 and coin_price_now and pred.get('trump_coin_verify_3h') is None:
                pred['trump_coin_verify_3h'] = coin_price_now

        # --- 美股軌驗證 ---
        spy_at = pred.get('spy_at_signal')
        if spy_at and api_ok:
            try:
                stock_now = snapshot_sp500()
                spy_now = stock_now.get('spy_price')
                if spy_now and spy_at > 0:
                    spy_change = (spy_now - spy_at) / spy_at * 100

                    if hours_elapsed >= 1 and pred.get('spy_verify_1h') is None:
                        pred['spy_verify_1h'] = round(spy_change, 3)
                        if direction == 'UP':
                            pred['spy_correct_1h'] = spy_change > 0
                        elif direction == 'DOWN':
                            pred['spy_correct_1h'] = spy_change < 0

                    if hours_elapsed >= 3 and pred.get('spy_verify_3h') is None:
                        pred['spy_verify_3h'] = round(spy_change, 3)
                        if direction == 'UP':
                            pred['spy_correct_3h'] = spy_change > 0
                        elif direction == 'DOWN':
                            pred['spy_correct_3h'] = spy_change < 0

                    # 雙軌比較：PM 和美股反應不同嗎？
                    if pred.get('pm_verify_1h') is not None:
                        pm_dir = 'UP' if avg_pm_change > 0 else 'DOWN'
                        stock_dir = 'UP' if spy_change > 0 else 'DOWN'
                        if pm_dir != stock_dir:
                            pred['pm_vs_stock_divergence'] = True
                            pred['divergence_detail'] = (
                                f"PM {pm_dir} {avg_pm_change:+.3f} vs "
                                f"SPY {stock_dir} {spy_change:+.3f}%"
                            )
                        else:
                            pred['pm_vs_stock_divergence'] = False
            except Exception:
                pass

        # === 事件分級 ===
        spy_move = abs(pred.get('spy_verify_1h') or pred.get('spy_verify_3h') or 0)
        pm_move = abs(avg_pm_change)

        if pm_move >= PM_EVENT or spy_move >= SPY_EVENT:
            event_level = 'EVENT'       # 大事！一年只有十幾天
            learn_weight = EVENT_WEIGHT
        elif pm_move >= PM_NOTABLE or spy_move >= SPY_NOTABLE:
            event_level = 'NOTABLE'     # 有感波動
            learn_weight = NOTABLE_WEIGHT
        else:
            event_level = 'NOISE'       # 噪音
            learn_weight = NOISE_WEIGHT

        pred['event_level'] = event_level
        pred['learn_weight'] = learn_weight
        pred['pm_move'] = round(pm_move, 4)
        pred['spy_move'] = round(spy_move, 3)

        # 持續追蹤：6h / 12h / 24h / 48h（川普效應最長好幾天）
        if hours_elapsed >= 6 and pred.get('pm_verify_6h') is None:
            pred['pm_verify_6h'] = round(avg_pm_change, 4)
            if hours_elapsed >= 6 and coin_price_now and pred.get('trump_coin_verify_6h') is None:
                pred['trump_coin_verify_6h'] = coin_price_now

        if hours_elapsed >= 12 and pred.get('pm_verify_12h') is None:
            pred['pm_verify_12h'] = round(avg_pm_change, 4)
            spy_12h = pred.get('spy_verify_1h')  # 用最新的 spy 數據
            if spy_at and stock_now.get('spy_price'):
                spy_12h_change = (stock_now['spy_price'] - spy_at) / spy_at * 100
                pred['spy_verify_12h'] = round(spy_12h_change, 3)

        if hours_elapsed >= 24 and pred.get('pm_verify_24h') is None:
            pred['pm_verify_24h'] = round(avg_pm_change, 4)
            if spy_at and stock_now.get('spy_price'):
                spy_24h_change = (stock_now['spy_price'] - spy_at) / spy_at * 100
                pred['spy_verify_24h'] = round(spy_24h_change, 3)
            log(f"   📊 24h追蹤: {pred['post_preview'][:40]}... PM {avg_pm_change:+.4f}")

        if hours_elapsed >= 48 and pred.get('pm_verify_48h') is None:
            pred['pm_verify_48h'] = round(avg_pm_change, 4)
            if spy_at and stock_now.get('spy_price'):
                spy_48h_change = (stock_now['spy_price'] - spy_at) / spy_at * 100
                pred['spy_verify_48h'] = round(spy_48h_change, 3)

        # 48h 後才結案（不是 6h 就結案）
        if hours_elapsed >= 48:
            if event_level == 'NOISE':
                pred['status'] = 'NOISE'
            else:
                pred['status'] = 'VERIFIED'
                verified_count += 1
                if pred.get('pm_correct_1h'):
                    correct_1h += 1
                if pred.get('pm_correct_3h'):
                    correct_3h += 1

                if event_level == 'EVENT':
                    log(f"   🔴 大事！{pred['post_preview'][:50]}...")
                    log(f"      PM {avg_pm_change:+.2%} | SPY {spy_move:+.2f}% | "
                        f"預測 {'✅' if pred.get('pm_correct_3h') else '❌'}")
        elif hours_elapsed >= 72:
            # 超過 72 小時還是 LIVE 的，強制結案
            pred['status'] = 'EXPIRED'

    # 存檔
    with open(RT_PREDICTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)

    # 學習：累積統計
    all_verified = [p for p in predictions if p.get('status') == 'VERIFIED']
    all_events = [p for p in all_verified if p.get('event_level') == 'EVENT']
    all_notable = [p for p in all_verified if p.get('event_level') == 'NOTABLE']
    all_noise = [p for p in predictions if p.get('status') == 'NOISE']
    if all_verified:
        total = len(all_verified)
        log(f"   📊 大事: {len(all_events)} | 有感: {len(all_notable)} | 噪音: {len(all_noise)}（忽略）")
        c1 = sum(1 for p in all_verified if p.get('direction_correct_1h'))
        c3 = sum(1 for p in all_verified if p.get('direction_correct_3h'))

        # 美股命中率
        spy_c1 = sum(1 for p in all_verified if p.get('spy_correct_1h'))
        spy_c3 = sum(1 for p in all_verified if p.get('spy_correct_3h'))
        divergences = sum(1 for p in all_verified if p.get('pm_vs_stock_divergence'))

        # 大事的命中率（最重要！）
        event_c1 = sum(1 for p in all_events if p.get('pm_correct_1h'))
        event_c3 = sum(1 for p in all_events if p.get('pm_correct_3h'))

        learning = {
            'updated_at': now_str(),
            'total_verified': total,
            'total_events': len(all_events),
            'total_notable': len(all_notable),
            'total_noise': len(all_noise),

            # 大事的命中率（最重要！系統的真正實力）
            'event_pm_hit_1h': round(event_c1 / len(all_events) * 100, 1) if all_events else 0,
            'event_pm_hit_3h': round(event_c3 / len(all_events) * 100, 1) if all_events else 0,

            # 全部（含有感）的命中率
            'all_pm_hit_1h': round(c1 / total * 100, 1),
            'all_pm_hit_3h': round(c3 / total * 100, 1),

            # 美股命中率
            'spy_hit_rate_1h': round(spy_c1 / total * 100, 1),
            'spy_hit_rate_3h': round(spy_c3 / total * 100, 1),

            # 雙軌比較
            'divergence_count': divergences,
            'divergence_rate': round(divergences / total * 100, 1),
            'insight': (
                'PM 和美股反應一致' if divergences < total * 0.2
                else 'PM 和美股經常反應不同 — 有套利空間'
            ),

            'by_signal': _stats_by_signal(all_verified),
        }

        if all_events:
            log(f"   🔴 大事命中率: PM {learning['event_pm_hit_1h']:.0f}%(1h) "
                f"{learning['event_pm_hit_3h']:.0f}%(3h) | {len(all_events)} 筆")

        with open(RT_LEARNING_FILE, 'w', encoding='utf-8') as f:
            json.dump(learning, f, ensure_ascii=False, indent=2)

        log(f"📊 即時預測統計: {total} 筆驗證 | "
            f"1h命中 {learning['hit_rate_1h']:.1f}% | "
            f"3h命中 {learning['hit_rate_3h']:.1f}%")

    return {
        'checked': len(live),
        'newly_verified': verified_count,
        'correct_1h': correct_1h,
        'correct_3h': correct_3h,
    }


def _stats_by_signal(verified: list[dict]) -> dict:
    """
    按信號類型統計即時預測的命中率。
    只統計 VERIFIED（重大波動），不含 NOISE。
    """
    stats: dict[str, dict] = defaultdict(lambda: {
        'total': 0,
        'pm_correct_1h': 0, 'pm_correct_3h': 0,
        'spy_correct_1h': 0, 'spy_correct_3h': 0,
        'divergences': 0,
    })
    for p in verified:
        for sig_type in p.get('signal_types', []):
            stats[sig_type]['total'] += 1
            if p.get('pm_correct_1h'):
                stats[sig_type]['pm_correct_1h'] += 1
            if p.get('pm_correct_3h'):
                stats[sig_type]['pm_correct_3h'] += 1
            if p.get('spy_correct_1h'):
                stats[sig_type]['spy_correct_1h'] += 1
            if p.get('spy_correct_3h'):
                stats[sig_type]['spy_correct_3h'] += 1
            if p.get('pm_vs_stock_divergence'):
                stats[sig_type]['divergences'] += 1

    return {
        sig: {
            'total': s['total'],
            'pm_hit_1h': round(s['pm_correct_1h'] / s['total'] * 100, 1) if s['total'] > 0 else 0,
            'pm_hit_3h': round(s['pm_correct_3h'] / s['total'] * 100, 1) if s['total'] > 0 else 0,
            'spy_hit_1h': round(s['spy_correct_1h'] / s['total'] * 100, 1) if s['total'] > 0 else 0,
            'spy_hit_3h': round(s['spy_correct_3h'] / s['total'] * 100, 1) if s['total'] > 0 else 0,
            'divergence_rate': round(s['divergences'] / s['total'] * 100, 1) if s['total'] > 0 else 0,
        }
        for sig, s in sorted(stats.items())
    }


# =====================================================================
# ⑥ 主循環
# =====================================================================

def run_once() -> dict[str, Any]:
    """跑一次完整的即時循環。"""
    result = {'new_posts': 0, 'predictions_made': 0, 'verified': 0}

    # 1. 偵測新推文
    posts = fetch_latest_posts(limit=20)
    new_posts = get_new_posts(posts)

    if new_posts:
        log(f"🆕 偵測到 {len(new_posts)} 篇新推文！")
        result['new_posts'] = len(new_posts)

        # 1.5 將新推文寫入 trump_posts_all.json（讓前端即時顯示）
        try:
            merged = _merge_into_posts_all(new_posts)
            result['merged_to_all'] = merged
        except Exception as e:
            log(f"   ⚠️ 合併到 trump_posts_all.json 失敗（不影響預測）: {e}")

        # 2. 同時快照 PM 價格 + 美股
        pm_snapshot = snapshot_pm_prices()
        stock_snapshot = snapshot_sp500()
        coin_snapshot = snapshot_trump_coin()
        if coin_snapshot.get('price'):
            log(f"   🪙 $TRUMP: ${coin_snapshot['price']:.2f} ({coin_snapshot.get('change_24h', 0):+.1f}%)")
        if stock_snapshot.get('spy_price'):
            log(f"   📈 SPY: ${stock_snapshot['spy_price']} ({stock_snapshot.get('spy_change_pct', 0):+.2f}%)"
                f" | ES: ${stock_snapshot.get('es_futures', '?')}"
                f" | VIX: {stock_snapshot.get('vix', '?')} ({stock_snapshot.get('vix_level', '?')})")


        # 3. 對每篇新推文做即時預測
        predictions: list[dict] = []
        if RT_PREDICTIONS_FILE.exists():
            with open(RT_PREDICTIONS_FILE, encoding='utf-8') as f:
                predictions = json.load(f)

        for post in new_posts:
            log(f"   📝 [{post['created_at'][11:16]}] {post['content'][:80]}...")

            signals = classify_post(post['content'])
            if signals:
                sig_str = ', '.join(f"{s['type']}({s['confidence']:.0%})" for s in signals)
                log(f"      信號: {sig_str}")

                pred = make_prediction(post, signals, pm_snapshot, stock_snapshot, coin_snapshot)
                if pred:
                    predictions.append(pred)
                    result['predictions_made'] += 1
                    log(f"      預測: PM {pred['predicted_direction']} "
                        f"({pred['confidence']:.0%}) | 追蹤 {len(pred['tracked_markets'])} 個市場")

        # 保留最近 500 筆
        predictions = predictions[-500:]
        with open(RT_PREDICTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)

    else:
        log("   沒有新推文")

    # 3.5 劇本偵測（避險/佈局/拉盤）
    if new_posts:
        try:
            from pathlib import Path as _P
            pb_file = _P(__file__).parent / "data" / "trump_playbook.json"
            if pb_file.exists():
                import json as _json
                with open(pb_file, encoding='utf-8') as _f:
                    playbook = _json.load(_f)

                # 從今天所有推文的信號判斷屬於哪個劇本
                day_signals = set()
                for post in new_posts:
                    for sig in classify_post(post['content']):
                        sig_type = sig['type']
                        if sig_type == 'TARIFF': day_signals.add('T')
                        elif sig_type == 'DEAL': day_signals.add('D')
                        elif sig_type in ('BULLISH',): day_signals.add('M')
                        elif sig_type == 'RELIEF': day_signals.add('R')
                        elif sig_type in ('BEARISH', 'THREAT'): day_signals.add('A')

                combo = '+'.join(sorted(day_signals))

                # 匹配劇本
                for section_key in ['hedge_signals', 'position_signals', 'pump_signals']:
                    section = playbook.get(section_key, {})
                    for rule in section.get('rules', []):
                        if rule['pattern'] == combo or (combo == '' and rule['pattern'] == 'NONE'):
                            icon = {'hedge_signals': '🛡️避險', 'position_signals': '📊佈局', 'pump_signals': '🚀拉盤'}.get(section_key, '?')
                            log(f"   {icon} 劇本觸發: {rule['label']}")
                            log(f"      歷史: {rule.get('avg_return', 0):+.3f}% | 建議: {rule.get('action', '?')}")
                            result['playbook'] = {'type': section_key, 'rule': rule}
                            break
        except Exception:
            pass

    # 4. 事件醞釀偵測（看前幾天的模式）
    try:
        from event_detector import detect_events
        event_alerts = detect_events()
        if event_alerts:
            for alert in event_alerts:
                icon = '🔴' if alert['severity'] == 'HIGH' else '🟡'
                log(f"   {icon} 醞釀偵測: {alert['name']} → {alert['expected_direction']}")
            result['event_alerts'] = event_alerts
    except ImportError:
        pass
    except Exception as e:
        log(f"   事件偵測失敗: {e}")

    # 2.5 保存 $TRUMP 幣價歷史（每輪都跑，不管有沒有新推文）
    coin_snapshot = snapshot_trump_coin()
    if coin_snapshot.get('price'):
        log(f"   🪙 $TRUMP: ${coin_snapshot['price']:.2f} ({coin_snapshot.get('change_24h', 0):+.1f}%)")
        try:
            coin_hist_file = DATA / "trump_coin_history.json"
            coin_hist = []
            if coin_hist_file.exists():
                with open(coin_hist_file, encoding="utf-8") as _f:
                    coin_hist = json.load(_f)
            should_save = True
            if coin_hist:
                last_ts = coin_hist[-1].get("timestamp", "")
                if last_ts[:13] == coin_snapshot.get("timestamp", "")[:13]:
                    should_save = False
            if should_save:
                coin_hist.append({
                    "price": coin_snapshot["price"],
                    "change_24h": coin_snapshot.get("change_24h", 0),
                    "market_cap": coin_snapshot.get("market_cap", 0),
                    "timestamp": coin_snapshot.get("timestamp", now_str()),
                    "date": now_str()[:10],
                })
                coin_hist = coin_hist[-720:]
                with open(coin_hist_file, "w", encoding="utf-8") as _f:
                    json.dump(coin_hist, _f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"   ⚠️ 幣價歷史存檔失敗: {e}")

    # 2.6 保存 Polymarket 快照（每輪都跑）
    pm_snapshot = snapshot_pm_prices()
    if pm_snapshot and pm_snapshot.get('markets'):
        try:
            pm_file = DATA / "polymarket_live.json"
            pm_snapshot["updated"] = now_str()
            pm_snapshot["total"] = len(pm_snapshot.get("markets", []))
            with open(pm_file, "w", encoding="utf-8") as _f:
                json.dump(pm_snapshot, _f, ensure_ascii=False, indent=2)
            log(f"   📊 Polymarket: {pm_snapshot['total']} 個市場已更新")
        except Exception as e:
            log(f"   ⚠️ Polymarket 快照存檔失敗: {e}")

    # 5. 驗證過去的預測
    verify_result = verify_predictions()
    result['verified'] = verify_result.get('newly_verified', 0)

    return result


def run_loop():
    """持續監控，每 5 分鐘跑一次。"""
    log("=" * 60)
    log("🔴 川普密碼 即時閉環引擎")
    log(f"   監控間隔: {POLL_INTERVAL} 秒")
    log("=" * 60)

    while True:
        try:
            result = run_once()
            if result['new_posts'] or result['verified']:
                log(f"📊 本輪: {result['new_posts']} 新推文 | "
                    f"{result['predictions_made']} 預測 | "
                    f"{result['verified']} 驗證")
        except KeyboardInterrupt:
            log("停止。")
            break
        except Exception as e:
            log(f"⚠️ 錯誤: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--once':
        run_once()
    elif len(sys.argv) > 1 and sys.argv[1] == '--verify':
        verify_predictions()
    else:
        run_loop()
