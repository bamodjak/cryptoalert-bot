# Orbsat Crypto Alerts — Telegram Bot

**Crypto price alerts in a few taps.** People choose a coin, choose whether it should rise or fall, and select a target offset with inline buttons. No command syntax to memorize; commands remain available for power users.

**User experience**
- Guided alert setup for 15 assets: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, TON, TRX, AVAX, DOT, LINK, LTC, USDT and USDC
- One-tap current BTC price, Ethereum gas, alert list and inline alert deletion
- Premium `/live` dashboard in Telegram chat with public trade-stream prices and automatic message updates (no Mini App)
- Clear onboarding, compact Telegram command menu, and branded bot profile metadata (explicit opt-in) and command menu updated on startup
- Free plan with up to 30 active alerts; optional Premium via Telegram Stars (default 1,500 ⭐ / 30 days) for up to 500 alerts, Ethereum gas alerts, and the in-chat live dashboard
- Referral links and join counts, payment support, service terms and concise notification copy
- Price feed uses CoinGecko with CoinPaprika fallback for supported assets; values are cached in Redis

**Operations and safety**
- PostgreSQL 16 (SQLAlchemy async + Alembic), Redis, Docker, health endpoint and Prometheus metrics
- Admin-only user management, Premium grants, reversible suspension, payment review, giveaways, broadcast confirmation and kill switch
- Anti-spam throttling and idempotent Telegram Stars payment handling
- Market data can be delayed; the bot is informational and does not provide financial advice

## Admin tools

Set `OWNER_ID` to the owner's numeric Telegram user ID. Keep `ADMIN_IDS` limited to trusted numeric user IDs. The owner receives Premium without payment; other admins do not automatically receive it.

Admin commands:
- `/users [ID or username]` — find users
- `/grant USER_ID DAYS [reason]` — grant Premium and record the grant
- `/suspend USER_ID [reason]` / `/unsuspend USER_ID` — reversible access control with audit history
- `/manualpay USER_ID METHOD AMOUNT CURRENCY DAYS [reference]` — record a pending payment (cash, in-person cash, Stars, BaridiMob, CCP, or bank transfer)
- `/pendingpay` — list pending manual payments; `/verify PAYMENT_ID` / `/rejectpay PAYMENT_ID` — verify or reject pending manual payments
- `/revenue` — show unrefunded Stars and verified manual totals by currency
- `/giveaway TITLE | PREMIUM_DAYS | WINNERS`, public `/join GIVEAWAY_ID`, admin `/draw GIVEAWAY_ID`
- `/broadcast announcement text` — preview and explicitly confirm before sending

The CI workflow checks Python compilation and imports, generates offline migration SQL, applies the complete Alembic chain to an ephemeral PostgreSQL 16 database, and runs unit tests. It does not test against your live database or Telegram. Back up an existing database before deploying schema changes. The bot logs Telegram button action labels and handler success/failure for troubleshooting, without logging user IDs or message contents. New Stars purchases are processed idempotently and record their Premium entitlement interval so refunds can reduce unused time without blindly deleting unrelated Premium time. Later tracked intervals are shifted when needed. Suspended users are blocked by update middleware and excluded from alert delivery and giveaway draws. Legacy payments created before this migration lack per-charge intervals; automated refunds are blocked for those records unless an admin explicitly uses `/refund CHARGE_ID ALLOW_LEGACY` after reviewing the account. That override leaves Premium expiry unchanged.

## 1. Create the bot
1. In Telegram, open @BotFather → `/newbot` → copy the token.
2. Get your own user id from @userinfobot (this is `ADMIN_IDS`).

## 2. Server (Oracle Cloud Always Free)
1. Sign up at cloud.oracle.com (card needed for verification only).
2. Create instance → Image **Ubuntu 24.04** → Shape **VM.Standard.A1.Flex**, 2 OCPU / 12 GB → add your SSH key.
   If you get "out of capacity", retry later or try another availability domain.
3. SSH in and install Docker:
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
   sudo usermod -aG docker $USER && newgrp docker
   ```

## 3. Deploy (polling mode — no domain needed)
```bash
# copy this folder to the server (scp -r cryptoalert ubuntu@SERVER_IP:~) then:
cd cryptoalert
cp .env.example .env && nano .env      # set BOT_TOKEN, ADMIN_IDS, passwords
docker compose up -d --build
docker compose logs -f bot             # should say "polling mode"
curl localhost:8080/health
```
Keep `POSTGRES_PASSWORD` and the password inside `DATABASE_URL` identical.

## 4. Optional: webhook mode (faster, scales better)
1. Point a domain at the server (a free DuckDNS subdomain works).
2. Open ports 80/443 in the Oracle VCN Security List **and** on the VM:
   `sudo iptables -I INPUT 6 -p tcp -m multiport --dports 80,443 -j ACCEPT && sudo netfilter-persistent save`
3. In `.env` set `WEBHOOK_BASE_URL=https://your.domain`, `WEBHOOK_SECRET=<random>`, `DOMAIN=your.domain`.
4. `docker compose --profile webhook up -d --build` (Caddy gets HTTPS automatically).

## Operations
- Update: `git pull && docker compose up -d --build` (migrations run automatically)
- Pause everything instantly: send `/kill` to the bot; `/resume` to undo
- Backup DB: `docker compose exec db pg_dump -U bot bot > backup.sql`
- New migration after changing models: `docker compose run --rm bot alembic revision --autogenerate -m "msg"`
- Tests: `pip install -r requirements.txt pytest && BOT_TOKEN=1:x pytest`

## Selling it
- Run it as your own product (Stars revenue), or white-label it per client: one `.env` + one compose stack per client.
- Keep the "not financial advice" disclaimer. Never store or ask for users' keys or wallets.
