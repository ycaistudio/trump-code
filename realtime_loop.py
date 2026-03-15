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
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

ARCHIVE_URL = "https://ix.cnn.io/data/truth-social/truth_archive.csv"
LAST_SEEN_FILE = DATA / "rt_last_seen.txt"
RT_PREDICTIONS_FILE = DATA / "rt_predictions.json"      # 即時預測紀錄
RT_LEARNING_FILE = DATA / "rt_learning.json"             # 即時學習結果
POLL_INTERVAL = 300  # 5 分鐘


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def now_str() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# =====================================================================
# ① 偵測新推文
# =====================================================================

def fetch_latest_posts(limit: int = 20) -> list[dict]:
    """從 CNN Archive 抓最新推文。"""
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
            posts.append({'created_at': created, 'content': content})

        # 按時間排序，取最新的
        posts.sort(key=lambda p: p['created_at'], reverse=True)
        return posts[:limit]

    except Exception as e:
        log(f"⚠️ 抓推文失敗: {e}")
        return []


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


def classify_post(content: str) -> list[dict]:
    """即時分類一篇推文的信號。"""
    cl = content.lower()
    signals = []

    for sig_type, keywords in SIGNAL_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in cl]
        if matched:
            # 信心度根據匹配數量
            confidence = min(0.95, 0.5 + 0.15 * len(matched))
            signals.append({
                'type': sig_type,
                'confidence': round(confidence, 2),
                'matched_keywords': matched,
            })

    # 額外偵測
    caps_ratio = sum(1 for c in content if c.isupper()) / max(sum(1 for c in content if c.isalpha()), 1)
    excl_count = content.count('!')
    if caps_ratio > 0.3 or excl_count > 3:
        # 高度情緒化
        for sig in signals:
            sig['confidence'] = min(0.95, sig['confidence'] + 0.1)

    return signals


# =====================================================================
# ③ 雙快照：Polymarket + 美股，同時抓
# =====================================================================

def snapshot_sp500() -> dict[str, Any]:
    """
    即時抓 S&P 500 價格（用 yfinance 抓 SPY ETF 的最新報價）。
    SPY ≈ S&P 500 的 1/10。
    盤外時段用最後收盤價 + 期貨方向。
    """
    try:
        import yfinance as yf

        # SPY = S&P 500 ETF，流動性最高
        spy = yf.Ticker("SPY")
        info = spy.fast_info

        current = float(info.last_price) if hasattr(info, 'last_price') else None
        prev_close = float(info.previous_close) if hasattr(info, 'previous_close') else None

        if current and prev_close and prev_close > 0:
            change_pct = (current - prev_close) / prev_close * 100
        else:
            change_pct = None

        # 也抓 ES=F（S&P 500 期貨）看盤外方向
        es_price = None
        try:
            es = yf.Ticker("ES=F")
            es_info = es.fast_info
            es_price = float(es_info.last_price) if hasattr(es_info, 'last_price') else None
        except Exception:
            pass

        return {
            'timestamp': now_str(),
            'spy_price': round(current, 2) if current else None,
            'spy_prev_close': round(prev_close, 2) if prev_close else None,
            'spy_change_pct': round(change_pct, 3) if change_pct else None,
            'es_futures': round(es_price, 2) if es_price else None,
            'source': 'yfinance',
        }

    except ImportError:
        return {'error': 'yfinance not installed', 'timestamp': now_str()}
    except Exception as e:
        return {'error': str(e), 'timestamp': now_str()}


def snapshot_pm_prices() -> dict[str, Any]:
    """即時抓 Polymarket 的 Trump 相關市場價格。"""
    try:
        from polymarket_client import fetch_trump_markets, get_market_price, PolymarketAPIError
    except ImportError:
        return {'error': 'polymarket_client not available'}

    try:
        raw = fetch_trump_markets(limit=20)
        market_list = raw.get('data', [])
    except PolymarketAPIError as e:
        return {'error': str(e)}

    snapshot = {
        'timestamp': now_str(),
        'markets': [],
    }

    for market in market_list[:15]:
        question = market.get('question', '?')
        tokens = market.get('tokens', [])

        for token in tokens:
            tid = token.get('token_id', '')
            outcome = token.get('outcome', '')
            price = float(token.get('price', 0.5))

            snapshot['markets'].append({
                'question': question[:100],
                'token_id': tid,
                'outcome': outcome,
                'price': round(price, 4),
            })

    return snapshot


# =====================================================================
# ④ 做出即時預測
# =====================================================================

def make_prediction(
    post: dict,
    signals: list[dict],
    pm_snapshot: dict,
    stock_snapshot: dict | None = None,
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
        'pm_correct_1h': None,
        'pm_correct_3h': None,

        # 美股軌
        'spy_at_signal': stock_snapshot.get('spy_price') if stock_snapshot else None,
        'es_at_signal': stock_snapshot.get('es_futures') if stock_snapshot else None,
        'spy_change_at_signal': stock_snapshot.get('spy_change_pct') if stock_snapshot else None,
        'spy_verify_1h': None,
        'spy_verify_3h': None,
        'spy_correct_1h': None,
        'spy_correct_3h': None,

        # 雙軌比較（驗證後回填）
        'pm_vs_stock_divergence': None,  # PM 和美股反應是否不同
        'divergence_detail': None,       # 具體差異

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

        if hours_elapsed >= 3 and pred.get('pm_verify_3h') is None:
            pred['pm_verify_3h'] = round(avg_pm_change, 4)
            if direction == 'UP':
                pred['pm_correct_3h'] = avg_pm_change > 0
            elif direction == 'DOWN':
                pred['pm_correct_3h'] = avg_pm_change < 0

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

        # 6h 後標為 VERIFIED
        if hours_elapsed >= 6:
            pred['pm_verify_6h'] = round(avg_pm_change, 4)
            pred['status'] = 'VERIFIED'
            verified_count += 1
            if pred.get('pm_correct_1h'):
                correct_1h += 1
            if pred.get('pm_correct_3h'):
                correct_3h += 1

    # 存檔
    with open(RT_PREDICTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)

    # 學習：累積統計
    all_verified = [p for p in predictions if p.get('status') == 'VERIFIED']
    if all_verified:
        total = len(all_verified)
        c1 = sum(1 for p in all_verified if p.get('direction_correct_1h'))
        c3 = sum(1 for p in all_verified if p.get('direction_correct_3h'))

        # 美股命中率
        spy_c1 = sum(1 for p in all_verified if p.get('spy_correct_1h'))
        spy_c3 = sum(1 for p in all_verified if p.get('spy_correct_3h'))
        divergences = sum(1 for p in all_verified if p.get('pm_vs_stock_divergence'))

        learning = {
            'updated_at': now_str(),
            'total_verified': total,

            # 預測市場命中率
            'pm_hit_rate_1h': round(c1 / total * 100, 1),
            'pm_hit_rate_3h': round(c3 / total * 100, 1),

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
    """按信號類型統計即時預測的命中率。"""
    stats: dict[str, dict] = defaultdict(lambda: {'total': 0, 'correct_1h': 0, 'correct_3h': 0})
    for p in verified:
        for sig_type in p.get('signal_types', []):
            stats[sig_type]['total'] += 1
            if p.get('direction_correct_1h'):
                stats[sig_type]['correct_1h'] += 1
            if p.get('direction_correct_3h'):
                stats[sig_type]['correct_3h'] += 1

    return {
        sig: {
            'total': s['total'],
            'hit_1h': round(s['correct_1h'] / s['total'] * 100, 1) if s['total'] > 0 else 0,
            'hit_3h': round(s['correct_3h'] / s['total'] * 100, 1) if s['total'] > 0 else 0,
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

        # 2. 同時快照 PM 價格 + 美股
        pm_snapshot = snapshot_pm_prices()
        stock_snapshot = snapshot_sp500()
        if stock_snapshot.get('spy_price'):
            log(f"   📈 SPY: ${stock_snapshot['spy_price']} ({stock_snapshot.get('spy_change_pct', 0):+.2f}%)")
        if stock_snapshot.get('es_futures'):
            log(f"   📊 ES 期貨: ${stock_snapshot['es_futures']}")

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

                pred = make_prediction(post, signals, pm_snapshot, stock_snapshot)
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

    # 4. 驗證過去的預測
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
