import asyncio
import logging
import secrets
import signal
import time
from datetime import timedelta

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError
from sqlalchemy import func, select, text

from . import metrics, worker
from .config import settings
from .db import Alert, ManualPayment, Payment, Session, User, engine, utcnow
from .handlers import admin, router
from .watchlists import router as watchlists_router
from .live_dashboard import live_runtime_loop, router as live_dashboard_router
from .admin_tools import public_router as giveaway_public_router, router as admin_tools_router
from .admin_management import router as admin_management_router
from .middlewares import KILL_KEY, CallbackTraceMiddleware, DbSessionMiddleware, KillSwitchMiddleware, MessageTraceMiddleware, ThrottleMiddleware
from .prices import PriceService
from .preferences import router as preferences_router

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="start", description="Home & referral link"),
    BotCommand(command="create", description="Create an alert with buttons"),
    BotCommand(command="alert", description="Create an alert with text syntax"),
    BotCommand(command="del", description="Delete an alert by ID"),
    BotCommand(command="price", description="Check a single coin price"),
    BotCommand(command="prices", description="Browse top 33 crypto prices"),
    BotCommand(command="market", description="Global market overview"),
    BotCommand(command="movers", description="Top 24-hour market movers"),
    BotCommand(command="live", description="Premium live market dashboard"),
    BotCommand(command="alerts", description="View or remove your alerts"),
    BotCommand(command="history", description="View recently triggered alerts"),
    BotCommand(command="watchlist", description="Track your favourite assets"),
    BotCommand(command="signals", description="Premium quantitative market research"),
    BotCommand(command="gas", description="Check Ethereum network gas"),
    BotCommand(command="gasalert", description="Premium Ethereum gas alert"),
    BotCommand(command="cancel", description="Cancel alert setup"),
    BotCommand(command="premium", description="Unlock more alerts"),
    BotCommand(command="help", description="How Orbsat works"),
    BotCommand(command="language", description="Change language / تغيير اللغة"),
    BotCommand(command="settings", description="Personal settings and timezone"),
    BotCommand(command="terms", description="Service terms"),
    BotCommand(command="paysupport", description="Payment help"),
]

CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}
telegram_transport_ready = False

ADMIN_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Orbsat · Operations</title>
<style>
:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#e8edf5;background:#0b1020}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 10% 0%,#1a2c48 0,transparent 42%),#0b1020}
main{max-width:1080px;margin:auto;padding:36px 22px 64px}.top{display:flex;justify-content:space-between;align-items:center;gap:18px;flex-wrap:wrap}
h1{font-size:clamp(25px,4vw,38px);margin:0}.muted{color:#93a4bd}.status{border:1px solid #33445d;border-radius:99px;padding:7px 12px;font-size:12px}
.panel{margin-top:26px;padding:20px;border:1px solid #27364d;border-radius:18px;background:#111a2b}
label{display:block;font-size:13px;color:#aab9ce;margin-bottom:8px}input{width:min(100%,520px);padding:12px;border:1px solid #40516b;border-radius:10px;background:#0b1322;color:#fff}
button{padding:12px 16px;border:0;border-radius:10px;background:#6ca6ff;color:#081323;font-weight:700;cursor:pointer;margin-left:8px}
#error{color:#ff9b9b;min-height:20px;margin-top:10px;font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-top:20px}
.card{padding:20px;border:1px solid #27364d;border-radius:16px;background:#111a2b}.card span{display:block;color:#9fb0c7;font-size:13px}.card strong{display:block;font-size:30px;margin-top:9px;overflow-wrap:anywhere}
footer{margin-top:28px;color:#8191a9;font-size:12px;line-height:1.6}
@media(max-width:520px){main{padding:24px 14px}button{margin:10px 0 0;width:100%}}
</style></head>
<body><main>
<div class="top"><div><div class="muted">ORBSAT / OPERATIONS</div><h1>Control Room</h1><div class="muted">Read-only service and growth overview</div></div><div id="status" class="status">Disconnected</div></div>
<section class="panel"><label for="token">Operations access token · METRICS_TOKEN</label><input id="token" type="password" autocomplete="off" placeholder="Paste the configured metrics token"><button id="load">Load overview</button><div id="error" role="status"></div><div class="muted">The token is used only for this request and is not saved by this page.</div></section>
<section class="grid" aria-live="polite">
<div class="card"><span>Registered users</span><strong id="users">—</strong></div>
<div class="card"><span>New users · 7 days</span><strong id="new-users">—</strong></div>
<div class="card"><span>Premium accounts</span><strong id="premium">—</strong></div>
<div class="card"><span>Active alerts</span><strong id="alerts">—</strong></div>
<div class="card"><span>Suspended / blocked</span><strong id="access">—</strong></div>
<div class="card"><span>Unrefunded Stars</span><strong id="stars">—</strong></div>
<div class="card"><span>Pending manual payments</span><strong id="pending">—</strong></div>
<div class="card"><span>Referral shares / joins</span><strong id="referrals">—</strong></div>
<div class="card"><span>Kill switch</span><strong id="killed">—</strong></div>
<div class="card"><span>Live runtime / stream</span><strong id="live">—</strong></div>
</section>
<footer>Read-only analytics. User grants, payment verification, broadcasts and other account mutations remain in the authenticated Telegram admin panel. Market information is not financial advice.</footer>
</main>
<script>
const $=id=>document.getElementById(id);
$("load").addEventListener("click",async()=>{
 const token=$("token").value.trim();$("error").textContent="";
 if(!token){$("error").textContent="Enter the configured METRICS_TOKEN.";return;}
 $("status").textContent="Connecting…";
 try{
  const response=await fetch("/stats.json",{headers:{Authorization:"Bearer "+token},cache:"no-store"});
  if(!response.ok)throw new Error(response.status===404?"Access denied. Check METRICS_TOKEN.":"Request failed ("+response.status+").");
  const d=await response.json();
  for(const [id,value] of Object.entries({
   users:d.users,"new-users":d.new_users,premium:d.premium_users,alerts:d.active_alerts,
   access:d.suspended_users+" / "+d.blocked_users,stars:d.stars_earned,pending:d.pending_manual_payments,
   referrals:d.affiliation_shared+" / "+d.referrals_joined,killed:d.killed?"ON":"OFF",
   live:(d.live_runtime_active?"active":"idle")+" / "+(d.live_stream_connected?"connected":"offline")
  }))$(id).textContent=String(value);
  $("status").textContent="Connected · refreshed "+new Date().toLocaleTimeString();
 }catch(error){$("status").textContent="Disconnected";$("error").textContent=error.message;}
});
</script></body></html>"""


def register_routers(dp: Dispatcher) -> None:
    # Specific admin-tools callbacks must precede the generic admin:* callback handler.
    dp.include_routers(admin_management_router, admin_tools_router, admin, giveaway_public_router, live_dashboard_router, watchlists_router, preferences_router, router)


def build_web_app(redis: Redis) -> web.Application:
    async def health(_: web.Request) -> web.Response:
        try:
            await redis.ping()
            async with Session() as s:
                await s.execute(text("SELECT 1"))
        except Exception:
            log.exception("health check failed")
            return web.json_response({"ok": False, "error": "dependency check failed"}, status=503, headers=CORS_HEADERS)
        if not telegram_transport_ready:
            return web.json_response(
                {"ok": False, "error": "telegram transport not ready"},
                status=503,
                headers=CORS_HEADERS,
            )
        stale = not worker.last_success or time.time() - worker.last_success > settings.check_interval * 5
        if stale:
            return web.json_response({"ok": False, "error": "alert worker stale"}, status=503, headers=CORS_HEADERS)
        return web.json_response({"ok": True, **metrics.live_status()}, headers=CORS_HEADERS)

    def metrics_authorized(request: web.Request) -> bool:
        token = settings.metrics_token.get_secret_value()
        supplied = request.headers.get("Authorization", "")
        return bool(token) and secrets.compare_digest(supplied, f"Bearer {token}")

    async def metrics_view(request: web.Request) -> web.Response:
        if not metrics_authorized(request):
            return web.Response(status=404)
        return web.Response(body=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST, **CORS_HEADERS})

    async def stats_json(request: web.Request) -> web.Response:
        if not metrics_authorized(request):
            return web.Response(status=404)
        async with Session() as s:
            users = await s.scalar(select(func.count()).select_from(User)) or 0
            premium = await s.scalar(select(func.count()).select_from(User).where((User.premium_until > utcnow()) | (User.id == settings.owner_id))) or 0
            alerts = await s.scalar(select(func.count()).select_from(Alert).where(Alert.active.is_(True))) or 0
            suspended = await s.scalar(select(func.count()).select_from(User).where(User.suspended.is_(True))) or 0
            blocked = await s.scalar(select(func.count()).select_from(User).where(User.blocked.is_(True))) or 0
            new_users = await s.scalar(select(func.count()).select_from(User).where(User.created_at >= utcnow() - timedelta(days=7))) or 0
            pending_manual = await s.scalar(select(func.count()).select_from(ManualPayment).where(ManualPayment.status == "pending")) or 0
            stars = await s.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.refunded.is_(False))) or 0
            shared = await s.scalar(select(func.coalesce(func.sum(User.affiliation_shares), 0))) or 0
            referrals = await s.scalar(select(func.coalesce(func.sum(User.referrals_count), 0))) or 0
        killed = bool(await redis.exists(KILL_KEY))
        return web.json_response(
            {
                "ok": True,
                "users": users,
                "new_users": new_users,
                "premium_users": premium,
                "active_alerts": alerts,
                "suspended_users": suspended,
                "blocked_users": blocked,
                "pending_manual_payments": pending_manual,
                "stars_earned": stars,
                "affiliation_shared": shared,
                "referrals_joined": referrals,
                "killed": killed,
                **metrics.live_status(),
            },
            headers={**CORS_HEADERS, "Cache-Control": "no-store"},
        )

    async def root(_: web.Request) -> web.Response:
        # Render probes the service root by default. Return a lightweight 200
        # rather than a 404 so deploy checks and humans see a useful landing point.
        return web.json_response(
            {
                "service": "Orbsat Crypto Alerts",
                "status": "running",
                "telegram": f"https://t.me/{settings.bot_username}",
                "health": "/health",
            },
            headers=CORS_HEADERS,
        )

    async def admin_dashboard(_: web.Request) -> web.Response:
        return web.Response(
            text=ADMIN_DASHBOARD_HTML,
            content_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            },
        )

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/dashboard", admin_dashboard)
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics_view)
    app.router.add_get("/stats.json", stats_json)
    return app



FSM_STATE_TTL = timedelta(minutes=30)

POLLING_LOCK_KEY = "bot:telegram-poller"
POLLING_LOCK_TTL_SECONDS = 45
POLLING_STARTUP_GRACE_SECONDS = 10
POLLING_LOCK_RETRY_SECONDS = 1
POLLING_LOCK_RENEW_SECONDS = 10
POLLING_TIMEOUT_SECONDS = 5


async def _renew_polling_lock(lock, dispatcher: Dispatcher, stop: asyncio.Event) -> None:
    """Renew the singleton Telegram polling lease; stop polling if ownership is lost."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLLING_LOCK_RENEW_SECONDS)
            return
        except asyncio.TimeoutError:
            pass
        try:
            if not await lock.extend(POLLING_LOCK_TTL_SECONDS, replace_ttl=True):
                log.critical("Telegram polling lease renewal failed; stopping this poller")
                stop.set()
                await dispatcher.stop_polling()
                return
        except Exception:
            log.exception("Telegram polling lease renewal errored; stopping this poller")
            stop.set()
            await dispatcher.stop_polling()
            return


async def run_polling_with_lock(
    dispatcher: Dispatcher, bot: Bot, redis: Redis, stop: asyncio.Event
) -> None:
    """Run exactly one Telegram getUpdates consumer across overlapping deployments."""
    # Give Render time to mark the new HTTP instance healthy and retire the old
    # pre-lock instance during the first rollout of this protection.
    if POLLING_STARTUP_GRACE_SECONDS:
        log.info("Waiting %ss for rolling-deploy handoff before Telegram polling", POLLING_STARTUP_GRACE_SECONDS)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLLING_STARTUP_GRACE_SECONDS)
        except asyncio.TimeoutError:
            pass

    lock = redis.lock(POLLING_LOCK_KEY, timeout=POLLING_LOCK_TTL_SECONDS, blocking=False)
    acquired = False
    while not stop.is_set():
        try:
            acquired = await lock.acquire(blocking=False)
        except Exception:
            log.exception("Could not acquire Telegram polling lease; retrying")
        if acquired:
            break
        log.warning("Telegram polling lease is held by another instance; waiting")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLLING_LOCK_RETRY_SECONDS)
        except asyncio.TimeoutError:
            pass

    if not acquired:
        log.info("Shutdown requested before Telegram polling lease was acquired")
        return

    log.info("Telegram polling lease acquired")
    renewal_stop = asyncio.Event()
    renewal_task = asyncio.create_task(_renew_polling_lock(lock, dispatcher, renewal_stop))
    async def _start_polling() -> None:
        global telegram_transport_ready
        telegram_transport_ready = True
        try:
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                polling_timeout=POLLING_TIMEOUT_SECONDS,
                handle_signals=False,
            )
        finally:
            telegram_transport_ready = False

    polling_task = asyncio.create_task(_start_polling())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            {polling_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done and not polling_task.done():
            await dispatcher.stop_polling()
        await polling_task
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        global telegram_transport_ready
        telegram_transport_ready = False
        renewal_stop.set()
        renewal_task.cancel()
        await asyncio.gather(renewal_task, return_exceptions=True)
        try:
            await lock.release()
            log.info("Telegram polling lease released")
        except LockNotOwnedError:
            log.warning("Telegram polling lease was no longer owned at shutdown")
        except Exception:
            log.exception("Could not release Telegram polling lease")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=RedisStorage.from_url(
        settings.redis_url, state_ttl=FSM_STATE_TTL, data_ttl=FSM_STATE_TTL
    ))
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    prices = PriceService(redis, http)

    dp["prices"] = prices
    dp["redis"] = redis
    dp.update.outer_middleware(KillSwitchMiddleware(redis))
    dp.update.middleware(DbSessionMiddleware())
    dp.message.middleware(ThrottleMiddleware(redis))
    dp.message.middleware(MessageTraceMiddleware())
    dp.callback_query.middleware(CallbackTraceMiddleware())
    register_routers(dp)
    log.info("Telegram routers registered: %s", ",".join(router.name or "unnamed" for router in dp.sub_routers))

    # Profile metadata is stable and should not be rewritten on every restart:
    # Telegram rate-limits these methods aggressively. Opt in only when deliberately
    # changing the name/description. The command menu is refreshed on each startup.
    if settings.update_bot_profile_on_startup:
        profile_updates = [
            ("name", lambda: bot.set_my_name(name="Orbsat Crypto Alerts")),
            ("description", lambda: bot.set_my_description(
                description=(
                    "Set simple crypto price alerts in a few taps. Choose a coin, choose whether "
                    "it should rise or fall, and pick a target. Get a Telegram notification when "
                    "your level is reached. Includes BTC, ETH, SOL and more, plus Ethereum gas alerts. "
                    "Free alerts available; optional Premium is paid in Telegram Stars. Market data may "
                    "be delayed. Not financial advice."
                )
            )),
            ("short description", lambda: bot.set_my_short_description(
                short_description="Crypto price alerts in a few taps. Track BTC, ETH, SOL and more."
            )),
        ]
        for label, update_profile in profile_updates:
            try:
                await update_profile()
            except Exception:
                log.exception("Could not update Telegram %s; continuing startup", label)

    try:
        await bot.set_my_commands(COMMANDS)
    except Exception:
        log.exception("Could not update Telegram command menu; continuing startup")

    app = build_web_app(redis)
    stop = asyncio.Event()
    worker_task = asyncio.create_task(worker.alert_loop(bot, prices, redis, stop))
    live_runtime_task = asyncio.create_task(live_runtime_loop(bot, redis, http, stop))
    runner = None
    try:
        if settings.webhook_base_url:
            # Never expose an unauthenticated webhook when no secret was configured.
            # Telegram receives this generated value in set_webhook below.
            webhook_secret = settings.webhook_secret or secrets.token_urlsafe(32)
            SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=webhook_secret).register(
                app, path=settings.webhook_path
            )
            setup_application(app, dp, bot=bot)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, settings.host, settings.port).start()
            await bot.set_webhook(
                settings.webhook_base_url.rstrip("/") + settings.webhook_path,
                secret_token=webhook_secret,
                allowed_updates=dp.resolve_used_update_types(),
            )
            global telegram_transport_ready
            telegram_transport_ready = True
            log.info("webhook mode on :%s", settings.port)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            await stop.wait()
        else:
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, settings.host, settings.port).start()
            await bot.delete_webhook(drop_pending_updates=False)
            log.info("polling mode (health on :%s)", settings.port)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            await run_polling_with_lock(dp, bot, redis, stop)
    finally:
        telegram_transport_ready = False
        stop.set()
        try:
            await asyncio.wait_for(live_runtime_task, timeout=5)
        except asyncio.TimeoutError:
            live_runtime_task.cancel()
            await asyncio.gather(live_runtime_task, return_exceptions=True)
        try:
            await asyncio.wait_for(worker_task, timeout=10)
        except asyncio.TimeoutError:
            log.warning("alert worker did not stop promptly; cancelling during shutdown")
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        if runner:
            await runner.cleanup()
        await http.close()
        await bot.session.close()
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
