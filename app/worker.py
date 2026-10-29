import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError
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


def should_pause_alert(
    is_premium: bool, kind: str, alert_index: int, free_limit: int, premium_limit: int
) -> bool:
    """Return whether an active alert exceeds the user's current entitlement."""
    if kind == "gas" and not is_premium:
        return True
    limit = premium_limit if is_premium else free_limit
    return alert_index > limit


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
            select(Alert, User.language, User.premium_until, User.id)
            .join(User, User.id == Alert.user_id)
            .where(Alert.active.is_(True), User.suspended.is_(False))
            .order_by(Alert.user_id, Alert.created_at, Alert.id)
        )).all()
        # Preserve alert configurations after Premium expiry, but pause gas alerts and
        # price alerts beyond the Free-plan allowance. Renewal automatically unpauses them.
        now = utcnow()
        price_counts: dict[int, int] = {}
        premium_counts: dict[int, int] = {}
        eligible_rows = []
        for alert, language, premium_until, user_id in rows:
            premium = user_id == settings.owner_id or (premium_until is not None and premium_until > now)
            if premium:
                premium_counts[user_id] = premium_counts.get(user_id, 0) + 1
                alert_index = premium_counts[user_id]
            else:
                if alert.kind == "price":
                    price_counts[user_id] = price_counts.get(user_id, 0) + 1
                alert_index = price_counts.get(user_id, 0)
            should_pause = should_pause_alert(
                premium, alert.kind, alert_index,
                settings.free_alert_limit, settings.premium_alert_limit,
            )
            if alert.entitlement_paused != should_pause:
                alert.entitlement_paused = should_pause
            if not should_pause:
                eligible_rows.append((alert, language))
        await s.commit()
    rows = eligible_rows
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


async def _renew_worker_lock(lock, stop: asyncio.Event) -> None:
    """Keep the distributed worker lock alive during long alert batches."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
            return
        except asyncio.TimeoutError:
            pass
        try:
            if not await lock.extend(90, replace_ttl=True):
                log.warning("alert worker lock could not be renewed")
                return
        except LockNotOwnedError:
            log.warning("alert worker lock expired before renewal")
            return
        except Exception:
            log.exception("alert worker lock renewal failed")
            return


async def _run_check_with_lock(bot: Bot, prices: PriceService, redis: Redis) -> bool:
    """Ensure only one overlapping deployment/instance sends alerts at a time."""
    lock = redis.lock("bot:alert-worker:check", timeout=90, blocking=False)
    if not await lock.acquire(blocking=False):
        log.debug("skipping alert check; another instance owns the worker lock")
        return False

    stop_renewal = asyncio.Event()
    renewal_task = asyncio.create_task(_renew_worker_lock(lock, stop_renewal))
    try:
        await check_once(bot, prices)
        return True
    finally:
        stop_renewal.set()
        renewal_task.cancel()
        await asyncio.gather(renewal_task, return_exceptions=True)
        try:
            await lock.release()
        except LockNotOwnedError:
            log.warning("alert worker lock was no longer owned at release")


async def alert_loop(bot: Bot, prices: PriceService, redis: Redis, stop: asyncio.Event) -> None:
    log.info("alert worker started, every %ss", settings.check_interval)
    while not stop.is_set():
        try:
            if not await redis.exists(KILL_KEY):
                await _run_check_with_lock(bot, prices, redis)
        except PriceError as e:
            log.warning("price source error: %s", e)
        except Exception:
            log.exception("alert check crashed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.check_interval)
        except asyncio.TimeoutError:
            pass
    log.info("alert worker stopped")
