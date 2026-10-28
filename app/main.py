import asyncio
import logging
import secrets
import signal
import time

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
from sqlalchemy import func, select, text

from . import worker
from .config import settings
from .db import Alert, Payment, Session, User, engine, utcnow
from .handlers import admin, router
from .admin_tools import public_router as giveaway_public_router, router as admin_tools_router
from .middlewares import KILL_KEY, DbSessionMiddleware, KillSwitchMiddleware, ThrottleMiddleware
from .prices import PriceService

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="start", description="Home & referral link"),
    BotCommand(command="price", description="Current price, e.g. /price btc"),
    BotCommand(command="alert", description="New alert, e.g. /alert btc > 70000"),
    BotCommand(command="alerts", description="Your active alerts"),
    BotCommand(command="gas", description="Ethereum gas now"),
    BotCommand(command="gasalert", description="Gas alert in gwei (Premium)"),
    BotCommand(command="premium", description="More alerts + gas alerts"),
    BotCommand(command="help", description="How it works"),
]

CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}


def register_routers(dp: Dispatcher) -> None:
    # Specific admin-tools callbacks must precede the generic admin:* callback handler.
    dp.include_routers(admin_tools_router, admin, giveaway_public_router, router)


def build_web_app(redis: Redis) -> web.Application:
    async def health(_: web.Request) -> web.Response:
        try:
            await redis.ping()
            async with Session() as s:
                await s.execute(text("SELECT 1"))
        except Exception:
            log.exception("health check failed")
            return web.json_response({"ok": False, "error": "dependency check failed"}, status=503, headers=CORS_HEADERS)
        stale = worker.last_success and time.time() - worker.last_success > settings.check_interval * 5
        if stale:
            return web.json_response({"ok": False, "error": "alert worker stale"}, status=503, headers=CORS_HEADERS)
        return web.json_response({"ok": True}, headers=CORS_HEADERS)

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
            stars = await s.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.refunded.is_(False))) or 0
            shared = await s.scalar(select(func.coalesce(func.sum(User.affiliation_shares), 0))) or 0
            referrals = await s.scalar(select(func.coalesce(func.sum(User.referrals_count), 0))) or 0
        killed = bool(await redis.exists(KILL_KEY))
        return web.json_response(
            {
                "ok": True,
                "users": users,
                "premium_users": premium,
                "active_alerts": alerts,
                "stars_earned": stars,
                "affiliation_shared": shared,
                "referrals_joined": referrals,
                "killed": killed,
            },
            headers=CORS_HEADERS,
        )

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/metrics", metrics_view)
    app.router.add_get("/stats.json", stats_json)
    return app


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=RedisStorage.from_url(settings.redis_url))
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    prices = PriceService(redis, http)

    dp["prices"] = prices
    dp["redis"] = redis
    dp.update.outer_middleware(KillSwitchMiddleware(redis))
    dp.update.middleware(DbSessionMiddleware())
    dp.message.middleware(ThrottleMiddleware(redis))
    register_routers(dp)

    await bot.set_my_commands(COMMANDS)

    app = build_web_app(redis)
    stop = asyncio.Event()
    worker_task = asyncio.create_task(worker.alert_loop(bot, prices, redis, stop))
    runner = None
    try:
        if settings.webhook_base_url:
            SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.webhook_secret or None).register(
                app, path=settings.webhook_path
            )
            setup_application(app, dp, bot=bot)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, settings.host, settings.port).start()
            await bot.set_webhook(
                settings.webhook_base_url.rstrip("/") + settings.webhook_path,
                secret_token=settings.webhook_secret or None,
                allowed_updates=dp.resolve_used_update_types(),
            )
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
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        stop.set()
        await worker_task
        if runner:
            await runner.cleanup()
        await http.close()
        await bot.session.close()
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
