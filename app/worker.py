import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from redis.asyncio import Redis
from sqlalchemy import select, update

from . import metrics
from .config import settings
from .db import Alert, Session, User, utcnow
from .i18n import tr
from .middlewares import KILL_KEY
from .parsing import fmt_gwei, fmt_usd
from .prices import PriceError, PriceService

log = logging.getLogger(__name__)
last_success = 0.0


async def _send(bot: Bot, chat_id: int, text: str) -> bool | None:
    """True sent, False user blocked the bot, None transient failure."""
    for _ in range(2):
        try:
            await bot.send_message(chat_id, text)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramForbiddenError:
            return False
        except Exception:
            log.exception("send failed to %s", chat_id)
            break
    metrics.SEND_ERRORS.inc()
    return None


def _alert_message(kind: str, symbol: str | None, value: float, target: float, alert_id: int, op: str, language: str | None) -> str:
    if kind == "gas":
        return tr(language, "gas_alert", value=fmt_gwei(value), target=target, id=alert_id)
    relation = tr(language, "above" if op == ">" else "below")
    return tr(
        language, "price_alert", symbol=symbol or "CRYPTO", value=fmt_usd(value),
        relation=relation, target=fmt_usd(target), id=alert_id,
    )


async def check_once(bot: Bot, prices: PriceService) -> None:
    global last_success
    async with Session() as s:
        rows = (await s.execute(
            select(Alert, User.language)
            .join(User, User.id == Alert.user_id)
            .where(Alert.active.is_(True), User.suspended.is_(False))
        )).all()
    alerts = [row[0] for row in rows]
    languages = {alert.user_id: language for alert, language in rows}
    if not alerts:
        last_success = time.time()
        metrics.LAST_CHECK.set(last_success)
        return

    px = await prices.prices([a.coin_id for a in alerts if a.kind == "price" and a.coin_id])
    gas = None
    if any(a.kind == "gas" for a in alerts):
        try:
            gas = await prices.gas_gwei()
        except PriceError:
            log.warning("gas unavailable this round")

    fired, blocked_users = [], set()
    for a in alerts:
        if a.user_id in blocked_users:
            continue
        value = gas if a.kind == "gas" else px.get(a.coin_id)
        if value is None:
            continue
        if not (value > a.target if a.op == ">" else value < a.target):
            continue
        text = _alert_message(a.kind, a.symbol, value, a.target, a.id, a.op, languages.get(a.user_id))
        ok = await _send(bot, a.user_id, text)
        if ok:
            fired.append(a.id)
            metrics.ALERTS_FIRED.inc()
        elif ok is False:
            blocked_users.add(a.user_id)
        await asyncio.sleep(0.05)  # stay well under Telegram's ~30 msg/s

    async with Session() as s:
        if fired:
            await s.execute(update(Alert).where(Alert.id.in_(fired)).values(active=False, triggered_at=utcnow()))
        if blocked_users:
            await s.execute(update(Alert).where(Alert.user_id.in_(blocked_users)).values(active=False))
            await s.execute(update(User).where(User.id.in_(blocked_users)).values(blocked=True))
        await s.commit()
    last_success = time.time()
    metrics.LAST_CHECK.set(last_success)
    if fired:
        log.info("fired %d alerts", len(fired))


async def alert_loop(bot: Bot, prices: PriceService, redis: Redis, stop: asyncio.Event) -> None:
    log.info("alert worker started, every %ss", settings.check_interval)
    while not stop.is_set():
        try:
            if not await redis.exists(KILL_KEY):
                await check_once(bot, prices)
        except PriceError as e:
            log.warning("price source error: %s", e)
        except Exception:
            log.exception("alert check crashed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.check_interval)
        except asyncio.TimeoutError:
            pass
    log.info("alert worker stopped")
