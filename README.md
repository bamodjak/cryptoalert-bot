# Crypto Alert Bot (Telegram)

Production-ready price & Ethereum gas alert bot with a Telegram Stars paywall.

**Stack:** aiogram 3 · PostgreSQL 16 (SQLAlchemy 2 async + Alembic) · Redis 7 · Docker Compose · Prometheus metrics · health check · admin kill switch.

## Features
- `/price btc`, `/alert btc > 70000`, `/alert eth below 2.5k`, `/alerts`, `/del 12`
- `/gas` and `/gasalert 0.5` (Premium) via a public Ethereum RPC
- Free tier: 3 alerts. Premium (Telegram Stars, default 250 ⭐ / 30 days): 50 alerts + gas alerts
- `/paysupport` and `/terms` (Telegram requires these for Stars payments)
- Admin: `/kill`, `/resume`, `/stats`, `/refund <charge_id>`
- Anti-spam throttle (30 msgs/min/user), auto-cleanup when a user blocks the bot
- Prices from CoinGecko (cached in Redis, batched 100 coins per call)
- `GET /health` (DB + Redis + worker freshness) and `GET /metrics` (Prometheus)

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
