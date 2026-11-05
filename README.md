# Orbsat Crypto Alerts — Telegram Bot

**Crypto price alerts in a few taps.** People choose a coin, choose whether it should rise or fall, and select a target offset with inline buttons. No command syntax to memorize; commands remain available for power users.

**User experience**
- Guided alert setup for 15 assets: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, TON, TRX, AVAX, DOT, LINK, LTC, USDT and USDC
- Top-33 crypto price browser with individual coin selection or all-coin view, Ethereum gas, global market pulse, personal watchlist (up to 10 assets), alert list and inline alert deletion
- Premium `/live` dashboard in Telegram chat with public trade-stream prices and automatic message updates (no Mini App)
- Clear onboarding, a persistent color-coded Telegram quick-action keyboard (markets/movers, crypto prices/alerts, Premium/help), compact Telegram command menu, and branded bot profile metadata (explicit opt-in) and command menu updated on startup
- Free plan with up to 30 active alerts; optional Premium via Telegram Stars (default 1,500 ⭐ / 30 days) for up to 500 alerts, Ethereum gas alerts, and the in-chat live dashboard
- Referral links and join counts, payment support, service terms and concise notification copy
- Price feed uses CoinGecko with CoinPaprika fallback for supported assets; provider quotes are cached in Redis
- `/prices` displays the top 33 assets by market capitalization with individual selection or a complete price list, 24-hour change, provider fallback and stale-data protection
- `/market` shows total crypto market capitalization, 24-hour trading volume and market-cap change, BTC dominance, and active asset count; results are cached briefly with a bounded stale-data fallback
- `/movers` shows the top five 24-hour gainers and losers among the 100 largest assets by market cap, with a $10M minimum market cap, cached data and a stale-data fallback
- `/watchlist` tracks up to 10 supported assets per Telegram account, shows cached USD quotes, and supports guided add/remove actions
- Premium `/signals` quantitative research desk ranks BTC, ETH, SOL, BNB, XRP, DOGE, LINK and AVAX using 4h exchange OHLCV history, multi-horizon momentum, EMA trend, RSI, ATR, volume confirmation and volatility-regime controls; cached for five minutes
- Premium live-market ticks are kept in process memory and dashboard edits are coalesced to a 10-second cadence to avoid burning Redis request quota on every exchange trade

**Operations and safety**
- PostgreSQL 16 (SQLAlchemy async + Alembic), Redis, Docker, health endpoint and Prometheus metrics
- Admin-only user management, Premium grants, reversible suspension, payment review, giveaways, broadcast confirmation and kill switch
- Anti-spam throttling and idempotent Telegram Stars payment handling
- Market data can be delayed; the bot is informational and does not provide financial advice

### Telegram navigation notes

The persistent reply keyboard presents four plain-language actions (Dashboard, Markets, Alerts, Premium); the inline dashboard also includes a personal Watchlist; users do not need to type slash commands. Each label is routed to a real handler, while existing slash commands remain available for compatibility. The compact inline dashboard provides the full feature menu, with administrative controls shown only to configured admins. Telegram clients control the exact rendering; unsupported clients may display buttons without color styling. The keyboard requires aiogram 3.25 or newer for Bot API 9.4 `KeyboardButton.style` support.

## Admin tools

Set `OWNER_ID` to the owner's numeric Telegram user ID. Keep `ADMIN_IDS` limited to trusted numeric user IDs. The owner receives Premium without payment; other admins do not automatically receive it.

Admin commands:
- `/users [ID or username]` — find users
- `/grant USER_ID DAYS [reason]` — grant Premium and record the grant
- `/suspend USER_ID [reason]` / `/unsuspend USER_ID` — reversible access control with audit history
- `/manualpay USER_ID METHOD AMOUNT CURRENCY DAYS [reference]` — record a pending payment (cash, in-person cash, Stars, BaridiMob, CCP, or bank transfer)
- `/pendingpay` — list pending manual payments; `/verify PAYMENT_ID` / `/rejectpay PAYMENT_ID` — verify or reject pending manual payments
- `/revenue` — show unrefunded Stars and verified manual totals by currency
- `/refundpending` — list Stars refunds with an uncertain API result; `/refundresolve CHARGE_ID REFUNDED|NOT_REFUNDED` records the outcome after checking Telegram
- `/giveaway TITLE | PREMIUM_DAYS | WINNERS`, public `/join GIVEAWAY_ID`, admin `/draw GIVEAWAY_ID`
- `/broadcast announcement text` — preview and explicitly confirm before sending

The CI workflow checks Python compilation and imports, generates offline migration SQL, applies the complete Alembic chain to an ephemeral PostgreSQL 16 database, and runs unit tests. It does not test against your live database or Telegram. Back up an existing database before deploying schema changes. The bot logs Telegram button action labels and handler success/failure for troubleshooting, without logging user IDs or message contents. New Stars purchases are processed idempotently and record their Premium entitlement interval so refunds can reduce unused time without blindly deleting unrelated Premium time. Later tracked intervals are shifted when needed. Suspended users are blocked by update middleware and excluded from alert delivery and giveaway draws. Legacy payments created before this migration lack per-charge intervals; automated refunds are blocked for those records unless an admin explicitly uses `/refund CHARGE_ID ALLOW_LEGACY` after reviewing the account. That override leaves Premium expiry unchanged. Premium-only gas alerts and price alerts beyond the Free-plan allowance are paused when Premium expires, preserved in the account, and automatically unpaused when Premium returns. A Stars refund whose Telegram API result is ambiguous is marked pending reconciliation; do not retry it until the transaction has been checked.

## 1. Create the bot
1. In Telegram, open @BotFather → `/newbot` → copy the token.
2. Get your own user id from @userinfobot (this is `ADMIN_IDS`).

## 2. Production deployment (Render)

The production deployment uses Render. Keep the Telegram bot, PostgreSQL and Redis connected through their private/internal service URLs.

1. In Render, create a **Web Service** from this GitHub repository and select the `main` branch.
2. Choose **Docker** as the runtime. The repository Dockerfile runs database migrations and starts the bot.
3. Set the health-check path to `/health`. Render supplies the `PORT` environment variable; the application listens on it.
4. Create or attach a PostgreSQL database and a Redis instance in the same Render region. Use their **internal** connection URLs.
5. Configure the environment variables below. Keep credentials in Render's Environment settings, never in Git.

Required environment:
- `BOT_TOKEN` — token from @BotFather.
- `BOT_USERNAME` — Telegram username without the @.
- `ADMIN_IDS` — comma-separated numeric Telegram user IDs.
- `OWNER_ID` — owner's numeric Telegram user ID.
- `DATABASE_URL` — Render PostgreSQL internal URL. The app normalizes standard PostgreSQL URLs for async SQLAlchemy.
- `REDIS_URL` — Render Redis internal URL.
- `POSTGRES_PASSWORD` is only needed for the Docker Compose self-hosted database; it is not used by the managed Render database.

Optional settings include `SUPPORT_CONTACT`, `COINGECKO_API_KEY`, `ETH_RPC_URL`, `CHECK_INTERVAL`, Premium pricing/limits and `METRICS_TOKEN`. Leave `WEBHOOK_BASE_URL` empty for the default polling mode. Do not enable `UPDATE_BOT_PROFILE_ON_STARTUP` unless intentionally changing Telegram profile metadata.

After deployment, verify:
- `/` returns the service landing response.
- `/health` returns HTTP 200 and reports Telegram transport and worker readiness.
- The bot responds to `/start` in Telegram.
- Render logs show successful database migrations, router registration and Telegram polling.

Deploys from `main` are automatic. Database migrations run at container startup; take a database backup before deploying schema changes.

## 3. Self-host with Docker Compose (optional)

For a separate self-managed installation, create `.env` from `.env.example`, set `BOT_TOKEN`, `ADMIN_IDS`, `OWNER_ID`, database credentials and other required values, then run:

```bash
cp .env.example .env
# Edit .env and set strong unique credentials.
docker compose up -d --build
docker compose logs -f bot
curl http://localhost:8080/health
```

Keep `POSTGRES_PASSWORD` and the password inside `DATABASE_URL` identical. The Compose stack provides PostgreSQL and Redis. For webhook mode, configure a public HTTPS domain, `WEBHOOK_BASE_URL`, `WEBHOOK_SECRET` and `DOMAIN`, then start the optional Caddy profile:

```bash
docker compose --profile webhook up -d --build
```

## Operations
- Update: `git pull && docker compose up -d --build` (migrations run automatically)
- Pause everything instantly: send `/kill` to the bot; `/resume` to undo
- Backup DB: `docker compose exec db pg_dump -U bot bot > backup.sql`
- New migration after changing models: `docker compose run --rm bot alembic revision --autogenerate -m "msg"`
- Tests: `pip install -r requirements.txt pytest && BOT_TOKEN=1:x pytest`

## Selling it
- Run it as your own product (Stars revenue), or white-label it per client: one `.env` + one compose stack per client.
- Keep the "not financial advice" disclaimer. Never store or ask for users' keys or wallets.
## Unified asset experience and user controls

Orbsat uses a canonical core asset registry shared by the price service, watchlists, live dashboard and quantitative research universe. Top-33 and market-mover assets open a common detail view with contextual actions to create alerts, add to a watchlist and open research where available.

Users can manage their preferred language, timezone and advanced presentation mode from /settings. Advanced mode reveals additional market-cap and 24-hour volume context when the provider supplies it. Alert management supports editing targets, one-hour snoozing, resuming and a recent triggered-alert history via /history.

These features are implemented as a modular monolith. The application keeps one deployment and shared PostgreSQL/Redis infrastructure while separating domain concepts, application services, provider adapters, Telegram routers and background workers. See docs/architecture/blueprint.md for dependency rules and the staged migration plan.

Database upgrades are additive. The alert snooze and user preference migrations preserve existing rows and values.

The live trade stream and dashboard updater run under one Redis-elected runtime lease, keeping their in-memory quote state together during overlapping deployments.

Users can independently enable or disable price-alert and gas-alert categories in `/settings`; disabled categories retain their configured alerts. Premium research screens show the rule drivers behind each signal and an in-sample historical directional evaluation with sample counts and explicit fee/slippage limitations.

Operational visibility includes Prometheus counters and gauges for alert delivery, live runtime ownership, Binance stream connectivity/reconnects, trade ingestion and Telegram dashboard edits. `/health` reports service readiness plus live-runtime and stream state.
