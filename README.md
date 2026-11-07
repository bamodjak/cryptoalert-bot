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

### Telegram navigation notes

Premium members receive an explicit membership/expiry banner and a summary of paid features on start, Dashboard and Premium screens. Premium Signals and Live Market entries are labelled as Premium features. The persistent reply keyboard presents four plain-language actions (Dashboard, Markets, Alerts, Premium); the inline dashboard includes Market Pulse, Top Movers, Live Market, Crypto Prices, Gas, Watchlist, Premium Signals, Alerts, Premium, Referrals, Help, Language and Settings. The Live Market entry is available in navigation but requires Premium to open. Watchlists expose the standard supported-asset picker to all users and a separate Premium custom-asset flow: Premium members submit a CoinGecko ID, which is format-checked, resolved against CoinGecko metadata, and required to return a current USD quote before insertion. Custom assets remain subject to the same 10-item watchlist cap. While entering a custom ID, slash commands are not mistaken for IDs; `/cancel` clears the pending input state. Interactive Telegram input states expire after 30 minutes of inactivity, preventing abandoned flows from capturing unrelated messages indefinitely. Alert target and alert-edit prompts accept plain-text values but allow slash commands such as /help and /start to reach their normal handlers; /cancel exits the active prompt. The inline dashboard is a single-column menu with 14 public destinations plus owner/admin-only controls where configured. Each action is routed to a real handler, while slash commands remain available for compatibility. Administrative controls are shown only to configured admins. In Admin Panel → Premium pricing & duration, change the Stars price (1–100000) or entitlement duration (1–3650 days); values are stored in Redis and apply to new invoices. Numeric replies are matched by the admin message filter and validated before saving; non-numeric input receives recovery guidance, and `/cancel` exits the edit without changing the current offer. A successful update is audited and confirmed in chat. Existing invoices retain the exact quoted price and duration. Telegram clients control the exact rendering; unsupported clients may display buttons without color styling. The keyboard requires aiogram 3.25 or newer for Bot API 9.4 `KeyboardButton.style` support.

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
- `/broadcast announcement text` — preview and explicitly confirm before sending; alternatively reply to a Telegram message with `/broadcast` to copy its text or supported media to the registered, non-suspended audience

The CI workflow checks Python compilation and imports, generates offline migration SQL, applies the complete Alembic chain to an ephemeral PostgreSQL 16 database, and runs unit tests. It does not test against your live database or Telegram. The workflow supports manual execution from GitHub Actions (`Actions` → `tests` → `Run workflow`). This is required when a branch commit is created by an authenticated GitHub automation token, because GitHub suppresses most follow-on workflow events for token-authored commits. A green CI result must be checked against the current PR head SHA. Back up an existing database before deploying schema changes. The bot logs Telegram button action labels and handler success/failure for troubleshooting, without logging user IDs or message contents. New Stars purchases are processed idempotently and record their Premium entitlement interval so refunds can reduce unused time without blindly deleting unrelated Premium time. Later tracked intervals are shifted when needed. Suspended users are blocked by update middleware and excluded from alert delivery and giveaway draws. Legacy payments created before this migration lack per-charge intervals; automated refunds are blocked for those records unless an admin explicitly uses `/refund CHARGE_ID ALLOW_LEGACY` after reviewing the account. That override leaves Premium expiry unchanged. Premium-only gas alerts and price alerts beyond the Free-plan allowance are paused when Premium expires, preserved in the account, and automatically unpaused when Premium returns. A Stars refund whose Telegram API result is ambiguous is marked pending reconciliation; do not retry it until the transaction has been checked.

## 1. Create the bot
1. In Telegram, open @BotFather → `/newbot` → copy the token.
2. Get your own user id from @userinfobot (this is `ADMIN_IDS`).

## 2. Production deployment (Botkeep)

The current production host is **Botkeep**. The bot uses Telegram long polling by default; a public domain is not required for Telegram updates. The application also exposes HTTP health and operations endpoints.

1. In Botkeep, create an application from this GitHub repository and select the production branch used for the release.
2. Select a Python runtime compatible with Python 3.12, or use the repository Dockerfile if choosing a Docker runtime.
3. Set the startup command to:
   ```bash
   alembic upgrade head && python -m app.main
   ```
   Do not run a second copy of the bot against the same production database.
4. Create PostgreSQL and Redis services in Botkeep. Copy their connection details from each service's Database tab into `DATABASE_URL` and `REDIS_URL`. Use the connection/TLS settings Botkeep supplies; do not assume local Docker service names.
5. Add the application environment variables in Botkeep's **Environment** settings. Never commit secrets to Git.
6. In the application's Network settings, use the assigned service port as `SERVER_PORT`. The app binds to `0.0.0.0`; `PORT` is also accepted for compatible hosts. Configure a Botkeep Domain only if you need public HTTPS access to the HTTP endpoints.
7. Start the application and verify the actual runtime in **Console**. An accepted deployment operation alone does not prove the bot is ready.

Required environment:
- `BOT_TOKEN` — token from @BotFather.
- `BOT_USERNAME` — Telegram username without the @.
- `ADMIN_IDS` — comma-separated numeric Telegram user IDs.
- `OWNER_ID` — owner's numeric Telegram user ID.
- `DATABASE_URL` — Botkeep PostgreSQL connection URL.
- `REDIS_URL` — Botkeep Redis connection URL.

Optional settings include `SUPPORT_CONTACT`, `COINGECKO_API_KEY`, `ETH_RPC_URL`, `CHECK_INTERVAL`, Premium pricing/limits and `METRICS_TOKEN`. Set a strong `METRICS_TOKEN` to protect `/dashboard`, `/stats.json` and `/metrics`. Leave `WEBHOOK_BASE_URL` empty for the default polling mode. Do not enable `UPDATE_BOT_PROFILE_ON_STARTUP` unless intentionally changing Telegram profile metadata.

After deployment, verify:
- Botkeep Console shows the process running without repeated restarts or startup errors.
- `/health` returns HTTP 200 after Telegram transport and the alert worker are ready; a 503 response means a dependency or readiness check has failed.
- The bot responds to `/start` in Telegram and its core flows work.
- Logs show successful database migrations, router registration and Telegram polling. Do not expose bot tokens, database URLs or user data in shared logs.

Back up PostgreSQL from the Botkeep panel before deploying schema changes. Keep an independent backup copy. Check the live application after every deployment; a green platform status page does not verify an individual bot workload.

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


## Reliability audit notes (PR #56)

- Premium offer editing accepts ordinary numeric replies, explains invalid input and supports /cancel; custom Watchlist CoinGecko-ID entry also supports /cancel. Alert target and alert-edit FSM prompts no longer swallow slash commands; normal bot commands remain reachable while a prompt is active.
- Telegram FSM state and data expire after 30 minutes of inactivity so abandoned alert/watchlist prompts do not capture unrelated messages indefinitely.
- Admin Premium grants and suspend/restore operations lock the target account and commit before success replies. Manual payment references are protected against concurrent duplicate submissions; a unique-reference conflict is rolled back and reported cleanly.
- Refund reconciliation locks the affected user before adjusting Premium expiry.
- The alert worker commits each successful alert delivery before moving to the next notification and stops starting new sends after losing its Redis lease. Telegram does not expose a sendMessage idempotency key, so a crash after Telegram accepts a message but before the database commit can still cause a single duplicate on retry.
- Broadcasts keep a Redis per-recipient delivery ledger and can resume after interruption without deliberately resending recipients already recorded as delivered. Cancellation clears the payload and ledger. The narrow send-accepted/process-crash-before-ledger-write interval can still cause one duplicate for the affected recipient.
- If Telegram reports that a registered Live Market dashboard message no longer exists or cannot be edited, the runtime removes that stale registration. The user can reopen /live to create a new dashboard.
- These safeguards are covered by tests/test_input_recovery.py and tests/test_reliability_audit.py. CI and Botkeep runtime smoke tests remain required before merge/deployment.
