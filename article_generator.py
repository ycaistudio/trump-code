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
    except Exception:
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


def build_prompt(lang: str, posts: list, report: dict, target_date: str):
    """建構 LLM prompt。回傳 (prompt_str, source_links)。"""
    # 整理推文摘要（含原文連結）
    posts_text = ""
    source_links = []
    for i, p in enumerate(posts[:30], 1):
        time = p.get("created_at", "")[:16]
        content = p.get("content", "")[:200]
        pid = p.get("id", "")
        url = p.get("original_url", "")
        if not url and pid:
            url = f"https://truthsocial.com/@realDonaldTrump/posts/{pid}"
        posts_text += f"{i}. [{time}] {content}\n"
        if url:
            posts_text += f"   原文：{url}\n"
            source_links.append(url)

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
1. 今日重點（川普在說什麼、語氣如何，引用原文關鍵句）
2. 信號解讀（對市場的潛在影響，帶具體數字）
3. 趨勢觀察（跟前幾天比有什麼變化）
4. 一句話結論

重要：文章中必須引用川普的原文關鍵句（用引號標示）。

格式：{cfg['format']}
用 Markdown 格式輸出。不要加 ```markdown 標記。
不要在文章末尾加出處區塊（系統會自動附上）。
""", source_links


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

    # 三語並行生成（從 45 秒縮到 15 秒）
    from concurrent.futures import ThreadPoolExecutor

    # 先組一次 prompt 拿 source_links（三語共用同一份連結）
    _, all_source_links = build_prompt("en", posts, report, target_date)
    links_text = "\n".join(f"  - {url}" for url in all_source_links[:30]) if all_source_links else "  - (無原文連結)"

    # 每日文章出處區塊（公定規格）
    signals = report.get("signals_detected", [])
    consensus = report.get("direction_summary", {}).get("consensus", "N/A")
    daily_provenance = {
        "zh": f"""
---
**📋 出處與方法**
- 原文來源：Truth Social (@realDonaldTrump)
- 當日推文數：{len(posts)} 篇
- 原文連結：
{links_text}
- 信號：{', '.join(signals) if signals else '無'} | 模型共識：{consensus}
- 分析引擎：Trump Code AI（Claude Opus / Gemini Flash）
- 信號偵測：基於 7,400+ 篇推文訓練的 551 條規則，z=5.39
- 分析方法：NLP 關鍵字分類 → LLM 因果推理 → 信心度評分
- 資料集：trumpcode.washinmura.jp/api/data
- 原始碼：github.com/sstklen/trump-code
""",
        "en": f"""
---
**📋 Sources & Methodology**
- Source: Truth Social (@realDonaldTrump)
- Posts analyzed: {len(posts)}
- Source URLs:
{links_text}
- Signals: {', '.join(signals) if signals else 'None'} | Consensus: {consensus}
- Analysis engine: Trump Code AI (Claude Opus / Gemini Flash)
- Signal detection: 551 validated rules from 7,400+ posts (z=5.39)
- Method: NLP keyword classification → LLM causal reasoning → confidence scoring
- Dataset: trumpcode.washinmura.jp/api/data
- Open source: github.com/sstklen/trump-code
""",
        "ja": f"""
---
**📋 出典・分析手法**
- 原文：Truth Social (@realDonaldTrump)
- 本日の投稿数：{len(posts)} 件
- 原文リンク：
{links_text}
- シグナル：{', '.join(signals) if signals else 'なし'} | コンセンサス：{consensus}
- 分析エンジン：Trump Code AI（Claude Opus / Gemini Flash）
- シグナル検出：7,400件以上の投稿から検証済み551ルール（z=5.39）
- 手法：NLPキーワード分類 → LLM因果推論 → 信頼度スコアリング
- データセット：trumpcode.washinmura.jp/api/data
- オープンソース：github.com/sstklen/trump-code
""",
    }

    def _gen_one(lang):
        log(f"   [{lang}] 呼叫 LLM...")
        try:
            prompt, _ = build_prompt(lang, posts, report, target_date)
            article = call_llm(prompt)
            # 自動附出處區塊（公定規格）
            article = article.rstrip() + "\n" + daily_provenance[lang]
            out_path = article_dir / f"{day}-{lang}.md"
            out_path.write_text(article, encoding="utf-8")
            log(f"   [{lang}] ✅ {len(article)} 字 → {out_path}")
            return lang, {"status": "ok", "path": str(out_path), "length": len(article)}
        except Exception as e:
            log(f"   [{lang}] ❌ {e}")
            return lang, {"status": "error", "error": str(e)}

    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for lang, result in pool.map(_gen_one, ["zh", "en", "ja"]):
            results[lang] = result

    # 存 metadata（含完整出處 + Article Schema for SEO/AEO — 公定規格）
    meta = {
        "date": target_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "posts_count": len(posts),
        "source_urls": all_source_links,
        "analysis_engine": "Trump Code AI (Claude Opus / Gemini Flash)",
        "analysis_method": "NLP keyword classification → LLM causal reasoning → confidence scoring",
        "rules_base": "551 validated rules from 7,400+ posts (z=5.39)",
        "articles": results,
        "schema": {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": f"Trump Code Daily Analysis {target_date}",
            "alternativeHeadline": f"川普密碼日報 {target_date}",
            "datePublished": target_date,
            "dateModified": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "author": {"@type": "Organization", "name": "Washin Mura (和心村)", "url": "https://washinmura.jp"},
            "publisher": {"@type": "Organization", "name": "TRUMP CODE", "url": "https://trumpcode.washinmura.jp"},
            "inLanguage": ["zh-TW", "en", "ja"],
            "url": f"https://trumpcode.washinmura.jp/daily.html?date={target_date}",
            "isAccessibleForFree": True,
            "about": "AI analysis of Trump social media posts and stock market impact",
        },
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


def notify_indexnow(urls: list[str]):
    """通知 Bing/Yandex IndexNow 有新頁面（即時索引）。"""
    # IndexNow key — 放在 public/.well-known/ 下驗證
    key = os.environ.get("INDEXNOW_KEY", "trumpcode2026washinmura")
    payload = json.dumps({
        "host": "trumpcode.washinmura.jp",
        "key": key,
        "urlList": urls,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.indexnow.org/IndexNow",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        log(f"🔔 IndexNow: {resp.status} — {len(urls)} URL 已通知")
    except Exception as e:
        log(f"⚠️ IndexNow 失敗（不影響發布）: {e}")


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
    post_url = post.get("original_url", "")
    post_source = post.get("source", "truthsocial")  # "truthsocial" 或 "x"
    post_id = post.get("id", "")
    pub_date = post.get("pub_date", "")
    sig_str = ", ".join(s.get("type", "?") for s in signals)

    # 如果沒有 original_url，自己組
    if not post_url:
        if post_source == "x":
            post_url = f"https://x.com/realDonaldTrump/status/{post_id.replace('x_', '')}"
        else:
            post_url = f"https://truthsocial.com/@realDonaldTrump/posts/{post_id}"

    # 如果 LLM 有因果推理，附上
    causal = ""
    for s in signals:
        if s.get("reasoning"):
            causal += f"\n- {s['type']}: {s['reasoning']}"
        if s.get("causal_chain"):
            causal += f"\n因果鏈: {s['causal_chain']}"

    # 出處區塊（每篇文章底部必帶）
    source_platform = "Truth Social" if post_source != "x" else "X (@realDonaldTrump)"
    provenance_zh = f"""
---
**📋 出處與方法**
- 原文來源：{source_platform}
- 原文連結：{post_url}
- 發文時間：{pub_date}
- 分析引擎：Trump Code AI（Claude Opus / Gemini Flash）
- 信號偵測：基於 7,400+ 篇推文訓練的 551 條規則，z=5.39
- 分析方法：NLP 關鍵字分類 → LLM 因果推理 → 信心度評分
- 資料集：trumpcode.washinmura.jp/api/data
- 原始碼：github.com/sstklen/trump-code
"""
    provenance_en = f"""
---
**📋 Sources & Methodology**
- Original post: {source_platform}
- Source URL: {post_url}
- Posted: {pub_date}
- Analysis engine: Trump Code AI (Claude Opus / Gemini Flash)
- Signal detection: 551 validated rules from 7,400+ posts (z=5.39)
- Method: NLP keyword classification → LLM causal reasoning → confidence scoring
- Dataset: trumpcode.washinmura.jp/api/data
- Open source: github.com/sstklen/trump-code
"""
    provenance_ja = f"""
---
**📋 出典・分析手法**
- 原文：{source_platform}
- リンク：{post_url}
- 投稿日時：{pub_date}
- 分析エンジン：Trump Code AI（Claude Opus / Gemini Flash）
- シグナル検出：7,400件以上の投稿から検証済み551ルール（z=5.39）
- 手法：NLPキーワード分類 → LLM因果推論 → 信頼度スコアリング
- データセット：trumpcode.washinmura.jp/api/data
- オープンソース：github.com/sstklen/trump-code
"""

    lang_config = {
        "zh": {
            "instruction": "你是「川普密碼」的即時分析師。用繁體中文寫一篇 150-300 字的即時快報。語氣像一個懂市場的朋友在第一時間跟你說重要的事。",
            "audience": "台灣投資人，想知道川普剛說了什麼、對市場有什麼影響",
            "format": "標題用「⚡ 川普密碼｜即時快報」開頭",
            "provenance": provenance_zh,
        },
        "en": {
            "instruction": "You are the 'Trump Code' flash analyst. Write a 150-300 word flash report. Concise, urgent, data-driven.",
            "audience": "US/EU traders who need to know what Trump just said and how it might move markets",
            "format": "Title starts with '⚡ Trump Code | Flash'",
            "provenance": provenance_en,
        },
        "ja": {
            "instruction": "あなたは「トランプ・コード」の速報アナリストです。150-300字の速報を書いてください。簡潔で緊急性を感じる文体で。",
            "audience": "日本の個人投資家。トランプの発言が日経平均・ドル円にどう影響するか知りたい",
            "format": "タイトルは「⚡ トランプ・コード｜速報」で始める",
            "provenance": provenance_ja,
        },
    }

    # 三語並行生成（從 45 秒縮到 15 秒）
    from concurrent.futures import ThreadPoolExecutor

    def _gen_flash_one(lang):
        cfg = lang_config[lang]
        prompt = f"""{cfg['instruction']}

川普剛發了這則推文：
---
{content}
---
原文來源：{source_platform}
原文連結：{post_url}
發文時間：{pub_date}

偵測到的信號：{sig_str}
預測方向：{direction}（信心度 {confidence:.0%}）
分析方法：NLP 關鍵字分類（551 條驗證規則，z=5.39）→ LLM 因果推理 → 信心度評分
{f"AI 因果分析：{causal}" if causal else ""}

目標讀者：{cfg['audience']}

請產出一篇即時快報（150-300 字），包含：
1. 川普說了什麼（引用原文關鍵句）
2. 為什麼重要（市場影響 + 具體指標）
3. 建議關注什麼（具體數字/指標/時間）

重要：文章中必須引用原文關鍵句（用引號標示），並提到信號偵測結果和信心度數字。

格式：{cfg['format']}
用 Markdown 格式輸出。不要加 ```markdown 標記。
不要在文章末尾加出處區塊（系統會自動附上）。"""

        log(f"   [flash-{lang}] 呼叫 LLM...")
        try:
            article = call_llm(prompt, max_tokens=1000)
            # 自動附上出處區塊（公定規格）
            article = article.rstrip() + "\n" + cfg["provenance"]
            out_path = article_dir / f"{day}-flash-{hm}-{lang}.md"
            out_path.write_text(article, encoding="utf-8")
            log(f"   [flash-{lang}] ✅ {len(article)} 字 → {out_path}")
            return lang, {"status": "ok", "path": str(out_path), "length": len(article)}
        except Exception as e:
            log(f"   [flash-{lang}] ❌ {e}")
            return lang, {"status": "error", "error": str(e)}

    results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for lang, result in pool.map(_gen_flash_one, ["zh", "en", "ja"]):
            results[lang] = result

    # 存 metadata（含完整出處 — 公定規格）
    meta = {
        "type": "flash",
        "date": target_date,
        "generated_at": now.isoformat(),
        "post_id": post_id,
        "post_content": content[:200],
        "post_url": post_url,
        "post_source": source_platform,
        "post_time": pub_date,
        "signals": sig_str,
        "direction": direction,
        "confidence": confidence,
        "analysis_engine": "Trump Code AI (Claude Opus / Gemini Flash)",
        "analysis_method": "NLP keyword classification → LLM causal reasoning → confidence scoring",
        "rules_base": "551 validated rules from 7,400+ posts (z=5.39)",
        "articles": results,
    }
    meta_path = article_dir / f"{day}-flash-{hm}-meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    # 更新索引
    update_index()
    return meta


def full_pipeline(target_date: str = None):
    """完整管線：生成文章 + 更新索引 + 發布 Dev.to + IndexNow 通知"""
    meta = generate_articles(target_date)
    update_index()

    actual_date = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Dev.to 三語都發
    for lang in ["zh", "en", "ja"]:
        if meta.get("articles", {}).get(lang, {}).get("status") == "ok":
            publish_to_devto(actual_date, lang)

    # IndexNow — 通知搜尋引擎有新文章
    notify_indexnow([
        f"https://trumpcode.washinmura.jp/daily.html?date={actual_date}",
        "https://trumpcode.washinmura.jp/daily.html",
    ])

    return meta


if __name__ == "__main__":
    date = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--date" and i + 1 < len(sys.argv) - 1:
            date = sys.argv[i + 2]
    full_pipeline(date)
