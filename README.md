# Orbsat Crypto Alerts — Telegram Bot

**Crypto price alerts in a few taps.** People choose a coin, choose whether it should rise or fall, and select a target offset with inline buttons. No command syntax to memorize; commands remain available for power users.

**User experience**
- Guided alert setup for 15 assets: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, TON, TRX, AVAX, DOT, LINK, LTC, USDT and USDC
- Top-33 crypto price browser with individual coin selection or all-coin view, Ethereum gas, global market pulse, personal watchlist (up to 10 assets), alert list and inline alert deletion
- Main navigation includes a one-tap Premium live-market entry; free users receive the Premium gate, while eligible users open an auto-updating dashboard in the chat
- Premium `/live` dashboard in Telegram chat with public trade-stream prices and automatic message updates (no Mini App)
- Clear onboarding, a persistent color-coded Telegram quick-action keyboard (markets/movers, crypto prices/alerts, Premium/help), compact Telegram command menu, and branded bot profile metadata (explicit opt-in) and command menu updated on startup
- Free plan with up to 30 active alerts; optional Premium via Telegram Stars (default 1,500 ⭐ / 30 days) for up to 500 alerts, Premium quantitative signals, Ethereum gas alerts, and the in-chat live dashboard
- Referral links and join counts, payment support, service terms and concise notification copy
- Price feed uses CoinGecko with CoinPaprika fallback for supported assets; provider quotes are cached in Redis
- `/prices` displays the top 33 assets by market capitalization with individual selection or a complete price list, 24-hour change, provider fallback and stale-data protection
- `/market` shows total crypto market capitalization, 24-hour trading volume and market-cap change, BTC dominance, and active asset count; results are cached briefly with a bounded stale-data fallback
- `/movers` shows the top five 24-hour gainers and losers among the 100 largest assets by market cap, with a $10M minimum market cap, cached data and a stale-data fallback
- `/watchlist` tracks up to 10 supported assets per Telegram account, shows cached USD quotes, and supports guided add/remove actions
- Premium `/signals` quantitative research desk ranks BTC, ETH, SOL, BNB, XRP, DOGE, LINK and AVAX using 4h exchange OHLCV history, multi-horizon momentum, EMA trend, RSI, ATR, volume confirmation and volatility-regime controls; cached for five minutes
- Premium live-market ticks are kept in process memory and dashboard edits are coalesced to a 10-second cadence to avoid burning Redis request quota on every exchange trade

**Operations and safety**
- PostgreSQL 16 (SQLAlchemy async + Alembic), Redis, Docker, health endpoint, Prometheus metrics and a token-gated read-only web operations dashboard at `/dashboard`
- Admin-only user management with searchable user selection and direct Premium grants (no routine ID copy/paste), confirmation-gated suspend/restore controls, and editable Premium Stars price/duration from the Admin Panel; pending manual payments have an in-chat review queue with explicit verify/reject confirmation, audit logging and Premium activation only after verification; admin grants and access changes are committed before success replies, concurrent grants lock the target account, and duplicate manual-payment references are handled as a recoverable validation error; also includes giveaways, broadcast confirmation and kill switch
- Admin overview reports registrations (including 7-day growth), Premium accounts, active alerts, suspended/blocked accounts, unrefunded Stars and pending manual payments
- Anti-spam throttling and idempotent Telegram Stars payment handling
- Market data can be delayed; the bot is informational and does not provide financial advice

### Product UX and Premium conversion

The Telegram home dashboard keeps **Create Price Alert** as the primary first action and groups the remaining destinations into compact two-button rows for mobile screens. Welcome copy explains the first three steps, while Help is organized as a short beginner guide before listing optional commands. English and Arabic copy must remain aligned.

### CI validation

The tests workflow runs on pushes to main and feature branches and on pull requests targeting main. Manual workflow dispatch is available after the workflow_dispatch trigger is present on the default branch. Commits created by GitHub App or API tokens may suppress follow-on workflow events; before merging, confirm that CI ran against the exact current PR head SHA. A prior green run for an older commit does not validate the current head.

- Premium custom Watchlist assets are now discoverable by typing a project or coin name in Telegram. The bot presents up to six matching CoinGecko results, lets the user choose one, verifies its metadata and current USD quote, then adds it within the existing 10-item cap. Users no longer need to leave Telegram to copy a CoinGecko ID. Search, result selection, verification errors, duplicates, full lists and cancellation are localized in English and Arabic.
