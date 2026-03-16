# 交接文件
> 日期：2026-03-16 (Session 2 Final) | 摘要：即時引擎三源 + Devvit 預測遊戲 v0.0.10 上線 Reddit

## 已完成

### 即時引擎（VPS 已上線）
- [x] 三源抓取 — CNN + trumpstruth.org + X API，每 5 分鐘掃
- [x] systemd 常駐 — `sudo systemctl status trump-realtime`
- [x] 新推文自動寫入網站 — 44,076 篇
- [x] Polymarket 快照修復 — /public-search API，390 個市場
- [x] 追蹤窗口 48h — 1h/3h/6h/12h/24h/48h
- [x] X API Bearer Token 更新到 VPS .env
- [x] 即時信號接回前端 — 每篇推文卡片有信號標籤
- [x] 遊戲 API 4 端點已部署 — game-signal / game-result / game-leaderboard / game-stats

### Devvit 預測遊戲（Reddit 上線）
- [x] Devvit CLI 安裝 + 登入（PipeAccording5302）
- [x] App 名稱：trumpcodegame，目前 v0.0.10
- [x] 測試 subreddit：r/trumpcodegame_dev — 能建帖、能投票、比例條即時更新
- [x] 正式 subreddit：r/TrumpCodeGame — 已建好，還沒安裝 App
- [x] Server：建帖 + 投票 + 開獎 + 排行榜 + AI vs Crowd（Redis 用 JSON 模擬 Set）
- [x] 前端：投票按鈕 + 比例條 + 倒數 + 結果面板 + 推廣區（可點擊連結）
- [x] 積分系統：猜對 +10、反 AI +25、猜錯 -5、連勝加成
- [x] 4 張卡全部完成 + Codex 施工 + Opus 驗收

### 其他
- [x] Devvit MCP 設定到 Claude Code
- [x] Reddit API 調查 — 被 Responsible Builder Policy 擋
- [x] 日文版 X 推文文案

## 進行中
- [ ] Devvit 外部 fetch 被 Reddit 沙盒擋 — 目前用測試信號建帖，需改成 VPS 主動推
- [ ] 安裝到正式 r/TrumpCodeGame — `npx @devvit/cli install TrumpCodeGame`
- [ ] 開獎功能未實測 — 需等 6 小時後按「✅ Resolve Game」測試

## 已知問題
- Devvit server 無法 fetch 外部 URL — Reddit 沙盒限制，需要改架構（VPS 推 → Reddit）
- Redis 沒有 sAdd/sMembers — 已用 JSON 陣列模擬，效能 OK 但大量玩家時可能要優化
- 推文內容寫死（測試信號）— 等外部 fetch 修好就會用真實信號
- Truth Social API 被 Cloudflare 擋 — 不影響（CNN + trumpstruth 覆蓋）

## 下一步（按優先順序）
1. 解決外部 fetch — 方案：VPS 用 PRAW 或 Reddit API 直接建帖，不靠 Devvit fetch
   ```bash
   # 或者在 VPS 寫一個 Python 腳本，偵測到新信號就用 Reddit API 建帖
   ssh washin 'cd /home/ubuntu/trump-code && python3 reddit_poster.py'
   ```
2. 安裝到正式 subreddit
   ```bash
   cd /tmp/trump-code-bot/trumpcode && npx @devvit/cli install TrumpCodeGame
   ```
3. 測試完整流程 — 建帖 → 投票 → 等 6h → 開獎 → 看排行榜
4. 跟單機器人 — $TRUMP 幣信號 → Binance API

## 重要連結
- 線上：https://trumpcode.washinmura.jp
- GitHub：https://github.com/sstklen/trump-code
- Devvit App：https://developers.reddit.com/apps/trumpcodegame
- 測試 subreddit：https://www.reddit.com/r/trumpcodegame_dev
- 正式 subreddit：https://www.reddit.com/r/TrumpCodeGame
- Devvit 專案：/tmp/trump-code-bot/trumpcode/
- VPS 即時引擎：`sudo systemctl status trump-realtime`
- Reddit 帳號：PipeAccording5302
