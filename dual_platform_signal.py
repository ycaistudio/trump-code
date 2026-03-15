#!/usr/bin/env python3
"""
川普密碼 — 雙平台信號引擎（Truth Social vs X）

另一個終端機的重大發現：
  Truth Social = 政策信號源（預測用）
  X = 形象窗口（確認用）
  他先在 TS 說，6 小時後才放到 X。
  這 6 小時就是操作窗口。

五大密碼：
  ① 中國 = 完全隱藏（203 篇提中國，0 篇放 X）→ 中國信號加權
  ② TS 先發，X 晚 6.2 小時 → 偵測到跨平台就開始倒計時
  ③ 時間差窗口內市場偏漲 63% → 窗口 = 做多機會
  ④ X 推文隔天報酬 +0.252%（TS-only 的 7 倍）→ X 出現 = 確認信號
  ⑤ X 使用率和市場正相關 0.35 → 他心情好才用 X

操作邏輯：
  TS 出現政策推文 → 開始 6 小時倒計時
  如果 6 小時內 X 也出現 → 確認！→ 當天做多（63% 偏漲）
  如果 X 沒出現 → 他不想讓大眾知道 → 可能是負面的
  如果是中國相關 → 絕對不會上 X → TS-only 信號加權
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent
DATA = BASE / "data"
NOW = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
TODAY = datetime.now(timezone.utc).strftime('%Y-%m-%d')

DUAL_SIGNAL_FILE = DATA / "dual_platform_signals.json"
WINDOW_TRACKING_FILE = DATA / "ts_to_x_windows.json"


def log(msg: str) -> None:
    print(f"[雙平台] {msg}", flush=True)


# =====================================================================
# 研究發現（硬編碼為常數，定期用新數據更新）
# =====================================================================

# 來自交叉比對分析的統計
STATS = {
    # TS→X 時間差
    'avg_ts_to_x_hours': 6.2,       # 平均延遲
    'ts_first_rate': 0.974,          # 38/39 篇 TS 先發

    # X 發文日 vs 沒發文日
    'x_day_up_rate': 0.638,          # X 有發文日上漲率 63.8%
    'no_x_day_up_rate': 0.498,       # X 沒發文日上漲率 49.8%
    'x_day_return': 0.109,           # X 有發文日當天報酬 +0.109%
    'x_next_day_return': -0.087,     # X 有發文日隔天報酬 -0.087%（回吐）

    # X 推文的特徵
    'x_avg_length': 261,             # X 推文較短
    'ts_avg_length': 378,            # TS 推文較長
    'x_media_ratio': 3.5,            # X 含媒體的比例是 TS 的 3.5 倍

    # 中國密碼
    'china_on_x': 0,                 # 中國相關推文在 X 的數量
    'china_on_ts': 203,              # 中國相關推文在 TS 的數量
    'china_x_rate': 0.0,             # 0%！完全不放 X

    # 相關性
    'x_usage_market_corr': 0.35,     # X 使用率和市場正相關
}

# 不會放到 X 的內容（放 X 的機率低）
TS_ONLY_SIGNALS = [
    'china', 'chinese', 'beijing', 'xi jinping',  # 中國 100% 不放 X
    'policy detail',  # 政策細節 -0.52x
    '?',              # 問句 -0.75x
]

# 會放到 X 的內容（放 X 的機率高）
X_LIKELY_SIGNALS = [
    'maga', 'america first', 'great',  # 形象/口號類
    'video', 'watch',                   # 影片類 +3.5x
]


# =====================================================================
# ① 分類推文來源
# =====================================================================

def classify_platform_intent(post: dict) -> dict[str, Any]:
    """
    判斷一篇推文是 TS-only 還是可能會上 X。
    如果是 TS-only（尤其中國相關），信號加權。
    """
    content = post.get('content', '')
    cl = content.lower()

    result = {
        'platform': 'truth_social',  # 我們的數據源都是 TS
        'likely_x_repost': False,     # 會不會被轉到 X
        'china_signal': False,        # 是不是中國相關
        'ts_only_boost': 1.0,         # 信號加權倍數
        'window_active': False,       # 6 小時窗口是否啟動
        'reasoning': '',
    }

    # 中國相關 → TS-only，加權
    china_keywords = ['china', 'chinese', 'beijing', 'xi jinping', 'xi ', 'ccp', 'prc']
    if any(kw in cl for kw in china_keywords):
        result['china_signal'] = True
        result['likely_x_repost'] = False
        result['ts_only_boost'] = 1.5  # 中國信號加權 1.5 倍（因為他刻意隱藏）
        result['reasoning'] = '中國相關：203 篇 TS，0 篇 X — 刻意隱藏，信號更真實'
        return result

    # 問句或政策細節 → 不太會放 X
    if '?' in content and len(content) > 200:
        result['likely_x_repost'] = False
        result['ts_only_boost'] = 1.2
        result['reasoning'] = '含問句 + 長文 → 政策討論型，不太會放 X'
        return result

    # 短文 + 大宣示 + 影片 → 很可能放 X
    is_short = len(content) < 300
    has_media_hint = any(w in cl for w in ['http', 'video', 'watch', '📺', '🎬'])
    has_maga = any(w in cl for w in ['maga', 'america first', 'great again', 'golden age'])

    if is_short and (has_media_hint or has_maga):
        result['likely_x_repost'] = True
        result['ts_only_boost'] = 0.8  # 會上 X 的信號降權（大眾已知）
        result['window_active'] = True  # 啟動 6 小時倒計時
        result['reasoning'] = '短文+宣示/影片 → 很可能 6 小時後轉到 X'
        return result

    # 預設：中等長度，不確定
    result['likely_x_repost'] = None  # 不確定
    result['ts_only_boost'] = 1.0
    result['reasoning'] = '中等長度，無法判斷是否會放 X'
    return result


# =====================================================================
# ② 雙平台信號加權
# =====================================================================

def apply_dual_platform_weights(
    signals: list[dict],
    post: dict,
) -> list[dict]:
    """
    根據雙平台分析，調整信號的信心度。

    規則：
      - 中國相關 → 信心度 ×1.5（他刻意隱藏的信號更真實）
      - 會上 X 的 → 信心度 ×0.8（大眾已知，套利空間小）
      - TS-only 政策細節 → 信心度 ×1.2（內部信號）
    """
    platform = classify_platform_intent(post)

    boost = platform['ts_only_boost']
    for sig in signals:
        sig['original_confidence'] = sig.get('confidence', 0.5)
        sig['confidence'] = min(0.95, sig['confidence'] * boost)
        sig['platform_analysis'] = {
            'china_signal': platform['china_signal'],
            'likely_x_repost': platform['likely_x_repost'],
            'boost_applied': boost,
            'reasoning': platform['reasoning'],
        }

    return signals


# =====================================================================
# ③ 6 小時窗口追蹤
# =====================================================================

def start_window(post: dict, signals: list[dict]) -> dict | None:
    """
    如果推文可能會被轉到 X，開始 6 小時倒計時。
    窗口內市場偏漲 63%。
    """
    platform = classify_platform_intent(post)

    if not platform['window_active']:
        return None

    window = {
        'post_time': post['created_at'],
        'post_preview': post['content'][:200],
        'signals': [s['type'] for s in signals if isinstance(s, dict)],
        'window_start': NOW,
        'window_end_est': (
            datetime.now(timezone.utc) + timedelta(hours=6.2)
        ).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'status': 'ACTIVE',     # ACTIVE → CONFIRMED → EXPIRED
        'x_appeared': False,
        'market_at_start': None,  # 稍後填入
        'market_at_end': None,
    }

    # 存入追蹤
    windows: list[dict] = []
    if WINDOW_TRACKING_FILE.exists():
        with open(WINDOW_TRACKING_FILE, encoding='utf-8') as f:
            windows = json.load(f)

    windows.append(window)
    windows = windows[-50:]

    with open(WINDOW_TRACKING_FILE, 'w', encoding='utf-8') as f:
        json.dump(windows, f, ensure_ascii=False, indent=2)

    log(f"   ⏱️ 6 小時窗口啟動！")
    log(f"      預計 X 轉貼時間: {window['window_end_est'][:16]}")
    log(f"      窗口內歷史上漲率: 63%")

    return window


# =====================================================================
# ④ 整合到即時引擎
# =====================================================================

def enhance_realtime_prediction(
    post: dict,
    signals: list[dict],
) -> dict[str, Any]:
    """
    被 realtime_loop 呼叫。
    把雙平台智慧加到即時預測中。

    回傳增強後的元數據。
    """
    # 加權信號
    enhanced = apply_dual_platform_weights(signals, post)

    # 平台分析
    platform = classify_platform_intent(post)

    # 窗口追蹤
    window = start_window(post, signals)

    result = {
        'platform_analysis': platform,
        'enhanced_signals': enhanced,
        'window': window,
        'china_boost_applied': platform['china_signal'],
        'ts_only': not platform['likely_x_repost'],
    }

    # 特殊日誌
    if platform['china_signal']:
        log(f"   🇨🇳 中國信號偵測！信心度 ×1.5（203 篇 TS / 0 篇 X — 刻意隱藏）")
    if window:
        log(f"   ⏱️ 可能 6h 後轉 X → 窗口內做多（歷史 63% 偏漲）")

    return result


# =====================================================================
# CLI
# =====================================================================

if __name__ == '__main__':
    import sys

    # Demo
    demo_posts = [
        {
            'created_at': '2026-03-15T06:30:00Z',
            'content': 'China has been taking advantage of the United States for decades. '
                       'We are imposing RECIPROCAL TARIFFS. Fair is fair!',
        },
        {
            'created_at': '2026-03-15T07:00:00Z',
            'content': 'GREAT new video showing the incredible progress of our MAGA movement! '
                       'Watch here: https://truthsocial.com/...',
        },
        {
            'created_at': '2026-03-15T08:00:00Z',
            'content': 'The question is whether the Federal Reserve will do the right thing '
                       'and lower interest rates, which they should have done a long time ago? '
                       'Our economy is strong but could be MUCH stronger with proper monetary policy.',
        },
    ]

    print("=== 雙平台信號分析 Demo ===\n")
    for i, post in enumerate(demo_posts, 1):
        print(f"--- 推文 {i} ---")
        print(f"  {post['content'][:80]}...")
        platform = classify_platform_intent(post)
        print(f"  中國信號: {'🇨🇳 是' if platform['china_signal'] else '否'}")
        print(f"  可能上 X: {'是' if platform['likely_x_repost'] else ('否' if platform['likely_x_repost'] is False else '不確定')}")
        print(f"  信號加權: ×{platform['ts_only_boost']}")
        print(f"  6h 窗口: {'啟動' if platform['window_active'] else '否'}")
        print(f"  理由: {platform['reasoning']}")
        print()
