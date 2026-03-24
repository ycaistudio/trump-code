"""X (Twitter) 自動發推模組 — @trumpcodeai

川普發文 → 快報生成 → 自動發一則摘要推文到 @trumpcodeai。

用法：
  from x_poster import post_tweet, post_flash_summary
  post_tweet("Hello from Trump Code!")
  post_flash_summary(meta)  # 從快報 meta 自動組文案

不需要外部套件 — 用 Python 標準庫實作 OAuth 1.0a 簽名。
"""

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path


# === Key 讀取（從 .env 或環境變數）===

def _load_env():
    """從 .env 讀 key（LaunchAgent 環境可能沒有）。"""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip()

_load_env()

API_KEY = os.environ.get("X_API_KEY", "")
API_SECRET = os.environ.get("X_API_SECRET", "")
ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "")
ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET", "")


# === OAuth 1.0a 簽名（純標準庫）===

def _percent_encode(s: str) -> str:
    """RFC 3986 percent-encoding。"""
    return urllib.parse.quote(str(s), safe="")


def _oauth_signature(method: str, url: str, params: dict, body_params: dict = None) -> str:
    """產生 OAuth 1.0a HMAC-SHA1 簽名。"""
    # 合併所有參數（OAuth params + body params）
    all_params = dict(params)
    if body_params:
        all_params.update(body_params)

    # 按 key 排序，組成 parameter string
    param_str = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(all_params.items())
    )

    # Signature Base String
    base_str = f"{method.upper()}&{_percent_encode(url)}&{_percent_encode(param_str)}"

    # Signing Key
    signing_key = f"{_percent_encode(API_SECRET)}&{_percent_encode(ACCESS_TOKEN_SECRET)}"

    # HMAC-SHA1
    sig = hmac.new(
        signing_key.encode("utf-8"),
        base_str.encode("utf-8"),
        hashlib.sha1,
    ).digest()

    return b64encode(sig).decode("utf-8")


def _oauth_header(method: str, url: str, body_params: dict = None) -> str:
    """產生完整的 OAuth Authorization header。"""
    oauth_params = {
        "oauth_consumer_key": API_KEY,
        "oauth_nonce": b64encode(os.urandom(32)).decode("utf-8").rstrip("="),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": ACCESS_TOKEN,
        "oauth_version": "1.0",
    }

    # 計算簽名
    oauth_params["oauth_signature"] = _oauth_signature(method, url, oauth_params, body_params)

    # 組成 Authorization header
    auth_str = ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )

    return f"OAuth {auth_str}"


# === 發推 ===

def post_tweet(text: str) -> dict:
    """發一則推文到 @trumpcodeai。

    Args:
        text: 推文內容（最多 280 字元）

    Returns:
        {"ok": True, "tweet_id": "...", "url": "..."} 或
        {"ok": False, "error": "..."}
    """
    if not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET]):
        return {"ok": False, "error": "X API key 未設定（檢查 .env）"}

    # X API v2 發推 endpoint
    url = "https://api.x.com/2/tweets"
    body = json.dumps({"text": text[:280]}).encode("utf-8")

    auth = _oauth_header("POST", url)

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": auth,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            tweet_id = data.get("data", {}).get("id", "")
            return {
                "ok": True,
                "tweet_id": tweet_id,
                "url": f"https://x.com/trumpcodeai/status/{tweet_id}",
            }
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:300]
        return {"ok": False, "error": f"HTTP {e.code}: {err}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def post_flash_summary(meta: dict) -> dict:
    """從快報 meta 自動組文案並發推。

    格式：
    ⚡ Trump Code | Flash
    {方向} {信號}
    {推文內容前 80 字}
    🔗 trumpcode.washinmura.jp/daily.html
    """
    direction = meta.get("direction", "NEUTRAL")
    signals = meta.get("signals", "")
    content = meta.get("post_content", "")[:80]
    date = meta.get("date", "")

    # 方向 emoji
    dir_emoji = {"UP": "📈", "DOWN": "📉", "NEUTRAL": "➡️"}.get(direction, "➡️")

    text = f"⚡ Trump Code | Flash\n{dir_emoji} {direction}"
    if signals:
        text += f" [{signals}]"
    text += f"\n\n{content}"
    if len(content) >= 78:
        text += "..."
    text += f"\n\n🔗 trumpcode.washinmura.jp/daily.html?date={date}"

    return post_tweet(text)


def post_daily_summary(date: str, posts_count: int, signals: list = None) -> dict:
    """每日日報發推摘要。

    格式：
    📊 Trump Code | Daily {date}
    {N} posts analyzed in 3 languages
    {信號列表}
    🔗 trumpcode.washinmura.jp/daily.html?date={date}
    """
    sig_str = ", ".join(signals) if signals else "No major signals"

    text = (
        f"📊 Trump Code | Daily {date}\n"
        f"{posts_count} posts analyzed in zh/en/ja\n"
        f"Signals: {sig_str}\n\n"
        f"🔗 trumpcode.washinmura.jp/daily.html?date={date}"
    )

    return post_tweet(text)


# === CLI 測試 ===

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # 測試推文（會真的發出去）
        result = post_tweet("🧪 Trump Code system test. Ignore this tweet.")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        print(f"API_KEY: {'✅' if API_KEY else '❌'} {API_KEY[:8]}..." if API_KEY else "❌ 未設定")
        print(f"API_SECRET: {'✅' if API_SECRET else '❌'}")
        print(f"ACCESS_TOKEN: {'✅' if ACCESS_TOKEN else '❌'}")
        print(f"ACCESS_TOKEN_SECRET: {'✅' if ACCESS_TOKEN_SECRET else '❌'}")
    else:
        print("用法: python3 x_poster.py status  — 查 key 狀態")
        print("      python3 x_poster.py test    — 發測試推文")
