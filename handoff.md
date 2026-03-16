# 交接文件
> 日期：2026-03-16 (Session 2) | 摘要：即時引擎大升級 — 三源抓取 + Polymarket 修復 + 48h 追蹤 + systemd 常駐 + Devvit App 起步

## 已完成

### 即時引擎（realtime_loop.py）
- [x] 推文抓取從 CNN 單源 → CNN + trumpstruth.org + X API 三源齊發
- [x] 新推文自動寫入 trump_posts_all.json — 前端即時顯示（44,070→44,076 篇）
- [x] Polymarket 快照修復 — 改用 /public-search API（390 個市場）
- [x] 追蹤窗口從 6h 延長到 48h（1h/3h/6h/12h/24h/48h）
- [x] VPS systemd 常駐服務 — 每 5 分鐘掃一次，掛了 30 秒重啟，開機自動啟動
- [x] X API Bearer Token 更新到 VPS .env（新 Token 測試通過）
- [x] Truth Social 帳號已設定在 VPS .env（API 被 Cloudflare 擋，備用）
- [x] commit + push 到 GitHub ✅

### Reddit / Devvit
- [x] Devvit CLI 安裝成功 — v0.12.14
- [x] Devvit 帳號登入成功 — PipeAccording5302
- [x] Devvit App 專案建立 — /tmp/trump-code-bot/trumpcode/（bare 模板）
- [x] Devvit MCP 設定到 ~/.claude/.mcp.json

### 推廣
- [x] 日文版 X 推文文案（翻譯完成，待發）

## 進行中
- [ ] Devvit 預測遊戲 App — 已建專案，還沒改程式碼
- [ ] Reddit API 申請被擋 — Responsible Builder Policy 2025/11 後要人工審核，create app 頁面過不去
- [ ] Reddit 手動推廣文案 — 還沒寫 r/sideproject、r/dataisbeautiful 版本

## 已知問題
- Reddit API 無法自助申請 — 被 Responsible Builder Policy 擋住，只能走 Devvit 或手動
- Truth Social 直接 API 被 Cloudflare 403 擋 — client_id/secret 是佔位符，暫不影響（CNN+trumpstruth 已覆蓋）
- VPS 上 polymarket_client.py 還是舊版 — 不影響（realtime_loop 已自己實作 /public-search）
- og:image 還沒做 — 需要 1200×630 社群分享圖

## 下一步（按優先順序）
1. Devvit 預測遊戲 — `cd /tmp/trump-code-bot/trumpcode && npm run dev`
   - 川普發文 → Reddit 用戶投票漲跌 → 6h 後比對真實市場 → 排行榜
   - 建一個 subreddit r/TrumpCodeGame 安裝 App
2. Reddit 推廣文案 — 寫好 r/sideproject、r/dataisbeautiful、r/Python 的貼文
3. 跟單機器人 — $TRUMP 幣信號→Binance API 自動下單
4. Telegram Bot — 信號即時推送給訂閱者
5. OG 預覽圖 — 做一張 1200×630 社群分享圖

## 重要連結
- 線上：https://trumpcode.washinmura.jp
- GitHub：https://github.com/sstklen/trump-code
- VPS 即時引擎：`sudo systemctl status trump-realtime`
- VPS 即時 log：`tail -f /home/ubuntu/trump-code/realtime.log`
- Devvit 專案：/tmp/trump-code-bot/trumpcode/
- Devvit Token：~/.devvit/token
- Reddit 帳號：PipeAccording5302
- Cron：每天 22:30 UTC 跑 daily_pipeline.py
