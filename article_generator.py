#!/usr/bin/env python3
"""
川普密碼 — 三語文章生成器
吃當天的推文 + daily_report.json → LLM 產出 zh/en/ja 三篇分析文章

用法：
  python3 article_generator.py                    # 用今天的資料
  python3 article_generator.py --date 2026-03-20  # 指定日期
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# washin_llm 共用 LLM 模組（本機 Opus + Gemini 3 Key）
sys.path.insert(0, str(Path.home() / "Projects" / "washin-llm"))
try:
    from washin_llm import call_ai as _washin_call_ai
    HAS_WASHIN_LLM = True
except ImportError:
    HAS_WASHIN_LLM = False

BASE = Path(__file__).parent
DATA = BASE / "data"
ARTICLES = BASE / "articles"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def call_llm(prompt: str, max_tokens: int = 2000) -> str:
    """呼叫 LLM — 走 washin_llm 共用模組（本機 Opus → Gemini fallback）。"""
    if HAS_WASHIN_LLM:
        result = _washin_call_ai(prompt, max_tokens=max_tokens, temperature=0.4)
        if result.ok:
            return result.text
        raise RuntimeError(f"washin_llm 失敗: {result.error}")

    # washin_llm 沒裝時的 fallback：直接用 claude -p
    import subprocess
    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            text = data.get("result", "")
            if text:
                return text
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    raise RuntimeError("所有 LLM 都失敗")


def load_today_data(target_date: str):
    """載入指定日期的推文和報告"""
    # 從 multi_source_fetcher 抓最新資料
    try:
        from multi_source_fetcher import fetch_all_sources
        all_posts, _ = fetch_all_sources()
    except:
        all_posts = []

    # 過濾當天
    day_posts = [p for p in all_posts if p.get("created_at", "").startswith(target_date)]

    # 讀 daily_report
    report_path = DATA / "daily_report.json"
    report = {}
    if report_path.exists():
        with open(report_path) as f:
            report = json.load(f)

    return day_posts, report


def build_prompt(lang: str, posts: list, report: dict, target_date: str) -> str:
    """建構 LLM prompt"""
    # 整理推文摘要
    posts_text = ""
    for i, p in enumerate(posts[:30], 1):
        time = p.get("created_at", "")[:16]
        content = p.get("content", "")[:200]
        posts_text += f"{i}. [{time}] {content}\n"

    if not posts_text:
        posts_text = "(今天尚無推文)"

    # 信號摘要
    signals = report.get("signals_detected", [])
    consensus = report.get("direction_summary", {}).get("consensus", "N/A")
    hit_rate = report.get("historical_hit_rate", {}).get("rate", "N/A")
    n_posts = report.get("posts_today", len(posts))

    lang_config = {
        "zh": {
            "instruction": "你是「川普密碼」的分析師。用繁體中文寫一篇給台灣投資人看的每日分析。語氣專業但不死板，像一個懂市場的朋友在跟你聊天。",
            "audience": "台灣投資人，關心美股、台股連動、匯率影響",
            "format": "標題用「川普密碼｜日報」開頭",
        },
        "en": {
            "instruction": "You are the 'Trump Code' analyst. Write a daily analysis for Western traders. Professional, data-driven, concise. Reference Polymarket odds when relevant.",
            "audience": "US/EU traders interested in S&P 500, prediction markets, and political signals",
            "format": "Title starts with 'Trump Code | Daily'",
        },
        "ja": {
            "instruction": "あなたは「トランプ・コード」のアナリストです。日本の投資家向けに日次分析を書いてください。丁寧だが簡潔に。日経平均・為替への影響を意識してください。",
            "audience": "日本の個人投資家。日経平均、ドル円、地政学リスクに関心",
            "format": "タイトルは「トランプ・コード｜日報」で始める",
        },
    }

    cfg = lang_config[lang]

    return f"""{cfg['instruction']}

日期：{target_date}
今日推文數：{n_posts}
偵測信號：{', '.join(signals) if signals else 'None'}
模型共識：{consensus}
歷史命中率：{hit_rate}%

今日推文：
{posts_text}

目標讀者：{cfg['audience']}

請產出一篇 300-500 字的分析文章，包含：
1. 今日重點（川普在說什麼、語氣如何）
2. 信號解讀（對市場的潛在影響）
3. 趨勢觀察（跟前幾天比有什麼變化）
4. 一句話結論

格式：{cfg['format']}
用 Markdown 格式輸出。不要加 ```markdown 標記。
"""


def generate_articles(target_date: str = None):
    """生成三語文章"""
    if not target_date:
        target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    log(f"📝 生成 {target_date} 三語文章")

    posts, report = load_today_data(target_date)
    log(f"   推文：{len(posts)} 篇")

    # 建目錄
    month = target_date[:7]  # 2026-03
    day = target_date[8:]    # 20
    article_dir = ARTICLES / month
    article_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for lang in ["zh", "en", "ja"]:
        log(f"   [{lang}] 呼叫 LLM...")
        try:
            prompt = build_prompt(lang, posts, report, target_date)
            article = call_llm(prompt)

            # 存檔
            out_path = article_dir / f"{day}-{lang}.md"
            out_path.write_text(article, encoding="utf-8")
            results[lang] = {"status": "ok", "path": str(out_path), "length": len(article)}
            log(f"   [{lang}] ✅ {len(article)} 字 → {out_path}")
        except Exception as e:
            results[lang] = {"status": "error", "error": str(e)}
            log(f"   [{lang}] ❌ {e}")

    # 存 metadata
    meta = {
        "date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "posts_count": len(posts),
        "articles": results,
    }
    (article_dir / f"{day}-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    log(f"✅ 完成：{article_dir}")
    return meta


# __main__ 移到檔案最底部


def update_index():
    """更新文章索引（給主站用）"""
    dates = set()
    for month_dir in ARTICLES.iterdir():
        if month_dir.is_dir() and month_dir.name[:4].isdigit():
            for f in month_dir.iterdir():
                if f.name.endswith("-zh.md"):
                    day = f.name.split("-")[0]
                    dates.add(f"{month_dir.name}-{day}")
    dates = sorted(dates, reverse=True)
    index_path = ARTICLES / "index.json"
    index_path.write_text(json.dumps(dates, ensure_ascii=False, indent=2))
    log(f"📋 索引更新：{len(dates)} 篇")
    return dates


def publish_to_devto(date: str, lang: str = "zh"):
    """發布到 Dev.to（單篇）"""
    month = date[:7]
    day = date[8:]
    article_path = ARTICLES / month / f"{day}-{lang}.md"
    if not article_path.exists():
        log(f"Dev.to: {article_path} 不存在")
        return

    content = article_path.read_text(encoding="utf-8")

    # Dev.to API key
    devto_key = os.environ.get("DEVTO_API_KEY", "")
    if not devto_key:
        env_path = Path.home() / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DEVTO_API_KEY="):
                    devto_key = line.split("=", 1)[1].strip().strip('"')
                    break
    if not devto_key:
        log("Dev.to: 無 API key，跳過")
        return

    # 標題和 tags 依語言
    lang_config = {
        "zh": {"title_prefix": "川普密碼｜", "tags": ["trump", "investing", "ai", "chinese"]},
        "en": {"title_prefix": "Trump Code | ", "tags": ["trump", "investing", "ai", "stockmarket"]},
        "ja": {"title_prefix": "トランプ・コード｜", "tags": ["trump", "investing", "ai", "japanese"]},
    }
    cfg = lang_config.get(lang, lang_config["en"])

    title = f"{cfg['title_prefix']}Daily Analysis {date}"
    body = content + f"\n\n---\n\n🔗 [Full dashboard](https://trumpcode.washinmura.jp/) | [All articles](https://trumpcode.washinmura.jp/daily.html)"

    payload = json.dumps({
        "article": {
            "title": title,
            "body_markdown": body,
            "published": True,
            "tags": cfg["tags"],
            "series": cfg["title_prefix"].strip("｜| "),
        }
    }).encode()

    req = urllib.request.Request(
        "https://dev.to/api/articles",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "api-key": devto_key,
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        log(f"Dev.to [{lang}]: ✅ 發布成功 → {result.get('url', '?')}")
    except Exception as e:
        log(f"Dev.to [{lang}]: ❌ {e}")


def generate_flash(post: dict, signals: list, direction: str, confidence: float):
    """即時快報：川普發新文 → 馬上生成三語短分析（150-300 字）。

    跟 generate_articles() 的差異：
    - 只分析單篇推文（不是整天）
    - 更短（150-300 字 vs 300-500 字）
    - 強調即時性（剛發生的事）
    - 檔名用 {day}-flash-{時分}-{lang}.md
    """
    now = datetime.now(timezone.utc)
    target_date = now.strftime("%Y-%m-%d")
    month = target_date[:7]
    day = target_date[8:]
    hm = now.strftime("%H%M")

    article_dir = ARTICLES / month
    article_dir.mkdir(parents=True, exist_ok=True)

    content = post.get("content", "")[:500]
    sig_str = ", ".join(s.get("type", "?") for s in signals)

    # 如果 LLM 有因果推理，附上
    causal = ""
    for s in signals:
        if s.get("reasoning"):
            causal += f"\n- {s['type']}: {s['reasoning']}"
        if s.get("causal_chain"):
            causal += f"\n因果鏈: {s['causal_chain']}"

    lang_config = {
        "zh": {
            "instruction": "你是「川普密碼」的即時分析師。用繁體中文寫一篇 150-300 字的即時快報。語氣像一個懂市場的朋友在第一時間跟你說重要的事。",
            "audience": "台灣投資人，想知道川普剛說了什麼、對市場有什麼影響",
            "format": "標題用「⚡ 川普密碼｜即時快報」開頭",
        },
        "en": {
            "instruction": "You are the 'Trump Code' flash analyst. Write a 150-300 word flash report. Concise, urgent, data-driven.",
            "audience": "US/EU traders who need to know what Trump just said and how it might move markets",
            "format": "Title starts with '⚡ Trump Code | Flash'",
        },
        "ja": {
            "instruction": "あなたは「トランプ・コード」の速報アナリストです。150-300字の速報を書いてください。簡潔で緊急性を感じる文体で。",
            "audience": "日本の個人投資家。トランプの発言が日経平均・ドル円にどう影響するか知りたい",
            "format": "タイトルは「⚡ トランプ・コード｜速報」で始める",
        },
    }

    results = {}
    for lang in ["zh", "en", "ja"]:
        cfg = lang_config[lang]
        prompt = f"""{cfg['instruction']}

川普剛發了這則推文：
---
{content}
---

偵測到的信號：{sig_str}
預測方向：{direction}（信心度 {confidence:.0%}）
{f"AI 因果分析：{causal}" if causal else ""}

目標讀者：{cfg['audience']}

請產出一篇即時快報（150-300 字），包含：
1. 剛說了什麼（一句話）
2. 為什麼重要（市場影響）
3. 建議關注什麼（具體指標）

格式：{cfg['format']}
用 Markdown 格式輸出。不要加 ```markdown 標記。"""

        log(f"   [flash-{lang}] 呼叫 LLM...")
        try:
            article = call_llm(prompt, max_tokens=1000)
            out_path = article_dir / f"{day}-flash-{hm}-{lang}.md"
            out_path.write_text(article, encoding="utf-8")
            results[lang] = {"status": "ok", "path": str(out_path), "length": len(article)}
            log(f"   [flash-{lang}] ✅ {len(article)} 字 → {out_path}")
        except Exception as e:
            results[lang] = {"status": "error", "error": str(e)}
            log(f"   [flash-{lang}] ❌ {e}")

    # 存 metadata
    meta = {
        "type": "flash",
        "date": target_date,
        "generated_at": now.isoformat(),
        "post_id": post.get("id", ""),
        "post_content": content[:200],
        "signals": sig_str,
        "direction": direction,
        "confidence": confidence,
        "articles": results,
    }
    meta_path = article_dir / f"{day}-flash-{hm}-meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    # 更新索引
    update_index()
    return meta


def full_pipeline(target_date: str = None):
    """完整管線：生成文章 + 更新索引 + 發布 Dev.to"""
    meta = generate_articles(target_date)
    update_index()

    # Dev.to 三語都發
    actual_date = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for lang in ["zh", "en", "ja"]:
        if meta.get("articles", {}).get(lang, {}).get("status") == "ok":
            publish_to_devto(actual_date, lang)

    return meta


if __name__ == "__main__":
    date = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--date" and i + 1 < len(sys.argv) - 1:
            date = sys.argv[i + 2]
    full_pipeline(date)
