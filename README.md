<div align="center">

# 🔐 TRUMP CODE

**AI-powered cryptanalysis of presidential communications × stock market impact.**

### 🌐 [→ trumpcode.washinmura.jp ← LIVE DASHBOARD](https://trumpcode.washinmura.jp)

[![Live Dashboard](https://img.shields.io/badge/🌐_LIVE_DASHBOARD-trumpcode.washinmura.jp-FFD700?style=for-the-badge&labelColor=FFD700&logoColor=black)](https://trumpcode.washinmura.jp)
[![GitHub Stars](https://img.shields.io/github/stars/sstklen/trump-code?style=for-the-badge&logo=github&color=FFD700)](https://github.com/sstklen/trump-code)

[![Buy Me a Claude Max](https://img.shields.io/badge/☕_Buy_Me_a_Claude_Max_→_Support_This_Project-FFD700?style=for-the-badge&labelColor=FFD700&logoColor=black)](https://buy.stripe.com/5kQ6oI8Wk2Ui6Q3aww4c80r)

[![Models Tested](https://img.shields.io/badge/Models_Tested-31.5M-FF0000?style=flat-square)](data/surviving_rules.json)
[![Survivors](https://img.shields.io/badge/Survivors-551-00C853?style=flat-square)](data/surviving_rules.json)
[![Hit Rate](https://img.shields.io/badge/Hit_Rate-61.3%25-FFD700?style=flat-square)](data/predictions_log.json)
[![Verified](https://img.shields.io/badge/Verified-566_predictions-2962FF?style=flat-square)](data/predictions_log.json)
[![Open Data](https://img.shields.io/badge/Data-100%25_Open-FF6F00?style=flat-square)](data/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

*Can you decode the President's posts before the market moves?*

📰 **[ABMedia: 把川普納入投資模型？台灣創業家開源「Trump Code」分析美股，命中率超過六成](https://abmedia.io/tk-lin-donald-trump-code-trade)**

[中文版](docs/README.zh.md) · [日本語版](docs/README.ja.md)

</div>

---

## What Is This?

Trump is the only person on Earth who can move global markets with a single social media post. This project applies brute-force computation to find statistically significant patterns between his posting behavior and stock market movements.

**Not gut feeling. Not opinion. Pure data.**

- **7,400+ Truth Social posts** analyzed (3 independent sources, cross-verified)
- **31.5 million model combinations** tested via brute-force search
- **551 surviving rules** that passed train/test validation
- **61.3% hit rate** across 566 verified predictions (z=5.39, p<0.05)
- Closed-loop system: predict → verify → learn → evolve → repeat daily

## Key Discoveries

| # | Finding | Evidence | Impact |
|---|---------|----------|--------|
| 1 | **Pre-market RELIEF = strongest buy signal** | Apr 9, 2025: S&P +9.52% | Avg +1.12% same-day |
| 2 | **TARIFF→SHORT is 70% wrong** | Circuit breaker analysis | Auto-reversed to LONG |
| 3 | **China signals hidden on Truth Social only** | 203 TS posts / 0 on X | 1.5x weight boost |
| 4 | **Truth Social publishes 6.2h before X** | 38/39 posts matched | 6-hour trading window |
| 5 | **Pure tariff day = most dangerous** | Apr 3: -4.84%, Apr 4: -5.97% | Avg -1.057% |
| 6 | **4 signals combo = most profitable** | 12 occurrences, 66.7% up | Avg +2.792% |
| 7 | **Silence = 80% bullish** | Zero-post days analysis | Avg +0.409% |
| 8 | **Late-night tariff tweets = anti-indicator** | 62% wrong → reverse = 62% right | Auto-inverted |

## System Architecture

```
Trump posts on Truth Social
         │
         ▼ (detected every 5 min)
┌─────────────────────────────────────────────────────┐
│  Real-Time Engine                                    │
│  Detect → Classify signals → Dual-platform boost →   │
│  Event pattern check → Snapshot PM + S&P 500 →       │
│  Predict → Track at 1h/3h/6h → Verify               │
└─────────────────────────┬───────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────┐
│  Daily Pipeline (11 steps)                           │
│  Fetch → Analyze → Run 551 rules → Predict →         │
│  Verify → Circuit Breaker → Prediction Market check → │
│  Learn (promote/demote/eliminate) →                   │
│  Evolve (crossover/mutation/distill) →                │
│  AI Briefing → Sync to GitHub                        │
└─────────────────────────┬───────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────┐
│  Three Brains                                        │
│  🧠 Opus — deep causal analysis                      │
│  🧬 Evolver — breeds new rules from survivors        │
│  🔒 Circuit Breaker — stops if system degrades       │
└─────────────────────────┬───────────────────────────┘
                          │
┌─────────────────────────┼───────────────────────────┐
│  Output                                              │
│  📊 Dashboard  💬 Chatbot  📡 API  💻 CLI  🤖 MCP   │
└─────────────────────────────────────────────────────┘
```

## Model Leaderboard

11 named strategy models, ranked by verified performance:

| # | Model | Strategy | Hit Rate | Avg Return | Trades |
|---|-------|----------|----------|------------|--------|
| 🥇 | A3 | Pre-market RELIEF → same-day surge | 72.7% | +1.206% | 11 |
| 🥈 | D3 | Post volume spike → panic bottom | 70.2% | +0.306% | 47 |
| 🥉 | D2 | Signature switch → formal statement | 70.0% | +0.472% | 80 |
| 4 | B3 | Pre-market ACTION + positive mood → rise | 66.7% | +0.199% | 33 |
| 5 | C1 | Burst posting → long silence → go long | 65.3% | +0.145% | 176 |
| 6 | B1 | 3 signals combo → buy 3 days | 64.7% | +0.597% | 17 |
| 7 | B2 | 3-day tariff streak → Deal pivot | 57.9% | +0.721% | 19 |
| 8 | A1 | Tariff during market hours → next-day drop | 56.5% | -0.758% | 23 |
| ⚠️ | A2 | DEAL signal → next-day rise | 52.2% | +0.029% | 90 |
| ⚠️ | C2 | Market brag → short-term top | 45.0% | +0.105% | 60 |
| 🗑️ | C3 | Late-night tariff → gap open (anti-indicator) | 37.5% | -0.414% | 8 |

## Dual-Platform Intelligence (Truth Social vs X)

| Finding | Data | Trading Implication |
|---------|------|---------------------|
| China = 100% hidden from X | 203 TS posts / 0 X posts | China signals are more authentic |
| TS publishes 6.2h before X | 38/39 posts matched | 6-hour arbitrage window |
| X posting correlates with market | r=0.35 | He uses X when confident |
| X day returns 7x higher | +0.252% vs +0.037% | X appearance = confirmation signal |

## Prediction Markets Integration

Live tracking of Trump-related prediction markets via [Polymarket](https://polymarket.com/search?_q=trump):

- **316+ active Trump markets** tracked in real-time
- Dual-track snapshots: Polymarket prices + S&P 500 simultaneously
- Signal → market correlation analysis
- [Kalshi](https://kalshi.com) cross-platform spread detection

## Live Dashboard

**[→ trumpcode.washinmura.jp](https://trumpcode.washinmura.jp)**

Real-time dashboard showing:
- Latest Trump posts with signal analysis
- Today's signals and market consensus
- Live Polymarket Trump markets
- Model rankings with performance bars
- 30-second auto-refresh

## API Endpoints

Base URL: `https://trumpcode.washinmura.jp`

| Endpoint | Description |
|----------|-------------|
| `GET /api/dashboard` | All data in one call |
| `GET /api/signals` | Latest signals + 7-day history |
| `GET /api/models` | Model performance rankings |
| `GET /api/status` | System health summary |
| `GET /api/recent-posts` | Latest 20 Trump posts + signal analysis |
| `GET /api/polymarket-trump` | Live Trump prediction markets (316+) |
| `GET /api/playbook` | Three playbooks (hedge/position/pump) |
| `GET /api/insights` | Crowd-sourced trading insights |
| `GET /api/data` | Downloadable dataset catalog |
| `GET /api/data/{file}` | Download raw data files |
| `POST /api/chat` | AI chatbot (Gemini Flash) |

## Open Data

All data is 100% public. Clone and explore:

| File | Description | Updated |
|------|-------------|---------|
| `trump_posts_all.json` | Full Truth Social archive (44,000+ posts) | Daily |
| `trump_posts_lite.json` | Posts with signals pre-tagged | Daily |
| `x_posts_full.json` | X (Twitter) full archive | Daily |
| `predictions_log.json` | 566 verified predictions with outcomes | Daily |
| `surviving_rules.json` | 551 active rules (brute-force + evolved) | Daily |
| `daily_report.json` | Daily trilingual report | Daily |
| `trump_playbook.json` | Three playbooks (hedge/position/pump) | Weekly |
| `signal_confidence.json` | Signal confidence scores (auto-adjusted) | Daily |
| `opus_analysis.json` | Claude Opus deep analysis | On demand |
| `learning_report.json` | Learning engine report | Daily |
| `evolution_log.json` | Rule evolution log (crossover/mutation) | Daily |
| `circuit_breaker_state.json` | System health + error analysis | Daily |
| `daily_features.json` | 384 features × 414 trading days | Daily |
| `market_SP500.json` | S&P 500 OHLC history | Daily |

## Quick Start

```bash
# Clone
git clone https://github.com/sstklen/trump-code.git
cd trump-code
pip install -r requirements.txt

# Check today's signals
python3 trump_code_cli.py signals

# Run any of the 12 analyses
python3 analysis_06_market.py    # Posts vs S&P 500 correlation
python3 analysis_09_combo_score.py  # Multi-signal combo scoring

# Run brute-force model search (~25 min)
python3 overnight_search.py

# Start real-time monitor
python3 realtime_loop.py

# Start web dashboard + chatbot
export GEMINI_KEYS="key1,key2,key3"
python3 chatbot_server.py
# → http://localhost:8888
```

## CLI Commands

```bash
python3 trump_code_cli.py signals    # Today's detected signals
python3 trump_code_cli.py models     # Model performance leaderboard
python3 trump_code_cli.py predict    # LONG/SHORT consensus
python3 trump_code_cli.py arbitrage  # Prediction market opportunities
python3 trump_code_cli.py health     # System health check
python3 trump_code_cli.py report     # Full daily report
python3 trump_code_cli.py json       # All data as JSON
```

## MCP Server (for Claude Code / Cursor)

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "trump-code": {
      "command": "python3",
      "args": ["/path/to/trump-code/mcp_server.py"]
    }
  }
}
```

9 tools: `signals`, `models`, `predict`, `arbitrage`, `health`, `events`, `dual_platform`, `crowd`, `full_report`

## Contributing

**We need the world's eyes on this. One team can't decode Trump alone.**

### Ways to Help

1. **Propose new features** — Open an issue: what to track, why it matters, how to detect it
2. **Run your own analysis** — Clone the data, find patterns, submit a PR
3. **Verify predictions** — Check `daily_report.json` against actual market closes
4. **Share trading logic** — Use the chatbot to share insights (best ones get absorbed)

### Ideas We Haven't Tried

| Idea | Difficulty |
|------|-----------|
| Correlate with Bitcoin/Gold/Oil | Easy |
| Analyze image/video posts | Medium |
| Track which accounts he retweets before big moves | Easy |
| Cross-reference with his public schedule | Medium |
| Detect if posts are written by him vs staff | Hard |
| Analyze deleted posts and edit history | Medium |

## File Structure

```
trump-code/
├── public/insights.html          # Dashboard (single-file, no build)
├── chatbot_server.py             # Web server + all API endpoints
├── realtime_loop.py              # Real-time monitor (every 5 min)
├── daily_pipeline.py             # Daily pipeline (11 steps)
├── learning_engine.py            # Promote/demote/eliminate rules
├── rule_evolver.py               # Crossover/mutation/distill
├── circuit_breaker.py            # System health + auto-pause
├── event_detector.py             # Multi-day event patterns
├── dual_platform_signal.py       # Truth Social vs X analysis
├── polymarket_client.py          # Polymarket API client
├── kalshi_client.py              # Kalshi API client
├── arbitrage_engine.py           # Cross-platform arbitrage
├── mcp_server.py                 # MCP server (9 tools)
├── trump_code_cli.py             # CLI interface
├── trump_monitor.py              # Post monitor
├── analysis_01_caps.py           # CAPS code analysis
├── analysis_02_timing.py         # Posting time patterns
├── analysis_03_hidden.py         # Hidden messages (acrostic)
├── analysis_04_entities.py       # Country & people mentions
├── analysis_05_anomaly.py        # Anomaly detection
├── analysis_06_market.py         # Posts vs S&P 500
├── analysis_07_signal_sequence.py # Signal sequences
├── analysis_08_backtest.py       # Strategy backtesting
├── analysis_09_combo_score.py    # Multi-signal scoring
├── analysis_10_code_change.py    # Signature change detection
├── analysis_11_brute_force.py    # Brute-force rule search
├── analysis_12_big_moves.py      # Big move prediction
├── data/                         # All data (100% open)
└── tests/                        # Test suite
```

## Disclaimer

> **FOR RESEARCH AND EDUCATIONAL PURPOSES ONLY.**
>
> This project is NOT financial advice. Do NOT make investment decisions based on these findings.
>
> **Statistical Limitations:**
> - 31.5 million model combinations tested. Even with train/test validation, surviving models may include false positives due to multiple comparisons (data snooping bias).
> - Past patterns do NOT guarantee future results. Correlation ≠ causation.
> - Trump can change his communication patterns at any time.
>
> **Legal:** The authors assume NO liability for financial losses. Data sourced from public archives. Not affiliated with Truth Social, S&P Global, or any government entity. Not registered with any financial regulatory authority.

---

## Also By Washin Mura | 和心村的其他專案 | 和心村の他プロジェクト

These are the tools we used to build Trump Code — and everything else.

這些是我們打造 Trump Code 和其他所有專案的工具。

Trump Codeを含む全プロジェクトを作るために使ったツールです。

| Project | EN | 中文 | 日本語 | |
|---------|----|----|--------|---|
| **[5x-cto](https://github.com/sstklen/5x-cto)** | Dev pipeline: 1 MAX + 1 Codex = 5x capacity | 開發流水線：一個人當五個用 | 開発パイプライン：1人で5人分 | 🚀 |
| **[YES.md](https://github.com/sstklen/yes.md)** | AI governance: safety gates + evidence rules | AI 治理：安全閘門 + 證據規則 | AIガバナンス：安全ゲート＋証拠ルール | ✅ |
| **[AI.MD](https://github.com/sstklen/ai-md)** | Convert CLAUDE.md to AI-native format | 蒸餾 CLAUDE.md 為 AI 原生格式 | CLAUDE.mdをAIネイティブ形式に | 📝 |
| **[Washin Playbook](https://github.com/sstklen/washin-playbook)** | The full story: 7 chapters, zero coding | 完整故事：7 章，零程式背景 | 全記録：7章、プログラミング経験ゼロ | 📚 |

---

<div align="center">

## 🎮 Prediction Game

**[Play Now → trumpcode.washinmura.jp/game](https://trumpcode.washinmura.jp/game)**

Trump posts → AI predicts market direction → You vote Bull/Bear/Flat → 6 hours later, SPY decides who wins.

Can the crowd beat the AI? 🤖 vs 👥

---

## 🤝 Contributors

Thanks to everyone who helps make Trump Code better!

| Contributor | Contribution |
|-------------|-------------|
| [@yongjer](https://github.com/yongjer) | PyTorch GPU acceleration for brute-force engine — **857x faster** (60s → 0.07s on RTX 4060 Ti) |

Want to contribute? See [CONTRIBUTING.md](CONTRIBUTING.md).

---

Built by **[Washin Mura (和心村)](https://washinmura.jp)** — Boso Peninsula, Japan.

Powered by brute-force computation, not gut feeling.

*If you find patterns we missed, [open an issue](https://github.com/sstklen/trump-code/issues). Let's decode this together.*

⭐ **Star this repo to follow the live decoding.**

</div>
