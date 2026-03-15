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


def _load(filename: str) -> dict | list | None:
    """安全載入 data/ 下的 JSON 檔案。"""
    path = DATA / filename
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)

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
    key = GEMINI_KEYS[_key_index % len(GEMINI_KEYS)]
    _key_index += 1
    return key


def _load_system_context() -> str:
    """載入 Opus 分析結果當 system prompt。"""
    context_parts = []

    # Opus 分析
    opus_file = DATA / "opus_analysis.json"
    if opus_file.exists():
        with open(opus_file, encoding='utf-8') as f:
            opus = json.load(f)
        context_parts.append("=== Opus 分析摘要 ===")
        context_parts.append(f"系統狀態: {opus.get('overall_system_health', '?')}")
        context_parts.append(f"重點: {opus.get('priority_action', '?')}")
        if opus.get('pattern_shift_detected'):
            context_parts.append(f"模式變化: {opus.get('pattern_shift_details', '')[:200]}")

    # 模型排行
    briefing_file = DATA / "opus_briefing.json"
    if briefing_file.exists():
        with open(briefing_file, encoding='utf-8') as f:
            briefing = json.load(f)
        perf = briefing.get('model_performance', {})
        if perf:
            context_parts.append("\n=== 模型排行 ===")
            for mid, s in sorted(perf.items(), key=lambda x: -x[1].get('win_rate', 0)):
                context_parts.append(
                    f"  {s.get('name', mid)}: {s.get('win_rate', 0):.1f}% 命中率, "
                    f"{s.get('avg_return', 0):+.3f}% 報酬, {s.get('total_trades', 0)} 筆"
                )

    # 日報
    report_file = DATA / "daily_report.json"
    if report_file.exists():
        with open(report_file, encoding='utf-8') as f:
            report = json.load(f)
        context_parts.append(f"\n=== 最新日報 ({report.get('date', '?')}) ===")
        context_parts.append(f"推文數: {report.get('posts_today', 0)}")
        context_parts.append(f"信號: {', '.join(report.get('signals_detected', []))}")
        direction = report.get('direction_summary', {})
        context_parts.append(f"共識: {direction.get('consensus', '?')} "
                           f"(多{direction.get('LONG', 0)} / 空{direction.get('SHORT', 0)})")

    # 信號信心度
    sc_file = DATA / "signal_confidence.json"
    if sc_file.exists():
        with open(sc_file, encoding='utf-8') as f:
            sc = json.load(f)
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
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))

        elif self.path == '/insights' or self.path == '/insights.html':
            # 群眾洞見頁面
            insights_file = BASE / 'public' / 'insights.html'
            if insights_file.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(insights_file.read_bytes())
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
            # 公開端點：今日信號
            report = _load('daily_report.json') or {}
            self._json_response(200, {
                'date': report.get('date', '?'),
                'signals': report.get('signals_detected', []),
                'posts': report.get('posts_today', 0),
                'consensus': report.get('direction_summary', {}).get('consensus', '?'),
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

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
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

            # 如果有洞見，存的時候帶匿名 ID
            if '[💡用戶洞見]' in reply or '[用戶洞見]' in reply:
                _save_crowd_insight(user_msg, reply, anon)

            self._json_response(200, {
                'reply': reply,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'anon_id': anon[:4] + '****',
                'quota': quota,
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
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
        print("\n停止。")
        server.server_close()
