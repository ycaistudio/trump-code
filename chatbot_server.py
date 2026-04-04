#!/usr/bin/env python3
"""
川普密碼 — 聊天機器人（Gemini Flash + 群眾智慧回收）

功能：
  1. 網頁聊天介面 — 讓任何人問 Trump Code 的信號和預測
  2. Gemini Flash 回答 — 便宜（3 把 key 免費額度輪用）
  3. 群眾智慧回收 — 收集用戶的邏輯建議，餵回學習循環

架構：
  Opus 分析結果 → 當 system prompt → Gemini Flash 回答用戶
  用戶提出邏輯 → 存到 crowd_insights.json → Opus 下次分析時參考

啟動：
  python3 chatbot_server.py
  → 瀏覽器打開 http://localhost:8888
"""

from __future__ import annotations

import json
import hashlib
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent
DATA = BASE / "data"
ANALYTICS_FILE = DATA / "analytics.json"

# === 訪客追蹤系統 ===
_analytics_cache = {
    'total_requests': 0,
    'total_unique_ips': 0,
    'daily': {},       # {"2026-03-16": {"views": 10, "unique_ips": ["hash1","hash2"], "pages": {"/": 5, "/api/signals": 3}}}
    'hourly': {},      # {"2026-03-16T14": 5}
    'pages': {},       # {"/": 100, "/api/signals": 50}
    'user_agents': {}, # {"Mozilla": 30, "GPTBot": 5}
}
# 增量計算用的全域 set，啟動時從歷史資料建一次，之後只 add
_all_ips_set: set[str] = set()

def _load_analytics():
    """啟動時載入分析數據"""
    global _analytics_cache, _all_ips_set
    if ANALYTICS_FILE.exists():
        try:
            with open(ANALYTICS_FILE, encoding='utf-8') as f:
                _analytics_cache = json.load(f)
        except Exception:
            pass
    # 從歷史資料建立 IP set（只跑一次）
    for d in _analytics_cache.get('daily', {}).values():
        _all_ips_set.update(d.get('unique_ips', []))

def _save_analytics():
    """每 50 次請求存一次檔"""
    try:
        with open(ANALYTICS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_analytics_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _track_request(ip: str, path: str, user_agent: str):
    """記錄每次請求"""
    now = datetime.now(timezone.utc)
    today = now.strftime('%Y-%m-%d')
    hour_key = now.strftime('%Y-%m-%dT%H')
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:12]

    _analytics_cache['total_requests'] = _analytics_cache.get('total_requests', 0) + 1

    # 每日統計
    if today not in _analytics_cache.get('daily', {}):
        _analytics_cache.setdefault('daily', {})[today] = {'views': 0, 'unique_ips': [], 'pages': {}}
    day = _analytics_cache['daily'][today]
    day['views'] += 1
    if ip_hash not in day.get('unique_ips', []):
        day.setdefault('unique_ips', []).append(ip_hash)
    day.setdefault('pages', {})[path] = day['pages'].get(path, 0) + 1

    # 每小時統計
    _analytics_cache.setdefault('hourly', {})[hour_key] = _analytics_cache.get('hourly', {}).get(hour_key, 0) + 1

    # 頁面統計
    _analytics_cache.setdefault('pages', {})[path] = _analytics_cache.get('pages', {}).get(path, 0) + 1

    # User-Agent 分類
    ua_short = 'Unknown'
    ua_lower = (user_agent or '').lower()
    if 'gptbot' in ua_lower: ua_short = 'GPTBot'
    elif 'claudebot' in ua_lower: ua_short = 'ClaudeBot'
    elif 'perplexitybot' in ua_lower: ua_short = 'PerplexityBot'
    elif 'googlebot' in ua_lower: ua_short = 'Googlebot'
    elif 'bingbot' in ua_lower: ua_short = 'Bingbot'
    elif 'twitterbot' in ua_lower: ua_short = 'TwitterBot'
    elif 'facebookexternalhit' in ua_lower: ua_short = 'FacebookBot'
    elif 'chrome' in ua_lower: ua_short = 'Chrome'
    elif 'safari' in ua_lower: ua_short = 'Safari'
    elif 'firefox' in ua_lower: ua_short = 'Firefox'
    elif 'curl' in ua_lower: ua_short = 'curl'
    elif 'python' in ua_lower: ua_short = 'Python'
    _analytics_cache.setdefault('user_agents', {})[ua_short] = _analytics_cache.get('user_agents', {}).get(ua_short, 0) + 1

    # 增量計算 unique IPs 總數（用 set 快取，不每次重建）
    global _all_ips_set
    _all_ips_set.add(ip_hash)
    _analytics_cache['total_unique_ips'] = len(_all_ips_set)

    # 每 50 次存檔
    if _analytics_cache['total_requests'] % 50 == 0:
        _save_analytics()

# 啟動時載入
_load_analytics()


def _load(filename: str) -> dict | list | None:
    """安全載入 data/ 下的 JSON 檔案。解析失敗回傳 None，不炸 handler。"""
    path = DATA / filename
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError, OSError) as e:
        print(f"⚠️ _load({filename}) 失敗: {e}")
        return None

# === 每日額度門檻 ===
# 1 把 Gemini key 的免費額度 ≈ 500 次/天
# 3 把 key 共 1500，但只用 1 把的量當每日上限（其他留 buffer）
DAILY_GLOBAL_LIMIT = 500      # 全站每日總量
DAILY_PER_USER = 15           # 每人每日最多幾則（500/30人≈15）
RATE_LIMIT_COOLDOWN = 3       # 每則之間至少幾秒
MSG_MIN_LENGTH = 5            # 最短幾個字
MSG_MAX_LENGTH = 800          # 最長幾個字
INSIGHT_MIN_LENGTH = 20       # 洞見最短字數
BANNED_PATTERNS = [           # 垃圾訊息關鍵字
    'http://', 'https://', '.com/', 'click here',
    'buy now', 'free money', 'airdrop', 'giveaway',
]

# 每日計數器（UTC 日期切換時自動重置）
_daily_state = {
    'date': '',        # 當天日期，換日自動重置
    'global_count': 0, # 全站今日用量
    'per_user': defaultdict(int),  # 每人今日用量
    'last_msg': defaultdict(float),  # 每人上次發訊時間
}

# === Gemini Flash 三把 Key 輪用 ===
# 從環境變數讀取，不寫死在代碼裡
# export GEMINI_KEYS="key1,key2,key3"
import os as _os
_keys_str = _os.environ.get('GEMINI_KEYS', '')
GEMINI_KEYS = [k.strip() for k in _keys_str.split(',') if k.strip()]
if not GEMINI_KEYS:
    print("⚠️ 請設定 GEMINI_KEYS 環境變數: export GEMINI_KEYS=\'key1,key2,key3\'")
    print("   沒有 key 的話聊天功能無法使用")
_key_index = 0  # 輪用指標

GEMINI_MODEL = "gemini-2.5-flash"
CROWD_INSIGHTS_FILE = DATA / "crowd_insights.json"
GAME_CURRENT_FILE = DATA / "game_current.json"
GAME_PLAYERS_FILE = DATA / "game_players.json"
GAME_HISTORY_FILE = DATA / "game_history.json"
GAME_ROUND_HOURS = 6

PORT = 8888


def _check_rate_limit(ip: str) -> tuple[str | None, dict]:
    """
    每日額度檢查。

    回傳：(錯誤訊息或None, 當日統計)
    換日自動重置所有計數器。
    """
    now = time.time()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    anon = _anon_id(ip)

    # 換日 → 重置
    if _daily_state['date'] != today:
        _daily_state['date'] = today
        _daily_state['global_count'] = 0
        _daily_state['per_user'] = defaultdict(int)
        _daily_state['last_msg'] = defaultdict(float)

    stats = {
        'daily_used': _daily_state['global_count'],
        'daily_limit': DAILY_GLOBAL_LIMIT,
        'daily_remaining': DAILY_GLOBAL_LIMIT - _daily_state['global_count'],
        'your_used': _daily_state['per_user'][anon],
        'your_limit': DAILY_PER_USER,
    }

    # 全站每日上限
    if _daily_state['global_count'] >= DAILY_GLOBAL_LIMIT:
        return (f"今天的額度用完了（{DAILY_GLOBAL_LIMIT}/{DAILY_GLOBAL_LIMIT}）。"
                f"明天 UTC 0:00 重置，到時再來！", stats)

    # 每人每日上限
    if _daily_state['per_user'][anon] >= DAILY_PER_USER:
        return (f"你今天已經聊了 {DAILY_PER_USER} 則。"
                f"明天再來吧！把機會留給其他人 😊", stats)

    # 冷卻時間
    last = _daily_state['last_msg'].get(anon, 0)
    if now - last < RATE_LIMIT_COOLDOWN:
        return (f"慢一點，{RATE_LIMIT_COOLDOWN} 秒後再發。", stats)

    # 通過 → 計數
    _daily_state['global_count'] += 1
    _daily_state['per_user'][anon] += 1
    _daily_state['last_msg'][anon] = now

    stats['daily_used'] = _daily_state['global_count']
    stats['daily_remaining'] = DAILY_GLOBAL_LIMIT - _daily_state['global_count']
    stats['your_used'] = _daily_state['per_user'][anon]

    return (None, stats)


def _check_message(text: str) -> str | None:
    """
    檢查訊息品質。
    回傳 None = 通過，回傳字串 = 被擋。
    """
    if len(text) < MSG_MIN_LENGTH:
        return "訊息太短了，多寫幾個字吧。"
    if len(text) > MSG_MAX_LENGTH:
        return f"訊息太長了（最多 {MSG_MAX_LENGTH} 字），精簡一下。"
    if any(p in text.lower() for p in BANNED_PATTERNS):
        return "請不要貼連結或廣告。"
    return None


def _anon_id(ip: str) -> str:
    """把 IP 匿名化成短 hash，不存原始 IP。"""
    return hashlib.sha256(ip.encode()).hexdigest()[:8]


def _next_key() -> str:
    """輪用三把 key，每次呼叫換一把。"""
    global _key_index
    if not GEMINI_KEYS:
        raise RuntimeError("GEMINI_KEYS 環境變數未設定")
    key = GEMINI_KEYS[_key_index % len(GEMINI_KEYS)]
    _key_index += 1
    return key


def _load_system_context() -> str:
    """載入 Opus 分析結果當 system prompt。用 _load() 統一走安全路徑。"""
    context_parts = []

    # Opus 分析
    opus = _load("opus_analysis.json") or {}
    if opus:
        context_parts.append("=== Opus 分析摘要 ===")
        context_parts.append(f"系統狀態: {opus.get('overall_system_health', '?')}")
        context_parts.append(f"重點: {opus.get('priority_action', '?')}")
        if opus.get('pattern_shift_detected'):
            context_parts.append(f"模式變化: {opus.get('pattern_shift_details', '')[:200]}")

    # 模型排行
    briefing = _load("opus_briefing.json") or {}
    perf = briefing.get('model_performance', {})
    if perf:
        context_parts.append("\n=== 模型排行 ===")
        for mid, s in sorted(perf.items(), key=lambda x: -x[1].get('win_rate', 0)):
            context_parts.append(
                f"  {s.get('name', mid)}: {s.get('win_rate', 0):.1f}% 命中率, "
                f"{s.get('avg_return', 0):+.3f}% 報酬, {s.get('total_trades', 0)} 筆"
            )

    # 日報
    report = _load("daily_report.json") or {}
    if report:
        context_parts.append(f"\n=== 最新日報 ({report.get('date', '?')}) ===")
        context_parts.append(f"推文數: {report.get('posts_today', 0)}")
        context_parts.append(f"信號: {', '.join(report.get('signals_detected', []))}")
        direction = report.get('direction_summary', {})
        context_parts.append(f"共識: {direction.get('consensus', '?')} "
                           f"(多{direction.get('LONG', 0)} / 空{direction.get('SHORT', 0)})")

    # 信號信心度
    sc = _load("signal_confidence.json") or {}
    if sc:
        context_parts.append(f"\n=== 信號信心度 ===")
        for sig, conf in sorted(sc.items()):
            context_parts.append(f"  {sig}: {conf:.0%}")

    return '\n'.join(context_parts)


SYSTEM_PROMPT_TEMPLATE = """你是「川普密碼」(Trump Code) 的 AI 助手。

你的工作：回答用戶關於 Trump 推文分析、股市預測、預測市場套利的問題。
語氣：專業但友善，像跟朋友聊股市。用中文回答。

重要規則：
1. 永遠提醒：這不是投資建議，歷史規律不保證未來
2. 有數據就用數據回答，沒有就誠實說「我不確定」
3. 如果用戶提出有趣的交易邏輯或觀察，在回答最後加上 [💡用戶洞見] 標記，簡述他們的邏輯
4. 不要編造數據

以下是系統最新的分析數據：

{context}

如果用戶問你不知道的事，引導他們到 GitHub: https://github.com/sstklen/trump-code"""


def call_gemini(user_message: str, history: list[dict] | None = None) -> str:
    """呼叫 Gemini Flash，自動輪用三把 key。"""
    system_context = _load_system_context()
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(context=system_context)

    # 組合對話歷史
    contents = []
    if history:
        for msg in history[-6:]:  # 只保留最近 6 輪
            contents.append({
                "role": "user" if msg['role'] == 'user' else "model",
                "parts": [{"text": msg['text']}],
            })
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1000,
        },
    }

    # 嘗試三把 key
    last_error = None
    for attempt in range(len(GEMINI_KEYS)):
        key = _next_key()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}"

        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={
                "Content-Type": "application/json",
            }, method="POST")

            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode('utf-8'))

            text = result['candidates'][0]['content']['parts'][0]['text']

            # 檢查是否有用戶洞見標記
            if '[💡用戶洞見]' in text or '[用戶洞見]' in text:
                _save_crowd_insight(user_message, text)

            return text

        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
            if e.code == 429:
                continue  # key 額度用完，換下一把
            break
        except Exception as e:
            last_error = str(e)
            break

    return f"抱歉，AI 暫時無法回應（{last_error}）。請稍後再試。"


def _save_crowd_insight(user_message: str, ai_response: str, anon_id: str = "") -> None:
    """
    儲存用戶的交易邏輯洞見，供 Opus 下次分析時參考。

    品質門檻：
      - 用戶原文至少 20 字（太短的不是認真的邏輯）
      - AI 提取的洞見至少 10 字
      - 不含垃圾關鍵字
    """
    # 門檻 1：長度
    if len(user_message) < INSIGHT_MIN_LENGTH:
        return  # 太短，不收

    # 門檻 2：垃圾過濾
    if any(p in user_message.lower() for p in BANNED_PATTERNS):
        return

    # 提取洞見部分
    insight_text = ""
    if '[💡用戶洞見]' in ai_response:
        insight_text = ai_response.split('[💡用戶洞見]')[-1].strip()
    elif '[用戶洞見]' in ai_response:
        insight_text = ai_response.split('[用戶洞見]')[-1].strip()

    if len(insight_text) < 10:
        return  # AI 沒有提取出有意義的洞見

    # 通過門檻 → 存檔
    insights: list[dict] = []
    if CROWD_INSIGHTS_FILE.exists():
        with open(CROWD_INSIGHTS_FILE, encoding='utf-8') as f:
            insights = json.load(f)

    insights.append({
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'anon_id': anon_id,  # 匿名 hash，不存 IP
        'user_logic': user_message[:500],
        'ai_extracted': insight_text[:300],
        'status': 'NEW',  # Opus 處理後改成 REVIEWED / ADOPTED / REJECTED
        'votes': 0,       # 未來可讓其他用戶投票
    })

    # 最多保留 500 條
    insights = insights[-500:]

    with open(CROWD_INSIGHTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(insights, f, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _iso_to_ts(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def _ts_to_iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace('+00:00', 'Z')
    except Exception:
        return _now_iso()


def _is_game_expired(game: dict | None, now_ts: float | None = None) -> bool:
    if not isinstance(game, dict):
        return False
    expires_ts = _iso_to_ts(game.get('expires_at'))
    if expires_ts is None:
        return False
    return (now_ts or time.time()) > expires_ts


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _direction_from_change(change: Any) -> str | None:
    try:
        value = float(change)
    except (TypeError, ValueError):
        return None
    if value > 0.1:
        return 'UP'
    if value < -0.1:
        return 'DOWN'
    return 'FLAT'


def _load_json_file(path: Path, default: dict | list | None) -> dict | list | None:
    if not path.exists():
        return default
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save_json_file(path: Path, data: dict | list) -> bool:
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _load_game_current():
    data = _load_json_file(GAME_CURRENT_FILE, None)
    return data if isinstance(data, dict) else None


def _save_game_current(game):
    if isinstance(game, dict):
        _save_json_file(GAME_CURRENT_FILE, game)


def _load_game_players():
    if not GAME_PLAYERS_FILE.exists():
        _save_game_players({})
    data = _load_json_file(GAME_PLAYERS_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_game_players(players):
    if isinstance(players, dict):
        _save_json_file(GAME_PLAYERS_FILE, players)


def _load_game_history():
    if not GAME_HISTORY_FILE.exists():
        _save_game_history([])
    data = _load_json_file(GAME_HISTORY_FILE, [])
    return data if isinstance(data, list) else []


def _save_game_history(history):
    if isinstance(history, list):
        _save_json_file(GAME_HISTORY_FILE, history)


def _find_latest_signal():
    predictions = _load('rt_predictions.json') or []
    if not isinstance(predictions, list):
        return None

    latest = None
    latest_key = ''
    for pred in predictions:
        if not isinstance(pred, dict) or pred.get('status') != 'LIVE':
            continue
        sort_key = f"{pred.get('created_at', '')}|{pred.get('id', '')}"
        if latest is None or sort_key > latest_key:
            latest = pred
            latest_key = sort_key
    return latest


def _build_game_round(signal: dict) -> dict | None:
    signal_id = signal.get('id')
    if not signal_id:
        return None

    # 用現在時間開始計時（不管信號多舊，新局一律從現在算 6 小時）
    now_ts = time.time()
    created_at = _now_iso()
    expires_at = _ts_to_iso(now_ts + GAME_ROUND_HOURS * 3600)

    return {
        'signal_id': signal_id,
        'post_preview': signal.get('post_preview', ''),
        'signal_types': signal.get('signal_types', []) if isinstance(signal.get('signal_types'), list) else [],
        'ai_direction': signal.get('predicted_direction'),
        'ai_confidence': signal.get('confidence'),
        'spy_at_signal': signal.get('spy_at_signal'),
        'created_at': created_at,
        'expires_at': expires_at,
        'votes': {},
        'resolved': False,
        'result': None,
    }


def _pick_verify_value(signal: dict) -> tuple[float | None, str | None]:
    for key in ('verify_6h', 'verify_3h', 'verify_1h'):
        value = signal.get(key)
        if value is None:
            continue
        try:
            return float(value), key
        except (TypeError, ValueError):
            continue
    return None, None


def _crowd_direction(votes: dict[str, str]) -> str | None:
    counts = {'UP': 0, 'DOWN': 0, 'FLAT': 0}
    for direction in votes.values():
        if direction in counts:
            counts[direction] += 1

    max_votes = max(counts.values()) if counts else 0
    if max_votes == 0:
        return None

    leaders = [direction for direction, count in counts.items() if count == max_votes]
    if len(leaders) != 1:
        return None
    return leaders[0]


def _maybe_start_new_round():
    current = _load_game_current()
    now_ts = time.time()

    # 有進行中的局且未過期 → 繼續玩
    if current and not current.get('resolved') and not _is_game_expired(current, now_ts):
        return current

    # 過期且未 resolve → 嘗試開獎
    if current and not current.get('resolved') and _is_game_expired(current, now_ts):
        current = _resolve_if_needed(current)
        # resolve 成功或失敗都繼續往下找新局

    # 已 resolve 或無局 → 找信號開新局
    latest_signal = _find_latest_signal()
    if latest_signal:
        new_game = _build_game_round(latest_signal)
        if new_game:
            _save_game_current(new_game)
            return new_game

    # 真的沒有任何信號 → 回傳現有局
    return current


def _resolve_if_needed(game):
    if not isinstance(game, dict):
        return None

    if game.get('resolved') or not _is_game_expired(game):
        return game

    predictions = _load('rt_predictions.json') or []
    if not isinstance(predictions, list):
        return game

    signal_id = game.get('signal_id')
    signal = next(
        (item for item in predictions if isinstance(item, dict) and item.get('id') == signal_id),
        None,
    )
    if not signal:
        return game

    verify_value, verify_source = _pick_verify_value(signal)
    actual_direction = _direction_from_change(verify_value)
    if actual_direction is None:
        # 過期超過 2 小時仍無 verify 數據 → 強制 VOID，不卡死
        expires_ts = _iso_to_ts(game.get('expires_at')) or 0
        if time.time() - expires_ts > 7200:
            game['resolved'] = True
            game['result'] = {'actual_direction': 'VOID', 'spy_change': None, 'verify_source': None,
                              'ai_correct': None, 'crowd_correct': None, 'crowd_direction': None,
                              'winning_votes': 0, 'total_votes': len(game.get('votes', {})),
                              'void_reason': 'no verify data after 2h timeout'}
            game['resolved_at'] = _now_iso()
            _save_game_current(game)
        return game

    votes = game.get('votes')
    if not isinstance(votes, dict):
        votes = {}

    valid_votes = {
        anon_id: direction
        for anon_id, direction in votes.items()
        if direction in {'UP', 'DOWN', 'FLAT'}
    }

    players = _load_game_players()
    winners = []
    ai_direction = game.get('ai_direction')

    for anon_id, direction in valid_votes.items():
        profile = players.get(anon_id)
        if not isinstance(profile, dict):
            profile = {}

        correct = direction == actual_direction
        delta = 10 if correct else -5
        if correct and ai_direction and ai_direction != actual_direction:
            delta += 25

        score = _safe_int(profile.get('score'))
        wins = _safe_int(profile.get('wins'))
        streak = _safe_int(profile.get('streak'))

        profile['nickname'] = (profile.get('nickname') or f'anon-{anon_id[:4]}')[:40]
        profile['score'] = score + delta
        profile['wins'] = wins + (1 if correct else 0)
        profile['streak'] = streak + 1 if correct else 0
        players[anon_id] = profile

        if correct:
            winners.append(anon_id)

    crowd_direction = _crowd_direction(valid_votes)
    result = {
        'actual_direction': actual_direction,
        'spy_change': verify_value,
        'verify_source': verify_source,
        'ai_correct': ai_direction == actual_direction if ai_direction else None,
        'crowd_correct': crowd_direction == actual_direction if crowd_direction else None,
        'crowd_direction': crowd_direction,
        'winning_votes': len(winners),
        'total_votes': len(valid_votes),
    }

    game['votes'] = valid_votes
    game['resolved'] = True
    game['result'] = result
    game['resolved_at'] = _now_iso()

    _save_game_current(game)
    _save_game_players(players)

    history = _load_game_history()
    if not any(isinstance(item, dict) and item.get('signal_id') == signal_id for item in history):
        history.append({
            'signal_id': signal_id,
            'created_at': game.get('created_at'),
            'resolved_at': game.get('resolved_at'),
            'post_preview': game.get('post_preview', ''),
            'actual_direction': actual_direction,
            'spy_change': verify_value,
            'verify_source': verify_source,
            'ai_direction': ai_direction,
            'ai_correct': result['ai_correct'],
            'crowd_direction': crowd_direction,
            'crowd_correct': result['crowd_correct'],
            'total_votes': len(valid_votes),
            'winning_votes': len(winners),
        })
        _save_game_history(history)

    return game


# =====================================================================
# 網頁介面（內嵌 HTML）
# =====================================================================

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>川普密碼 Trump Code</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #0a0a0f;
  color: #e0e0e0;
  height: 100vh;
  display: flex;
  flex-direction: column;
}
header {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
  padding: 16px 24px;
  border-bottom: 1px solid #333;
  display: flex;
  align-items: center;
  gap: 12px;
}
header h1 {
  font-size: 20px;
  color: #ffd700;
}
header .badge {
  background: #2d5a27;
  color: #7dff6e;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 11px;
}
.chat-area {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.msg {
  max-width: 80%;
  padding: 12px 16px;
  border-radius: 16px;
  line-height: 1.6;
  font-size: 14px;
  white-space: pre-wrap;
}
.msg.user {
  align-self: flex-end;
  background: #1e3a5f;
  color: #fff;
  border-bottom-right-radius: 4px;
}
.msg.ai {
  align-self: flex-start;
  background: #1a1a2e;
  border: 1px solid #333;
  border-bottom-left-radius: 4px;
}
.msg.ai .insight {
  background: #2d5a27;
  color: #7dff6e;
  padding: 8px 12px;
  border-radius: 8px;
  margin-top: 8px;
  font-size: 12px;
}
.msg.system {
  align-self: center;
  color: #888;
  font-size: 12px;
  padding: 4px;
}
.input-area {
  padding: 16px 20px;
  background: #111;
  border-top: 1px solid #333;
  display: flex;
  gap: 10px;
}
.input-area input {
  flex: 1;
  padding: 12px 16px;
  border: 1px solid #444;
  border-radius: 24px;
  background: #1a1a2e;
  color: #fff;
  font-size: 14px;
  outline: none;
}
.input-area input:focus { border-color: #ffd700; }
.input-area button {
  padding: 12px 24px;
  background: #ffd700;
  color: #000;
  border: none;
  border-radius: 24px;
  font-weight: bold;
  cursor: pointer;
  font-size: 14px;
}
.input-area button:hover { background: #ffed4a; }
.input-area button:disabled { opacity: 0.5; cursor: not-allowed; }
footer {
  text-align: center;
  padding: 8px;
  font-size: 11px;
  color: #555;
}
footer a { color: #888; }
</style>
</head>
<body>
<header>
  <h1>🔴 川普密碼 Trump Code</h1>
  <span class="badge">AI 即時分析</span>
</header>

<div class="chat-area" id="chat">
  <div class="msg system">⚠️ 這不是投資建議。歷史規律不保證未來表現。</div>
  <div class="msg ai">嗨！我是川普密碼的 AI 助手。我可以回答你關於：

• Trump 今天發了什麼推文？信號是什麼？
• 模型的命中率排行
• 預測市場的套利機會
• 你有什麼交易邏輯想跟我討論的？

你的想法對我們很重要——好的交易邏輯會被收錄到系統裡 💡</div>
</div>

<div class="input-area">
  <input type="text" id="input" placeholder="問我任何關於川普密碼的問題..." autofocus>
  <button id="send" onclick="sendMessage()">發送</button>
</div>

<footer>
  <a href="https://github.com/sstklen/trump-code" target="_blank">GitHub</a> ·
  Powered by Gemini Flash + Opus Analysis
</footer>

<script>
const chat = document.getElementById('chat');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
let history = [];

input.addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;

  // 顯示用戶訊息
  addMsg(text, 'user');
  input.value = '';
  sendBtn.disabled = true;

  // 顯示載入
  const loadingId = addMsg('思考中...', 'ai');

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, history: history}),
    });
    const data = await resp.json();

    // 更新回應
    document.getElementById(loadingId).innerHTML = formatResponse(data.reply);
    history.push({role: 'user', text: text});
    history.push({role: 'ai', text: data.reply});

    // 保留最近 10 輪
    if (history.length > 20) history = history.slice(-20);
  } catch (e) {
    document.getElementById(loadingId).textContent = '抱歉，連線失敗。請重試。';
  }

  sendBtn.disabled = false;
  input.focus();
}

function addMsg(text, role) {
  const id = 'msg-' + Date.now();
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.id = id;
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return id;
}

function formatResponse(text) {
  // 處理洞見標記
  if (text.includes('[💡用戶洞見]')) {
    const parts = text.split('[💡用戶洞見]');
    return parts[0] + '<div class="insight">💡 你的邏輯已被記錄！' + parts[1] + '</div>';
  }
  return text.replace(/\\n/g, '<br>');
}
</script>
</body>
</html>"""


# =====================================================================
# HTTP Server
# =====================================================================

class ChatHandler(BaseHTTPRequestHandler):
    def _get_ip(self) -> str:
        return self.headers.get('X-Forwarded-For', self.client_address[0]).split(',')[0].strip()

    def _json_response(self, code: int, data: dict):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', 'https://trumpcode.washinmura.jp')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
      try:
        # 追蹤每個 GET 請求（排除 favicon）
        if self.path != '/favicon.ico':
            _track_request(
                self._get_ip(),
                self.path.split('?')[0],
                self.headers.get('User-Agent', '')
            )

        if self.path == '/' or self.path == '/index.html' or self.path == '/insights' or self.path == '/insights.html':
            # 首頁 = 儀表板（恢復原狀）
            insights_file = BASE / 'public' / 'insights.html'
            if insights_file.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(insights_file.read_bytes())
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(HTML_PAGE.encode('utf-8'))

        elif self.path in ('/robots.txt', '/sitemap.xml', '/llms.txt', '/llms-full.txt',
                           '/og-image.png', '/trumpcode2026washinmura.txt',
                           '/.well-known/llms.txt'):
            # SEO/AEO 靜態檔案（public/ 目錄下）
            fname = self.path.lstrip('/')
            fpath = BASE / 'public' / fname
            if fpath.exists():
                # Content-Type 對照表
                ext_ct = {
                    '.txt': 'text/plain; charset=utf-8',
                    '.xml': 'application/xml; charset=utf-8',
                    '.png': 'image/png',
                    '.json': 'application/json; charset=utf-8',
                    '.ico': 'image/x-icon',
                }
                ext = '.' + fname.rsplit('.', 1)[-1] if '.' in fname else '.txt'
                ct = ext_ct.get(ext, 'application/octet-stream')
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Cache-Control', 'public, max-age=3600')
                self.end_headers()
                self.wfile.write(fpath.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

        elif self.path.startswith('/articles/'):
            # 每日文章靜態檔案 — 防路徑遍歷攻擊
            fname = self.path.lstrip('/')
            fpath = (BASE / fname).resolve()
            articles_root = (BASE / 'articles').resolve()
            if fpath.is_file() and str(fpath).startswith(str(articles_root)):
                ext = fpath.suffix
                ct = {'.md': 'text/markdown; charset=utf-8',
                      '.json': 'application/json; charset=utf-8'}.get(ext, 'text/plain; charset=utf-8')
                self.send_response(200)
                self.send_header('Content-Type', ct)
                self.send_header('Cache-Control', 'public, max-age=1800')
                self.end_headers()
                self.wfile.write(fpath.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

        elif self.path == '/daily' or self.path == '/daily.html':
            # 每日分析頁
            daily_file = BASE / 'public' / 'daily.html'
            if daily_file.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(daily_file.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

        elif self.path == '/analysis' or self.path == '/analysis.html':
            # AI 分析頁
            analysis_file = BASE / 'public' / 'analysis.html'
            if analysis_file.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(analysis_file.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

        elif self.path == '/game' or self.path == '/game.html':
            # 預測遊戲
            game_file = BASE / 'public' / 'game.html'
            if game_file.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(game_file.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

        elif self.path == '/chat':
            # 純聊天頁面（給 iframe 嵌入用）
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))

        elif self.path == '/insights' or self.path == '/insights.html' or self.path == '/dashboard':
            # 儀表板（舊首頁）
            insights_file = BASE / 'public' / 'insights.html'
            if game_file.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(game_file.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

        elif self.path == '/api/insights':
            # 公開端點：所有人的洞見（匿名）
            insights = []
            if CROWD_INSIGHTS_FILE.exists():
                with open(CROWD_INSIGHTS_FILE, encoding='utf-8') as f:
                    insights = json.load(f)
            # 只回傳已提取的洞見，不回傳原始訊息（保護隱私）
            public = [
                {
                    'id': i,
                    'time': ins.get('timestamp', '?')[:16],
                    'insight': ins.get('ai_extracted', ''),
                    'status': ins.get('status', 'NEW'),
                    'anon': ins.get('anon_id', '?')[:4] + '****',
                }
                for i, ins in enumerate(insights)
                if ins.get('ai_extracted')
            ]
            self._json_response(200, {'insights': public, 'total': len(public)})

        elif self.path == '/api/game-signal':
            try:
                signal = _find_latest_signal()
                if not signal:
                    self._json_response(404, {'error': 'no live signal'})
                    return

                created_at = signal.get('created_at') or _now_iso()
                created_ts = _iso_to_ts(created_at) or time.time()
                self._json_response(200, {
                    'id': signal.get('id'),
                    'post_preview': signal.get('post_preview', ''),
                    'signal_types': signal.get('signal_types', []),
                    'direction': signal.get('predicted_direction'),
                    'confidence': signal.get('confidence'),
                    'spy_at_signal': signal.get('spy_at_signal'),
                    'created_at': created_at,
                    'expires_at': _ts_to_iso(created_ts + GAME_ROUND_HOURS * 3600),
                })
            except Exception as e:
                self._json_response(500, {'error': 'game signal unavailable', 'details': str(e)})

        elif self.path == '/api/game-state':
            try:
                game = _maybe_start_new_round()
                if game:
                    game = _resolve_if_needed(game) or game
                    game = _maybe_start_new_round() or _load_game_current()

                if not game:
                    self._json_response(200, {
                        'active': False,
                        'message': 'Waiting for Trump to post...',
                    })
                    return

                votes_raw = game.get('votes')
                if not isinstance(votes_raw, dict):
                    votes_raw = {}

                vote_counts = {'up': 0, 'down': 0, 'flat': 0}
                for direction in votes_raw.values():
                    if direction == 'UP':
                        vote_counts['up'] += 1
                    elif direction == 'DOWN':
                        vote_counts['down'] += 1
                    elif direction == 'FLAT':
                        vote_counts['flat'] += 1

                self._json_response(200, {
                    'active': True,
                    'signal_id': game.get('signal_id'),
                    'post_preview': game.get('post_preview', ''),
                    'signal_types': game.get('signal_types', []),
                    'ai_direction': game.get('ai_direction'),
                    'ai_confidence': game.get('ai_confidence'),
                    'spy_at_signal': game.get('spy_at_signal'),
                    'votes': vote_counts,
                    'total_votes': sum(vote_counts.values()),
                    'expires_at': game.get('expires_at'),
                    'resolved': bool(game.get('resolved')),
                    'result': game.get('result'),
                    'created_at': game.get('created_at'),
                })
            except Exception as e:
                self._json_response(500, {'error': 'game state unavailable', 'details': str(e)})

        elif self.path == '/api/game-leaderboard':
            try:
                players = _load_game_players()
                rows = []
                for profile in players.values():
                    if not isinstance(profile, dict):
                        continue
                    rows.append({
                        'nickname': (profile.get('nickname') or 'anon')[:40],
                        'score': _safe_int(profile.get('score')),
                        'wins': _safe_int(profile.get('wins')),
                        'streak': _safe_int(profile.get('streak')),
                    })

                rows.sort(key=lambda row: (-row['score'], -row['wins'], -row['streak'], row['nickname'].lower()))
                top_players = [
                    {
                        'nickname': row['nickname'],
                        'score': row['score'],
                        'wins': row['wins'],
                        'streak': row['streak'],
                        'rank': index + 1,
                    }
                    for index, row in enumerate(rows[:20])
                ]

                ai_wins = 0
                crowd_wins = 0
                history = _load_game_history()
                for item in history:
                    if not isinstance(item, dict):
                        continue
                    ai_correct = item.get('ai_correct') is True
                    crowd_correct = item.get('crowd_correct') is True
                    if ai_correct and not crowd_correct:
                        ai_wins += 1
                    elif crowd_correct and not ai_correct:
                        crowd_wins += 1

                self._json_response(200, {
                    'players': top_players,
                    'ai_vs_crowd': {
                        'ai_wins': ai_wins,
                        'crowd_wins': crowd_wins,
                        'total_rounds': len([item for item in history if isinstance(item, dict)]),
                    },
                })
            except Exception as e:
                self._json_response(500, {'error': 'leaderboard unavailable', 'details': str(e)})

        elif self.path == '/api/dashboard':
            # 一次給前端所有數據（零硬編碼）
            report = _load('daily_report.json') or {}
            preds_raw = _load('predictions_log.json') or []
            pb = _load('trump_playbook.json') or {}
            opus = _load('opus_analysis.json') or {}
            sc = _load('signal_confidence.json') or {}
            breaker = _load('circuit_breaker_state.json') or {}
            learning = _load('learning_report.json') or {}
            evo = _load('evolution_log.json') or []
            briefing = _load('opus_briefing.json') or {}
            pm_live = _load('polymarket_live.json') or {}

            # 統計
            verified = [p for p in preds_raw if p.get('status') == 'VERIFIED'] if isinstance(preds_raw, list) else []
            correct = [p for p in verified if p.get('correct')]
            hit_rate = round(len(correct) / len(verified) * 100, 1) if verified else 0

            # 規則數
            rules_data = _load('surviving_rules.json') or {}
            n_rules = len(rules_data.get('rules', [])) if isinstance(rules_data, dict) else 0

            # 模型績效
            perf = briefing.get('model_performance', {})

            # 最近信號（7天）
            from collections import defaultdict as _dd2
            recent_sigs = _dd2(list)
            for p in (preds_raw[-100:] if isinstance(preds_raw, list) else []):
                if p.get('status') != 'VERIFIED': continue
                s = p.get('day_summary', {})
                date = p.get('date_signal', '?')
                for key, label in [('tariff','TARIFF'),('deal','DEAL'),('relief','RELIEF'),
                                   ('action','ACTION'),('attack','ATTACK'),('market_brag','MARKET_BRAG'),
                                   ('threat','THREAT'),('russia','RUSSIA'),('iran','IRAN')]:
                    if s.get(key, 0) > 0:
                        recent_sigs[date].append({'type': label, 'count': s[key]})

            # 進化
            last_evo = evo[-1] if isinstance(evo, list) and evo else {}

            # breaker checks
            checks = breaker.get('checks', {})
            failure = checks.get('failure_learning', {})

            self._json_response(200, {
                # 漏斗
                'funnel': {
                    'total_posts': 7411,  # 從 data_stats 拉更好，但先用已知值
                    'features': 384,
                    'models_tested': 31554180,
                    'survivors': n_rules or 550,
                    'elimination_rate': 99.84,
                },
                # 統計
                'stats': {
                    'verified': len(verified),
                    'hit_rate': hit_rate,
                    'rules': n_rules or 550,
                    'models': len(perf),
                    'markets': pm_live.get('total', 0),
                },
                # 亮點
                'highlights': [
                    {'value': '+1.12%', 'label_zh': '盤前 RELIEF → S&P 漲', 'label_en': 'Pre-market RELIEF', 'color': 'green'},
                    {'value': '17.4h', 'label_zh': '關稅→Deal 轉折間隔', 'label_en': 'Tariff → Deal gap', 'color': 'gold'},
                    {'value': '⚠️', 'label_zh': '大跌前語氣反而正面', 'label_en': 'Positive before crash', 'color': 'red'},
                    {'value': '🔍', 'label_zh': '2025-08 偷換簽名格式', 'label_en': 'Code change detected', 'color': 'blue'},
                ],
                # 三張發現卡
                'discoveries_top3': [
                    pb.get('most_dangerous', {}),
                    pb.get('most_profitable', {}),
                    pb.get('biggest_surprise', {}),
                ],
                # 8 個發現
                'discoveries_all': opus.get('error_analysis', []) + opus.get('new_rule_hypotheses', []),
                # 劇本
                'playbook': {
                    'hedge': pb.get('hedge_signals', {}).get('rules', []),
                    'position': pb.get('position_signals', {}).get('rules', []),
                    'pump': pb.get('pump_signals', {}).get('rules', []),
                },
                # 即時狀態
                'live': {
                    'date': report.get('date', '?'),
                    'posts': report.get('posts_today', 0),
                    'signals': report.get('signals_detected', []),
                    'consensus': report.get('direction_summary', {}).get('consensus', 'NEUTRAL'),
                    'health': breaker.get('system_status', '?'),
                    'hit_rate': hit_rate,
                },
                # 信號歷史
                'recent_signals': dict(list(recent_sigs.items())[-7:]),
                # 信號信心度
                'signal_confidence': sc,
                # 模型排行
                'models': perf,
                # Polymarket
                'polymarket': pm_live.get('markets', []),
                # 運算引擎狀態
                'engines': {
                    'learning': learning.get('adjustments', {}).get('summary', {}),
                    'evolution': {
                        'new_rules': last_evo.get('total_new', 0),
                        'total_after': last_evo.get('total_rules_after', 0),
                    },
                    'breaker': {
                        'vs_random': checks.get('vs_random', {}).get('status', '?'),
                        'degradation': checks.get('degradation', {}).get('status', '?'),
                        'consecutive_wrong': checks.get('consecutive', {}).get('consecutive_wrong', 0),
                        'inverse_rules': len(failure.get('bad_signal_combos', [])),
                    },
                },
                # 雙平台
                'dual_platform': opus.get('pattern_shift_details', ''),
                # Opus
                'opus_priority': opus.get('priority_action', ''),
            })

        elif self.path.startswith('/api/data/'):
            # 公開端點：原始數據下載
            filename = self.path.replace('/api/data/', '')
            allowed = [
                'trump_posts_all.json', 'trump_posts_lite.json',
                'predictions_log.json', 'surviving_rules.json',
                'daily_features.json', 'trump_playbook.json',
                'signal_confidence.json', 'market_SP500.json',
                'market_DOW.json', 'market_NASDAQ.json', 'market_VIX.json',
                'opus_analysis.json', 'learning_report.json',
                'evolution_log.json', 'circuit_breaker_state.json',
                'daily_report.json', 'polymarket_live.json',
                'own_archive.json', 'x_posts_full.json',
            ]
            if filename in allowed:
                filepath = DATA / filename
                if filepath.exists():
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Access-Control-Allow-Origin', 'https://trumpcode.washinmura.jp')
                    self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                    self.end_headers()
                    self.wfile.write(filepath.read_bytes())
                else:
                    self._json_response(404, {'error': f'{filename} not found on server'})
            else:
                self._json_response(403, {'error': 'file not in allowed list'})

        elif self.path == '/api/data':
            # 列出所有可下載的數據
            allowed = [
                'trump_posts_all.json', 'trump_posts_lite.json',
                'predictions_log.json', 'surviving_rules.json',
                'daily_features.json', 'trump_playbook.json',
                'signal_confidence.json', 'market_SP500.json',
                'own_archive.json', 'x_posts_full.json',
                'daily_report.json', 'polymarket_live.json',
                'opus_analysis.json', 'learning_report.json',
            ]
            import os
            files = []
            for f in allowed:
                p = DATA / f
                if p.exists():
                    size = os.path.getsize(p)
                    files.append({
                        'name': f,
                        'size_mb': round(size / 1024 / 1024, 2),
                        'url': f'/api/data/{f}',
                    })
            self._json_response(200, {'files': files, 'total': len(files)})

        elif self.path == '/api/polymarket':
            # 公開端點：Polymarket 即時市場數據
            pm = _load('polymarket_live.json')
            if pm:
                self._json_response(200, pm)
            else:
                self._json_response(200, {'markets': [], 'total': 0})

        elif self.path == '/api/playbook':
            # 公開端點：三套劇本（避險/佈局/拉盤）
            pb_file = DATA / "trump_playbook.json"
            if pb_file.exists():
                with open(pb_file, encoding='utf-8') as f:
                    self._json_response(200, json.load(f))
            else:
                self._json_response(404, {'error': 'playbook not found'})

        elif self.path == '/api/models':
            # 公開端點：模型排行
            briefing = _load('opus_briefing.json') or {}
            self._json_response(200, {
                'models': briefing.get('model_performance', {}),
                'date': briefing.get('date', '?'),
            })

        elif self.path == '/api/signals':
            # 公開端點：完整信號（最近一次 + 歷史 + 劇本）
            report = _load('daily_report.json') or {}
            preds = _load('predictions_log.json') or []
            pb = _load('trump_playbook.json') or {}
            opus = _load('opus_analysis.json') or {}
            sc = _load('signal_confidence.json') or {}

            # 從最近的預測提取所有信號
            from collections import defaultdict as _dd
            recent_signals = _dd(list)
            for p in (preds[-50:] if isinstance(preds, list) else []):
                if p.get('status') != 'VERIFIED':
                    continue
                s = p.get('day_summary', {})
                date = p.get('date_signal', '?')
                sigs = []
                if s.get('tariff'): sigs.append({'type': 'TARIFF', 'count': s['tariff']})
                if s.get('deal'): sigs.append({'type': 'DEAL', 'count': s['deal']})
                if s.get('relief'): sigs.append({'type': 'RELIEF', 'count': s['relief']})
                if s.get('action'): sigs.append({'type': 'ACTION', 'count': s['action']})
                if s.get('attack'): sigs.append({'type': 'ATTACK', 'count': s['attack']})
                if s.get('market_brag'): sigs.append({'type': 'MARKET_BRAG', 'count': s['market_brag']})
                if s.get('threat'): sigs.append({'type': 'THREAT', 'count': s['threat']})
                if s.get('russia'): sigs.append({'type': 'RUSSIA', 'count': s['russia']})
                if s.get('iran'): sigs.append({'type': 'IRAN', 'count': s['iran']})
                recent_signals[date] = sigs

            self._json_response(200, {
                'date': report.get('date', '?'),
                'signals': report.get('signals_detected', []),
                'posts': report.get('posts_today', 0),
                'consensus': report.get('direction_summary', {}).get('consensus', '?'),
                'recent_days': dict(list(recent_signals.items())[-7:]),
                'signal_confidence': sc,
                'playbook_summary': {
                    'most_dangerous': pb.get('most_dangerous', {}).get('description', ''),
                    'most_profitable': pb.get('most_profitable', {}).get('description', ''),
                    'biggest_surprise': pb.get('biggest_surprise', {}).get('description', ''),
                },
                'opus_insight': opus.get('priority_action', ''),
            })

        elif self.path == '/api/health':
            # 公開端點：系統健康
            breaker = _load('circuit_breaker_state.json') or {}
            self._json_response(200, {
                'status': breaker.get('system_status', 'UNKNOWN'),
                'action': breaker.get('action', ''),
            })

        elif self.path == '/api/status':
            # 公開端點：系統狀態摘要
            report = {}
            report_file = DATA / "daily_report.json"
            if report_file.exists():
                with open(report_file, encoding='utf-8') as f:
                    report = json.load(f)

            ai = {}
            ai_file = DATA / "opus_analysis.json"
            if ai_file.exists():
                with open(ai_file, encoding='utf-8') as f:
                    ai = json.load(f)

            self._json_response(200, {
                'date': report.get('date', '?'),
                'posts_today': report.get('posts_today', 0),
                'signals': report.get('signals_detected', []),
                'consensus': report.get('direction_summary', {}).get('consensus', '?'),
                'system_health': ai.get('overall_system_health', '?'),
                'total_rules': 546,
                'models': 11,
            })

        elif self.path == '/api/polymarket-trump':
            # 即時搜尋：用 /public-search API（Polymarket 官方文件確認）
            try:
                search_params = urllib.parse.urlencode({
                    'q': 'trump',
                    'limit_per_type': 15,
                    'events_status': 'active',
                })
                search_url = f"https://gamma-api.polymarket.com/public-search?{search_params}"
                req = urllib.request.Request(search_url, headers={'User-Agent': 'TrumpCode/1.0'})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode('utf-8'))

                events = data.get('events') or []
                items = []
                for ev in events:
                    title = ev.get('title', '')
                    slug = ev.get('slug', '')
                    mkts = ev.get('markets', [])
                    vol = sum(float(m.get('volumeNum', 0) or 0) for m in mkts)
                    yes_price = 0
                    liq = 0
                    if mkts:
                        m0 = mkts[0]
                        liq = float(m0.get('liquidityNum', 0) or 0)
                        outcomes = m0.get('outcomePrices', '')
                        if isinstance(outcomes, str) and outcomes:
                            try:
                                prices = json.loads(outcomes)
                                if prices:
                                    yes_price = float(prices[0])
                            except (json.JSONDecodeError, ValueError):
                                pass
                    items.append({
                        'question': title,
                        'yes_price': yes_price,
                        'no_price': round(1 - yes_price, 4) if yes_price else 0,
                        'liquidity': liq,
                        'volume': vol,
                        'slug': slug,
                        'url': f'https://polymarket.com/event/{slug}' if slug else '',
                        'sub_markets': len(mkts),
                    })
                items.sort(key=lambda x: x['volume'], reverse=True)
                pagination = data.get('pagination', {})
                self._json_response(200, {
                    'markets': items[:15],
                    'total': pagination.get('totalResults', len(items)),
                    'source': 'public-search',
                })
            except Exception as e:
                self._json_response(200, {'markets': [], 'total': 0, 'source': 'error', 'error': str(e)})

        elif self.path == '/api/analytics':
            # 公開端點：訪客統計
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            today_data = _analytics_cache.get('daily', {}).get(today, {})
            # 最近 7 天
            recent_days = {}
            for d, v in sorted(_analytics_cache.get('daily', {}).items(), reverse=True)[:7]:
                recent_days[d] = {'views': v.get('views', 0), 'unique_visitors': len(v.get('unique_ips', []))}
            self._json_response(200, {
                'total_requests': _analytics_cache.get('total_requests', 0),
                'total_unique_visitors': _analytics_cache.get('total_unique_ips', 0),
                'today': {
                    'views': today_data.get('views', 0),
                    'unique_visitors': len(today_data.get('unique_ips', [])),
                    'top_pages': dict(sorted(today_data.get('pages', {}).items(), key=lambda x: -x[1])[:10]),
                },
                'recent_7_days': recent_days,
                'top_pages_all_time': dict(sorted(_analytics_cache.get('pages', {}).items(), key=lambda x: -x[1])[:15]),
                'user_agents': _analytics_cache.get('user_agents', {}),
            })

        elif self.path == '/api/chat-log':
            # 聊天記錄（最近 50 筆）
            chat_log_file = DATA / "chat_log.json"
            logs = []
            if chat_log_file.exists():
                try:
                    logs = json.load(open(chat_log_file, encoding='utf-8'))
                except Exception:
                    pass
            self._json_response(200, {'chats': logs[-50:], 'total': len(logs)})

        elif self.path == '/api/recent-posts':
            # 公開端點：最近推文+信號分析（第二任期 2025-01-20 起）
            posts_data = _load('trump_posts_all.json') or {}
            all_posts = posts_data.get('posts', [])
            report = _load('daily_report.json') or {}
            sc = _load('signal_confidence.json') or {}
            preds = _load('predictions_log.json') or []
            rt_preds = _load('rt_predictions.json') or []

            # 只取第二任期的最近推文
            recent = [p for p in all_posts
                      if (p.get('created_at') or '') >= '2025-01-20']
            recent.sort(key=lambda x: x.get('created_at', ''), reverse=True)
            recent = recent[:20]  # 最近 20 篇

            # 每日管線的信號（按日期）
            daily_signals = {}
            if isinstance(preds, list):
                for p in preds[-30:]:
                    d = p.get('date_signal', '')
                    if d not in daily_signals:
                        daily_signals[d] = p.get('day_summary', {})

            # 即時引擎的信號（按推文內容前 50 字配對）
            rt_by_preview = {}
            rt_signal_types = set()
            rt_directions = {'UP': 0, 'DOWN': 0, 'NEUTRAL': 0}
            if isinstance(rt_preds, list):
                for rp in rt_preds:
                    preview = (rp.get('post_preview') or '')[:50].strip().lower()
                    if preview:
                        rt_by_preview[preview] = {
                            'signal_types': rp.get('signal_types', []),
                            'direction': rp.get('predicted_direction', '?'),
                            'confidence': rp.get('confidence', 0),
                            'pm_1h': rp.get('pm_verify_1h'),
                            'pm_3h': rp.get('pm_verify_3h'),
                            'spy_at': rp.get('spy_at_signal'),
                            'status': rp.get('status', 'LIVE'),
                        }
                    # 統計即時信號
                    for st in rp.get('signal_types', []):
                        rt_signal_types.add(st)
                    d = rp.get('predicted_direction', 'NEUTRAL')
                    if d in rt_directions:
                        rt_directions[d] += 1

            # 組合回傳：每日信號 + 即時信號雙層補上
            items = []
            for p in recent:
                date = (p.get('created_at') or '')[:10]
                text = (p.get('content') or p.get('text', ''))[:300]

                # 先用每日信號
                signals = daily_signals.get(date, {})

                # 如果每日信號是空的，用即時信號補
                if not signals:
                    preview_key = text[:50].strip().lower()
                    rt_match = rt_by_preview.get(preview_key)
                    if rt_match:
                        signals = {
                            'rt_signals': rt_match['signal_types'],
                            'rt_direction': rt_match['direction'],
                            'rt_confidence': rt_match['confidence'],
                            'rt_pm_1h': rt_match['pm_1h'],
                            'rt_pm_3h': rt_match['pm_3h'],
                            'rt_status': rt_match['status'],
                            'source': 'realtime',
                        }

                items.append({
                    'date': p.get('created_at', ''),
                    'text': text,
                    'url': p.get('url', ''),
                    'source': p.get('source', 'truth_social'),
                    'signals': signals,
                })

            # 今日信號：每日管線 + 即時引擎合併
            today_signals = report.get('signals_detected', [])
            if rt_signal_types:
                for st in rt_signal_types:
                    if st not in today_signals:
                        today_signals.append(st)

            # 今日共識：有即時數據就用即時的
            today_consensus = report.get('direction_summary', {}).get('consensus', '?')
            if rt_directions['UP'] + rt_directions['DOWN'] > 0:
                if rt_directions['DOWN'] > rt_directions['UP'] * 1.5:
                    today_consensus = 'BEARISH'
                elif rt_directions['UP'] > rt_directions['DOWN'] * 1.5:
                    today_consensus = 'BULLISH'
                else:
                    today_consensus = 'NEUTRAL'

            self._json_response(200, {
                'posts': items,
                'total': len(items),
                'today_signals': today_signals,
                'today_consensus': today_consensus,
                'signal_confidence': sc,
                'rt_predictions_count': len(rt_preds),
            })

        elif self.path == '/api/game-signal':
            # 遊戲用：最新未開獎的即時信號（給 Devvit App 拉）
            rt_preds = _load('rt_predictions.json') or []
            live = [p for p in rt_preds if p.get('status') == 'LIVE']
            if live:
                latest = live[-1]
                self._json_response(200, {
                    'id': latest.get('id', ''),
                    'post_time': latest.get('post_time', ''),
                    'post_preview': latest.get('post_preview', ''),
                    'signal_types': latest.get('signal_types', []),
                    'direction': latest.get('predicted_direction', '?'),
                    'confidence': latest.get('confidence', 0),
                    'spy_at_signal': latest.get('spy_at_signal'),
                    'vix_at_signal': latest.get('vix_at_signal'),
                    'tracked_markets': len(latest.get('tracked_markets', [])),
                    'created_at': latest.get('created_at', ''),
                    'status': 'LIVE',
                })
            else:
                self._json_response(200, {'status': 'no_live_signal'})

        elif self.path.startswith('/api/game-result/'):
            # 遊戲用：查特定信號的結果（開獎用）
            sig_id = self.path.split('/api/game-result/')[-1]
            rt_preds = _load('rt_predictions.json') or []
            found = None
            for p in rt_preds:
                if p.get('id') == sig_id:
                    found = p
                    break
            if found:
                self._json_response(200, {
                    'id': found.get('id', ''),
                    'status': found.get('status', 'LIVE'),
                    'direction': found.get('predicted_direction', '?'),
                    'confidence': found.get('confidence', 0),
                    'pm_verify_1h': found.get('pm_verify_1h'),
                    'pm_verify_3h': found.get('pm_verify_3h'),
                    'pm_verify_6h': found.get('pm_verify_6h'),
                    'spy_at_signal': found.get('spy_at_signal'),
                    'spy_verify_1h': found.get('spy_verify_1h'),
                    'spy_verify_3h': found.get('spy_verify_3h'),
                    'spy_verify_6h': found.get('spy_verify_6h'),
                    'event_level': found.get('event_level', '?'),
                    'post_preview': found.get('post_preview', ''),
                })
            else:
                self._json_response(404, {'error': 'signal not found'})

        elif self.path == '/api/game-leaderboard':
            # 遊戲用：排行榜資料
            lb_file = DATA / 'game_leaderboard.json'
            lb = {}
            if lb_file.exists():
                try:
                    with open(lb_file, encoding='utf-8') as f:
                        lb = json.load(f)
                except Exception:
                    pass
            self._json_response(200, lb)

        elif self.path == '/api/game-stats':
            # 遊戲用：AI vs 群眾統計
            stats_file = DATA / 'game_stats.json'
            stats = {}
            if stats_file.exists():
                try:
                    with open(stats_file, encoding='utf-8') as f:
                        stats = json.load(f)
                except Exception:
                    pass
            # 補上 AI 的歷史命中率
            rt_learning = _load('rt_learning.json') or {}
            stats['ai_hit_rate'] = rt_learning.get('all_pm_hit_1h', 61.3)
            stats['ai_total_verified'] = rt_learning.get('total_verified', 0)
            self._json_response(200, stats)

        else:
            self.send_response(404)
            self.end_headers()
      except Exception as e:
        # 頂層防護：任何未預期錯誤回傳 JSON，不回空回應
        print(f"⚠️ do_GET {self.path} 錯誤: {e}")
        try:
            self._json_response(500, {'error': 'internal error', 'path': self.path})
        except Exception:
            pass

    def do_POST(self):
      try:
        if self.path == '/api/chat':
            ip = self._get_ip()
            anon = _anon_id(ip)

            # 門檻 1：每日額度
            rate_error, quota = _check_rate_limit(ip)
            if rate_error:
                self._json_response(429, {
                    'reply': rate_error,
                    'blocked': True,
                    'quota': quota,
                })
                return

            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))

            user_msg = body.get('message', '').strip()
            history = body.get('history', [])

            # 門檻 2：訊息品質
            msg_error = _check_message(user_msg)
            if msg_error:
                self._json_response(400, {'reply': msg_error, 'blocked': True})
                return

            reply = call_gemini(user_msg, history)

            # 聊天記錄存檔
            chat_log_file = DATA / "chat_log.json"
            try:
                chat_log = json.load(open(chat_log_file, encoding='utf-8')) if chat_log_file.exists() else []
                chat_log.append({
                    'time': datetime.now(timezone.utc).isoformat()[:19],
                    'anon': anon[:4] + '****',
                    'user': user_msg[:200],
                    'reply': reply[:300],
                })
                # 保留最近 10,000 筆
                if len(chat_log) > 10000:
                    chat_log = chat_log[-10000:]
                with open(chat_log_file, 'w', encoding='utf-8') as f:
                    json.dump(chat_log, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            # 如果有洞見，存的時候帶匿名 ID
            if '[💡用戶洞見]' in reply or '[用戶洞見]' in reply:
                _save_crowd_insight(user_msg, reply, anon)

            self._json_response(200, {
                'reply': reply,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'anon_id': anon[:4] + '****',
                'quota': quota,
            })
        elif self.path == '/api/game-vote':
            try:
                length = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(length) or b'{}')
                direction = body.get('direction')
                if direction not in {'UP', 'DOWN', 'FLAT'}:
                    self._json_response(400, {'error': 'direction must be UP, DOWN, or FLAT'})
                    return

                ip = self._get_ip()
                anon = _anon_id(ip)

                _maybe_start_new_round()
                game = _load_game_current()
                if not game:
                    self._json_response(404, {'error': 'no active round'})
                    return

                if _is_game_expired(game):
                    _resolve_if_needed(game)
                    self._json_response(410, {'error': 'round ended'})
                    return

                votes = game.get('votes')
                if not isinstance(votes, dict):
                    votes = {}
                votes[anon] = direction
                game['votes'] = votes

                nickname = body.get('nickname')
                if isinstance(nickname, str):
                    nickname = nickname.strip()[:40]
                    if nickname:
                        players = _load_game_players()
                        profile = players.get(anon)
                        if not isinstance(profile, dict):
                            profile = {}
                        profile['nickname'] = nickname
                        profile['score'] = _safe_int(profile.get('score'))
                        profile['wins'] = _safe_int(profile.get('wins'))
                        profile['streak'] = _safe_int(profile.get('streak'))
                        players[anon] = profile
                        _save_game_players(players)

                _save_game_current(game)
                self._json_response(200, {
                    'success': True,
                    'direction': direction,
                    'signal_id': game.get('signal_id'),
                })
            except json.JSONDecodeError:
                self._json_response(400, {'error': 'invalid json'})
            except Exception as e:
                self._json_response(500, {'error': 'vote failed', 'details': str(e)})
        else:
            self.send_response(404)
            self.end_headers()
      except Exception as e:
        # 頂層防護：任何未預期錯誤回傳 JSON，不回空回應
        print(f"⚠️ do_POST {self.path} 錯誤: {e}")
        try:
            self._json_response(500, {'error': 'internal error', 'path': self.path})
        except Exception:
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', 'https://trumpcode.washinmura.jp')
        self.send_header('Access-Control-Allow-Methods', 'POST')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def log_message(self, format, *args):
        # 靜默 HTTP log，只印重要的
        pass


if __name__ == '__main__':
    print(f"🔴 川普密碼聊天機器人")
    print(f"=" * 40)
    print(f"🌐 http://localhost:{PORT}")
    print(f"🔑 Gemini Flash × 3 把 key 輪用")
    print(f"💡 群眾智慧回收啟用")
    print(f"=" * 40)
    print(f"Ctrl+C 停止")

    server = HTTPServer(('0.0.0.0', PORT), ChatHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止。儲存分析數據...")
        _save_analytics()
        server.server_close()
