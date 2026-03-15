#!/usr/bin/env python3
"""
川普密碼 — 斷路器（Circuit Breaker）

如果我們全部都想錯了，怎麼辦？
這個模組就是保險——自動偵測「整個系統壞了」的情況。

三道防線：

  第 1 道：我們真的比隨機好嗎？
    → 跟「丟銅板 50/50」比，沒有顯著好 → 系統可能在學噪音

  第 2 道：最近有沒有在惡化？
    → 最近 2 週的命中率 vs 歷史 → 如果大幅下滑 → 模式可能變了

  第 3 道：連續錯太多次 → 自動停機
    → 連錯 N 次 → 停止預測 → 發警報 → 等人來看

還有一個反向思維：
  如果連續錯，也許反著做才對。
  系統會自動偵測「反向信號」— 如果一直錯，也許我們的信號是反指標。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections import defaultdict
import math

BASE = Path(__file__).parent
DATA = BASE / "data"
NOW = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
TODAY = datetime.now(timezone.utc).strftime('%Y-%m-%d')

BREAKER_STATE_FILE = DATA / "circuit_breaker_state.json"
BREAKER_LOG_FILE = DATA / "circuit_breaker_log.json"


def log(msg: str) -> None:
    print(f"[斷路器] {msg}", flush=True)


# =====================================================================
# 參數
# =====================================================================

# 第 1 道：隨機基準線
RANDOM_BASELINE = 50.0           # 丟銅板的命中率 %
SIGNIFICANCE_MIN_SAMPLES = 20   # 至少幾筆才比
SIGNIFICANCE_THRESHOLD = 5.0    # 要贏隨機至少 5% 才算有效

# 第 2 道：惡化偵測
RECENT_WINDOW = 14              # 最近幾天
DEGRADATION_THRESHOLD = -10.0   # 最近 vs 歷史差超過 -10% = 惡化

# 第 3 道：連錯停機
CONSECUTIVE_WRONG_LIMIT = 8     # 連錯幾次 → 停機
SYSTEM_PAUSE_HOURS = 24         # 停機幾小時

# 反向信號
INVERSE_THRESHOLD = 7           # 連錯幾次 → 嘗試反向
INVERSE_MIN_SAMPLES = 10        # 反向模式至少驗證幾筆


# =====================================================================
# 第 1 道：我們真的比隨機好嗎？
# =====================================================================

def check_vs_random(predictions: list[dict]) -> dict[str, Any]:
    """
    跟丟銅板比。如果沒有顯著好，我們可能在學噪音。

    用二項檢定（簡化版）：
    如果 N 筆中 K 筆正確，K/N 跟 50% 沒有顯著差異 → 跟隨機一樣。
    """
    verified = [p for p in predictions if p.get('status') == 'VERIFIED']

    if len(verified) < SIGNIFICANCE_MIN_SAMPLES:
        return {
            'check': 'vs_random',
            'status': 'INSUFFICIENT_DATA',
            'message': f'只有 {len(verified)} 筆驗證（需 {SIGNIFICANCE_MIN_SAMPLES}）',
        }

    correct = sum(1 for p in verified if p.get('correct'))
    total = len(verified)
    hit_rate = correct / total * 100
    edge = hit_rate - RANDOM_BASELINE

    # 簡化的統計顯著性（z-test）
    p0 = RANDOM_BASELINE / 100
    se = math.sqrt(p0 * (1 - p0) / total)
    z = (correct / total - p0) / se if se > 0 else 0
    significant = z > 1.96  # p < 0.05

    result = {
        'check': 'vs_random',
        'total': total,
        'correct': correct,
        'hit_rate': round(hit_rate, 1),
        'edge_vs_random': round(edge, 1),
        'z_score': round(z, 2),
        'statistically_significant': significant,
    }

    if not significant:
        result['status'] = '🔴 WARNING'
        result['message'] = (
            f'命中率 {hit_rate:.1f}% 跟隨機 50% 沒有統計顯著差異（z={z:.2f}）。'
            f'我們可能在學噪音，不是真的預測到了什麼。'
        )
    elif edge < SIGNIFICANCE_THRESHOLD:
        result['status'] = '🟡 MARGINAL'
        result['message'] = (
            f'命中率 {hit_rate:.1f}% 比隨機好 {edge:.1f}%，'
            f'統計顯著但優勢很小。謹慎使用。'
        )
    else:
        result['status'] = '✅ OK'
        result['message'] = (
            f'命中率 {hit_rate:.1f}% 比隨機好 {edge:.1f}%（z={z:.2f}，p<0.05）。'
            f'系統有真正的預測能力。'
        )

    return result


# =====================================================================
# 第 2 道：最近有沒有在惡化？
# =====================================================================

def check_degradation(predictions: list[dict]) -> dict[str, Any]:
    """
    最近 2 週 vs 歷史全部。如果大幅下滑，模式可能變了。
    """
    verified = [p for p in predictions if p.get('status') == 'VERIFIED']
    if len(verified) < 20:
        return {'check': 'degradation', 'status': 'INSUFFICIENT_DATA'}

    # 按日期排序
    verified.sort(key=lambda p: p.get('date_signal', '') or p.get('signal_date', ''))

    # 全部的命中率
    all_correct = sum(1 for p in verified if p.get('correct'))
    all_rate = all_correct / len(verified) * 100

    # 最近 N 天
    recent = verified[-RECENT_WINDOW:]
    recent_correct = sum(1 for p in recent if p.get('correct'))
    recent_rate = recent_correct / len(recent) * 100 if recent else 0

    # 差異
    degradation = recent_rate - all_rate

    result = {
        'check': 'degradation',
        'all_hit_rate': round(all_rate, 1),
        'recent_hit_rate': round(recent_rate, 1),
        'degradation': round(degradation, 1),
        'recent_window': len(recent),
    }

    if degradation <= DEGRADATION_THRESHOLD:
        result['status'] = '🔴 DEGRADING'
        result['message'] = (
            f'最近 {len(recent)} 筆命中率 {recent_rate:.0f}% '
            f'比歷史 {all_rate:.0f}% 低了 {abs(degradation):.0f}%。'
            f'Trump 的模式可能已經改變。'
        )
    elif degradation <= -5:
        result['status'] = '🟡 DECLINING'
        result['message'] = (
            f'最近 {len(recent)} 筆命中率 {recent_rate:.0f}% '
            f'略低於歷史 {all_rate:.0f}%。持續觀察。'
        )
    else:
        result['status'] = '✅ STABLE'
        result['message'] = f'最近表現穩定，跟歷史一致。'

    return result


# =====================================================================
# 第 3 道：連錯停機
# =====================================================================

def check_consecutive_errors(predictions: list[dict]) -> dict[str, Any]:
    """
    連續錯太多次 → 停機。
    也偵測「反向信號」— 如果一直錯，也許反著做才對。
    """
    verified = [p for p in predictions if p.get('status') == 'VERIFIED']
    if not verified:
        return {'check': 'consecutive', 'status': 'NO_DATA'}

    verified.sort(key=lambda p: p.get('date_signal', '') or p.get('signal_date', ''))

    # 從最新往回數連錯次數
    consecutive_wrong = 0
    for p in reversed(verified):
        if not p.get('correct'):
            consecutive_wrong += 1
        else:
            break

    # 反向偵測：最近 N 筆的反向命中率
    recent = verified[-INVERSE_MIN_SAMPLES:]
    inverse_correct = sum(1 for p in recent if not p.get('correct'))  # 錯的 = 反向對的
    inverse_rate = inverse_correct / len(recent) * 100 if recent else 0

    result = {
        'check': 'consecutive',
        'consecutive_wrong': consecutive_wrong,
        'inverse_hit_rate': round(inverse_rate, 1),
    }

    if consecutive_wrong >= CONSECUTIVE_WRONG_LIMIT:
        result['status'] = '🔴 CIRCUIT_BREAK'
        result['message'] = (
            f'連錯 {consecutive_wrong} 次！系統自動停機 {SYSTEM_PAUSE_HOURS} 小時。'
            f'需要人工檢查：是模式變了？還是代碼有 bug？'
        )
        result['action'] = 'PAUSE'
    elif consecutive_wrong >= INVERSE_THRESHOLD:
        result['status'] = '🟡 CONSIDER_INVERSE'
        result['message'] = (
            f'連錯 {consecutive_wrong} 次。反向命中率 {inverse_rate:.0f}%。'
            f'也許我們的信號是反指標？考慮反向操作。'
        )
        if inverse_rate > 60:
            result['action'] = 'INVERSE_SUGGESTED'
            result['message'] += f' 反向命中率 {inverse_rate:.0f}% > 60%，建議嘗試反向。'
    else:
        result['status'] = '✅ OK'
        result['message'] = f'連錯 {consecutive_wrong} 次，在正常範圍內。'

    return result


# =====================================================================
# 主檢查
# =====================================================================

def run_circuit_breaker() -> dict[str, Any]:
    """
    跑完整的斷路器檢查。
    回傳系統狀態和是否應該停機。
    """
    log("=" * 60)
    log(f"斷路器檢查 — {TODAY}")
    log("=" * 60)

    # 載入預測數據
    pred_file = DATA / "predictions_log.json"
    if not pred_file.exists():
        log("⚠️ 無預測數據")
        return {'status': 'NO_DATA'}

    with open(pred_file, encoding='utf-8') as f:
        predictions = json.load(f)

    # 三道防線
    random_check = check_vs_random(predictions)
    degrade_check = check_degradation(predictions)
    consec_check = check_consecutive_errors(predictions)

    log(f"\n  第 1 道（vs 隨機）: {random_check['status']}")
    log(f"    {random_check.get('message', '')}")
    log(f"\n  第 2 道（惡化偵測）: {degrade_check['status']}")
    log(f"    {degrade_check.get('message', '')}")
    log(f"\n  第 3 道（連錯停機）: {consec_check['status']}")
    log(f"    {consec_check.get('message', '')}")

    # 第 4 道：從錯誤中學
    failure_learning = learn_from_failures(predictions)
    log(f"\n  第 4 道（從錯誤學）: {failure_learning.get('philosophy', '')}")

    # 綜合判斷
    should_pause = consec_check.get('action') == 'PAUSE'
    should_inverse = consec_check.get('action') == 'INVERSE_SUGGESTED'
    all_wrong = random_check.get('status', '').startswith('🔴')

    overall = {
        'date': TODAY,
        'checked_at': NOW,
        'checks': {
            'vs_random': random_check,
            'degradation': degrade_check,
            'consecutive': consec_check,
            'failure_learning': failure_learning,
        },
        'should_pause': should_pause,
        'should_inverse': should_inverse,
        'all_might_be_wrong': all_wrong,
    }

    if should_pause:
        overall['system_status'] = '🔴 PAUSED'
        overall['action'] = '停止所有預測，等人工檢查'
        log(f"\n  🔴🔴🔴 系統停機！連錯太多次。")
    elif all_wrong:
        overall['system_status'] = '🔴 QUESTIONABLE'
        overall['action'] = '系統可能在學噪音，降低信心度'
        log(f"\n  🔴 系統可能全錯了——跟隨機沒有顯著差異。")
    elif should_inverse:
        overall['system_status'] = '🟡 INVERSE_MODE'
        overall['action'] = '考慮反向操作'
        log(f"\n  🟡 連續錯誤，反向信號可能有效。")
    elif degrade_check.get('status', '').startswith('🔴'):
        overall['system_status'] = '🟡 DEGRADING'
        overall['action'] = '模式可能改變，減少部位'
        log(f"\n  🟡 系統在惡化，可能需要重新訓練。")
    else:
        overall['system_status'] = '✅ OPERATIONAL'
        overall['action'] = '正常運作'
        log(f"\n  ✅ 系統正常。")

    # 存檔
    with open(BREAKER_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(overall, f, ensure_ascii=False, indent=2)

    # 歷史紀錄
    history: list[dict] = []
    if BREAKER_LOG_FILE.exists():
        with open(BREAKER_LOG_FILE, encoding='utf-8') as f:
            history = json.load(f)
    history.append({
        'date': TODAY,
        'status': overall['system_status'],
        'vs_random': random_check.get('status', '?'),
        'degradation': degrade_check.get('status', '?'),
        'consecutive': consec_check.get('status', '?'),
        'consecutive_wrong': consec_check.get('consecutive_wrong', 0),
    })
    history = history[-90:]
    with open(BREAKER_LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    log("=" * 60)
    return overall


# =====================================================================
# 給其他模組用的介面
# =====================================================================

# =====================================================================
# 從錯誤中學（Elimination Learning）
# =====================================================================

def learn_from_failures(predictions: list[dict]) -> dict[str, Any]:
    """
    一直做錯、做錯、做錯 → 對的方向就出來了。

    分析所有「錯」的預測：
      - 哪些信號組合一直錯？→ 這些組合是反指標
      - 哪些時段一直錯？→ 這些時段不要做
      - 哪些方向一直錯？→ 也許該反過來

    錯誤不是失敗，是排除法。排除夠多，剩下的就是答案。
    """
    verified = [p for p in predictions if p.get('status') == 'VERIFIED']
    wrong = [p for p in verified if not p.get('correct')]

    if len(wrong) < 5:
        return {'status': 'insufficient_errors', 'message': '錯得不夠多，還學不到什麼'}

    # 哪些模型一直錯？
    from collections import Counter
    model_errors = Counter()
    model_totals = Counter()
    for p in verified:
        mid = p.get('model_id', '?')
        model_totals[mid] += 1
        if not p.get('correct'):
            model_errors[mid] += 1

    # 找出「反指標」模型（錯超過 55%）
    anti_indicators = []
    for mid, errors in model_errors.items():
        total = model_totals[mid]
        if total >= 8 and errors / total > 0.55:
            anti_indicators.append({
                'model': mid,
                'error_rate': round(errors / total * 100, 1),
                'total': total,
                'suggestion': f'反著做可能命中率 {errors/total*100:.0f}%',
            })

    # 哪些信號組合一直錯？
    signal_errors = Counter()
    signal_totals = Counter()
    for p in verified:
        summary = p.get('day_summary', {})
        has_tariff = summary.get('tariff', 0) > 0
        has_deal = summary.get('deal', 0) > 0
        has_relief = summary.get('relief', 0) > 0
        direction = p.get('direction', '?')

        # 組合 key
        combo = []
        if has_tariff: combo.append('TARIFF')
        if has_deal: combo.append('DEAL')
        if has_relief: combo.append('RELIEF')
        combo_key = f"{'+'.join(combo) if combo else 'NONE'}→{direction}"

        signal_totals[combo_key] += 1
        if not p.get('correct'):
            signal_errors[combo_key] += 1

    # 找出一直錯的組合
    bad_combos = []
    for combo, errors in signal_errors.items():
        total = signal_totals[combo]
        if total >= 5 and errors / total > 0.55:
            bad_combos.append({
                'combo': combo,
                'error_rate': round(errors / total * 100, 1),
                'total': total,
                'suggestion': '避開這個組合，或反向操作',
            })

    # 總結
    result = {
        'total_errors': len(wrong),
        'total_verified': len(verified),
        'error_rate': round(len(wrong) / len(verified) * 100, 1),
        'anti_indicator_models': anti_indicators,
        'bad_signal_combos': bad_combos,
        'philosophy': (
            '做錯不是失敗，是排除法。'
            f'我們排除了 {len(anti_indicators)} 個反指標模型 '
            f'和 {len(bad_combos)} 個無效信號組合。'
            f'剩下的就是真正有效的。'
        ),
    }

    if anti_indicators:
        log(f"\n   🔄 發現 {len(anti_indicators)} 個反指標模型（錯太多，反著做可能對）：")
        for ai in anti_indicators:
            log(f"      {ai['model']}: 錯 {ai['error_rate']:.0f}% → {ai['suggestion']}")

    if bad_combos:
        log(f"\n   ❌ 發現 {len(bad_combos)} 個無效信號組合：")
        for bc in bad_combos:
            log(f"      {bc['combo']}: 錯 {bc['error_rate']:.0f}% → {bc['suggestion']}")

    return result


def is_system_paused() -> bool:
    """其他模組呼叫：系統是否被斷路器暫停了？"""
    if not BREAKER_STATE_FILE.exists():
        return False
    with open(BREAKER_STATE_FILE, encoding='utf-8') as f:
        state = json.load(f)
    return state.get('should_pause', False)


def get_system_status() -> str:
    """回傳系統狀態字串。"""
    if not BREAKER_STATE_FILE.exists():
        return 'UNKNOWN'
    with open(BREAKER_STATE_FILE, encoding='utf-8') as f:
        state = json.load(f)
    return state.get('system_status', 'UNKNOWN')


if __name__ == '__main__':
    result = run_circuit_breaker()
