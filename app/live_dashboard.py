"""Premium live market dashboard delivered entirely in Telegram chat.

Prices are consumed from Binance's public trade WebSocket stream and dashboard
messages are coalesced to a safe refresh cadence rather than edited on every tick.
"""
import asyncio
import json
import logging
import time

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import User, Session
from .i18n import tr
from .parsing import fmt_usd
from .prices import PriceError, PriceService

log = logging.getLogger(__name__)
router = Router(name="live_dashboard")

# These pairs mirror the assets already supported by PriceService. Pair availability
# can vary by exchange; a missing stream simply shows as unavailable in the dashboard.
COINS = (
    ("bitcoin", "BTC", "Bitcoin", "btcusdt"),
    ("ethereum", "ETH", "Ethereum", "ethusdt"),
    ("solana", "SOL", "Solana", "solusdt"),
    ("binancecoin", "BNB", "BNB", "bnbusdt"),
    ("ripple", "XRP", "XRP", "xrpusdt"),
    ("dogecoin", "DOGE", "Dogecoin", "dogeusdt"),
    ("cardano", "ADA", "Cardano", "adausdt"),
    ("the-open-network", "TON", "Toncoin", "tonusdt"),
    ("tron", "TRX", "TRON", "trxusdt"),
    ("avalanche-2", "AVAX", "Avalanche", "avaxusdt"),
    ("polkadot", "DOT", "Polkadot", "dotusdt"),
    ("chainlink", "LINK", "Chainlink", "linkusdt"),
    ("litecoin", "LTC", "Litecoin", "ltcusdt"),
    ("tether", "USDT", "Tether", ""),
    ("usd-coin", "USDC", "USDC", "usdcusdt"),
)
PAIR_TO_COIN = {pair.upper(): (coin_id, symbol, name) for coin_id, symbol, name, pair in COINS if pair}
DASHBOARDS_KEY = "live:dashboards"
DASHBOARD_REFRESH_SECONDS = 3
PRICE_TTL_SECONDS = 30


def dashboard_keyboard(language: str) -> InlineKeyboardMarkup:
    # Updates are automatic; do not show a refresh control that cannot force a refresh.
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(language, "live_close"), callback_data="live:close"),
    ]])


def format_dashboard(prices: dict[str, tuple[float, float]], language: str, now: float | None = None) -> str:
    """Format the latest received trades; each tuple is (USD price, receive timestamp)."""
    now = now or time.time()
    lines = [tr(language, "live_title"), tr(language, "live_subtitle"), ""]
    for coin_id, symbol, name, _pair in COINS:
        quote = prices.get(coin_id)
        if not quote:
            lines.append(f"• <b>{symbol}</b> — {tr(language, 'live_waiting')}")
            continue
        value, timestamp = quote
        age = max(0, int(now - timestamp))
        marker = "🟢" if age <= PRICE_TTL_SECONDS else "⚪"
        lines.append(f"{marker} <b>{symbol}</b> {fmt_usd(value)}  <i>{age}s</i>")
    lines.extend(["", tr(language, "live_footer")])
    return "\n".join(lines)


@router.message(Command("live"))
async def cmd_live(message: Message, session: AsyncSession, redis: Redis, prices: PriceService):
    if message.chat.type != "private":
        return await message.answer(tr("en", "live_private_only"))
    user = await session.get(User, message.from_user.id)
    if user is None:
        return await message.answer(tr("en", "live_start_hint"))
    if not user.is_premium:
        return await message.answer(tr(user.language, "live_premium_only"))
    # Reuse the user's existing dashboard instead of orphaning older messages.
    existing_raw = await redis.hget(DASHBOARDS_KEY, str(user.id))
    if existing_raw:
        try:
            existing = json.loads(existing_raw)
            existing["language"] = user.language
            existing["last_text"] = None
            await redis.hset(DASHBOARDS_KEY, str(user.id), json.dumps(existing))
            await message.answer(tr(user.language, "live_started_hint"))
            return
        except Exception:
            await redis.hdel(DASHBOARDS_KEY, str(user.id))
    # Provider-cache timestamps are unknown; show seeded quotes as stale until the
    # WebSocket supplies a timestamped trade.
    try:
        initial = await prices.prices([coin_id for coin_id, *_ in COINS], allow_stale=True)
    except PriceError:
        initial = {}
    now = time.time()
    seeded = {coin_id: (float(value), now - PRICE_TTL_SECONDS - 1) for coin_id, value in initial.items()}
    text = format_dashboard(seeded, user.language)
    sent = await message.answer(text, reply_markup=dashboard_keyboard(user.language))
    record = {
        "user_id": user.id,
        "chat_id": sent.chat.id,
        "message_id": sent.message_id,
        "language": user.language,
        "last_text": text,
    }
    await redis.hset(DASHBOARDS_KEY, str(user.id), json.dumps(record))
    await message.answer(tr(user.language, "live_started_hint"))


@router.callback_query(F.data.startswith("live:"))
async def cb_live(callback: CallbackQuery, redis: Redis, session: AsyncSession):
    if not callback.message:
        await callback.answer()
        return
    user = await session.get(User, callback.from_user.id)
    if user is None:
        await callback.answer()
        return
    current_raw = await redis.hget(DASHBOARDS_KEY, str(user.id))
    try:
        current = json.loads(current_raw) if current_raw else {}
    except (ValueError, TypeError):
        current = {}
    is_current = (
        int(current.get("chat_id", -1)) == callback.message.chat.id
        and int(current.get("message_id", -1)) == callback.message.message_id
    )
    if not is_current:
        await callback.answer("This dashboard is no longer active.", show_alert=True)
        return
    if not user.is_premium or user.suspended:
        await redis.hdel(DASHBOARDS_KEY, str(user.id))
        await callback.answer("Premium access is required.", show_alert=True)
        try:
            await callback.message.edit_text(tr(user.language, "live_premium_only"))
        except TelegramBadRequest:
            pass
        return
    action = (callback.data or "").split(":", 1)[-1]
    if action == "close":
        await redis.hdel(DASHBOARDS_KEY, str(user.id))
        await callback.answer()
        try:
            await callback.message.edit_text(tr(user.language, "live_closed"))
        except TelegramBadRequest:
            pass
    else:
        # Kept for old keyboards created before the refresh button was removed.
        await callback.answer("Dashboard updates automatically.")


async def live_market_stream(redis: Redis, http: aiohttp.ClientSession, stop: asyncio.Event) -> None:
    """Subscribe to public individual-trade events and cache the most recent price per coin."""
    streams = "/".join(f"{pair}@trade" for _coin_id, _symbol, _name, pair in COINS if pair)
    url = f"wss://stream.binance.com:9443/stream?streams={streams}"
    while not stop.is_set():
        try:
            async with http.ws_connect(url, heartbeat=20, receive_timeout=45, max_msg_size=2**20) as ws:
                log.info("premium live-price trade stream connected")
                async for message in ws:
                    if stop.is_set():
                        break
                    if message.type == aiohttp.WSMsgType.TEXT:
                        try:
                            event = json.loads(message.data).get("data", {})
                            coin = PAIR_TO_COIN.get(str(event.get("s", "")).upper())
                            if not coin:
                                continue
                            price = float(event["p"])
                            if price <= 0:
                                continue
                            coin_id, _symbol, _name = coin
                            await redis.set(f"live:px:{coin_id}", price, ex=PRICE_TTL_SECONDS)
                            await redis.set(f"live:ts:{coin_id}", time.time(), ex=PRICE_TTL_SECONDS)
                            # USDT is a USD-pegged stablecoin and has no USDT/USDT market pair.
                            # Refresh its approximate peg alongside the USDC/USDT trade stream.
                            if str(event.get("s", "")).upper() == "USDCUSDT":
                                await redis.set("live:px:tether", 1.0, ex=PRICE_TTL_SECONDS)
                                await redis.set("live:ts:tether", time.time(), ex=PRICE_TTL_SECONDS)
                        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                            continue
                    elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("live-price stream disconnected (%s); reconnecting", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass


async def live_dashboard_loop(bot: Bot, redis: Redis, stop: asyncio.Event) -> None:
    """Coalesce streaming ticks into edits of each Premium user's existing message."""
    log.info("live dashboard updater started")
    while not stop.is_set():
        try:
            dashboards = await redis.hgetall(DASHBOARDS_KEY)
            if dashboards:
                ids = [int(value) for value in dashboards if value.isdigit()]
                async with Session() as session:
                    users = (await session.scalars(select(User).where(User.id.in_(ids)))).all() if ids else []
                    by_id = {user.id: user for user in users}
                coin_ids = [coin_id for coin_id, *_ in COINS]
                cached_values = await redis.mget([f"live:px:{cid}" for cid in coin_ids])
                cached_times = await redis.mget([f"live:ts:{cid}" for cid in coin_ids])
                quotes = {
                    coin_id: (float(value), float(timestamp))
                    for coin_id, value, timestamp in zip(coin_ids, cached_values, cached_times)
                    if value is not None and timestamp is not None
                }
                for user_key, raw in dashboards.items():
                    try:
                        record = json.loads(raw)
                        user_id = int(user_key)
                        user = by_id.get(user_id)
                        if user is None or user.suspended or not user.is_premium:
                            await redis.hdel(DASHBOARDS_KEY, user_key)
                            if user is not None:
                                try:
                                    await bot.edit_message_text(
                                        tr(user.language, "live_premium_expired"),
                                        chat_id=record["chat_id"], message_id=record["message_id"],
                                    )
                                except Exception:
                                    pass
                            continue
                        text = format_dashboard(quotes, record.get("language", user.language))
                        if text == record.get("last_text"):
                            continue
                        try:
                            await bot.edit_message_text(
                                text, chat_id=int(record["chat_id"]),
                                message_id=int(record["message_id"]),
                                reply_markup=dashboard_keyboard(record.get("language", user.language)),
                            )
                            record["last_text"] = text
                            await redis.hset(DASHBOARDS_KEY, user_key, json.dumps(record))
                        except TelegramBadRequest as exc:
                            if "message is not modified" in str(exc).lower():
                                record["last_text"] = text
                                await redis.hset(DASHBOARDS_KEY, user_key, json.dumps(record))
                            else:
                                log.debug("live dashboard edit rejected: %s", exc)
                        except TelegramForbiddenError:
                            await redis.hdel(DASHBOARDS_KEY, user_key)
                        except TelegramRetryAfter as exc:
                            await asyncio.sleep(min(float(exc.retry_after), 3.0))
                        except Exception:
                            log.exception("could not update live dashboard for user")
                        await asyncio.sleep(0.05)
                    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                        await redis.hdel(DASHBOARDS_KEY, user_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("live dashboard updater iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=DASHBOARD_REFRESH_SECONDS)
        except asyncio.TimeoutError:
            pass
    log.info("live dashboard updater stopped")
