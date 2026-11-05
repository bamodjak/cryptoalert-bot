"""Telegram handlers for market data, movers and quantitative research."""
import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .advisor import AdvisorUnavailable, build_advisory, format_advisory
from .asset_detail import asset_detail_keyboard, asset_detail_text
from .i18n import tr
from .keyboards import edit_message_if_changed, nav_keyboard
from .market_views import (
    crypto_prices_keyboard, crypto_prices_text, market_movers_keyboard,
    market_movers_text, market_snapshot_text,
)
from .parsing import fmt_usd
from .prices import PriceError, PriceService
from .users import get_user

market_router = Router(name="market")

@market_router.message(Command("price"))
async def cmd_price(m: Message, command: CommandObject, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    if not command.args:
        return await m.answer(tr(u.language, "price_usage"), reply_markup=nav_keyboard(u.id, lang=u.language))
    try:
        coin = await prices.resolve(command.args.split()[0])
        if not coin:
            return await m.answer(tr(u.language, "unknown_coin"), reply_markup=nav_keyboard(u.id, lang=u.language))
        px = (await prices.prices([coin.id])).get(coin.id)
    except PriceError:
        return await m.answer(tr(u.language, "price_busy"), reply_markup=nav_keyboard(u.id, lang=u.language))
    if px is None:
        return await m.answer(tr(u.language, "no_price"), reply_markup=nav_keyboard(u.id, lang=u.language))
    await m.answer(
        f"<b>{coin.symbol.upper()}</b> ({html.escape(coin.name)}): {fmt_usd(px)}",
        reply_markup=nav_keyboard(u.id, lang=u.language),
    )


@market_router.message(Command("prices"))
async def cmd_prices(m: Message, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    try:
        payload = await prices.top_coins()
        text = crypto_prices_text(payload, u.language)
        keyboard = crypto_prices_keyboard(payload["coins"], u.language)
    except PriceError:
        text = tr(u.language, "crypto_prices_unavailable")
        keyboard = nav_keyboard(u.id, lang=u.language)
    await m.answer(text, reply_markup=keyboard)


@market_router.callback_query(F.data.startswith("prices:"))
async def cb_crypto_prices(cb: CallbackQuery, session: AsyncSession, prices: PriceService):
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass
    u = await get_user(session, cb.from_user)
    if not cb.message:
        return
    try:
        payload = await prices.top_coins()
        action = (cb.data or "").split(":")
        if len(action) == 3 and action[1] == "coin":
            rank = int(action[2])
            coin = next((item for item in payload["coins"] if item["rank"] == rank), None)
            if coin is None:
                raise PriceError("Selected market rank is no longer available")
            text = asset_detail_text(coin, u.language, payload, advanced=u.advanced_mode)
            keyboard = asset_detail_keyboard(coin["symbol"], u.language, "prices:open")
        else:
            text = crypto_prices_text(payload, u.language)
            keyboard = crypto_prices_keyboard(payload["coins"], u.language)
    except (PriceError, ValueError):
        text = tr(u.language, "crypto_prices_unavailable")
        keyboard = nav_keyboard(u.id, lang=u.language)
    try:
        await cb.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _market_snapshot(prices: PriceService, language: str) -> str:
    return market_snapshot_text(await prices.market_overview(), language)


@market_router.message(Command("market"))
async def cmd_market(m: Message, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    try:
        text = await _market_snapshot(prices, u.language)
    except PriceError:
        text = tr(u.language, "market_snapshot_unavailable")
    await m.answer(text, reply_markup=nav_keyboard(u.id, lang=u.language))


@market_router.callback_query(F.data == "dash:market")
async def cb_market(cb: CallbackQuery, session: AsyncSession, prices: PriceService):
    u = await get_user(session, cb.from_user)
    await cb.answer()
    if not cb.message:
        return
    try:
        text = await _market_snapshot(prices, u.language)
    except PriceError:
        text = tr(u.language, "market_snapshot_unavailable")
    await edit_message_if_changed(cb.message, text, nav_keyboard(u.id, lang=u.language))


@market_router.message(Command("movers"))
async def cmd_movers(m: Message, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    try:
        snapshot = await prices.market_movers()
        text = market_movers_text(snapshot, u.language)
        keyboard = market_movers_keyboard(snapshot, u.language)
    except PriceError:
        text = tr(u.language, "market_movers_unavailable")
        keyboard = nav_keyboard(u.id, lang=u.language)
    await m.answer(text, reply_markup=keyboard)


@market_router.callback_query(F.data == "dash:movers")
async def cb_movers(cb: CallbackQuery, session: AsyncSession, prices: PriceService):
    u = await get_user(session, cb.from_user)
    await cb.answer()
    if not cb.message:
        return
    try:
        snapshot = await prices.market_movers()
        text = market_movers_text(snapshot, u.language)
        keyboard = market_movers_keyboard(snapshot, u.language)
    except PriceError:
        text = tr(u.language, "market_movers_unavailable")
        keyboard = nav_keyboard(u.id, lang=u.language)
    await edit_message_if_changed(cb.message, text, keyboard)


@market_router.callback_query(F.data.startswith("asset:mover:"))
async def cb_mover_asset(cb: CallbackQuery, session: AsyncSession, prices: PriceService):
    u = await get_user(session, cb.from_user)
    if not cb.message:
        await cb.answer()
        return
    coin_id = (cb.data or "").rsplit(":", 1)[-1]
    try:
        snapshot = await prices.market_movers()
    except PriceError:
        await cb.answer(tr(u.language, "market_movers_unavailable"), show_alert=True)
        return
    coin = next((item for item in snapshot["gainers"] + snapshot["losers"] if item["id"] == coin_id), None)
    if coin is None:
        await cb.answer(tr(u.language, "invalid_option"), show_alert=True)
        return
    detail = {**coin, "rank": None}
    await edit_message_if_changed(
        cb.message,
        asset_detail_text(detail, u.language, snapshot, advanced=u.advanced_mode),
        asset_detail_keyboard(coin["symbol"], u.language, "dash:movers"),
    )
    await cb.answer()


@market_router.message(Command("signals"))
async def cmd_signals(m: Message, session: AsyncSession, prices: PriceService, redis: Redis):
    u = await get_user(session, m.from_user)
    if not u.is_premium:
        return await m.answer(tr(u.language, "advisor_premium_only"), reply_markup=nav_keyboard(u.id, lang=u.language))
    try:
        rows = await build_advisory(prices.http, redis)
        text = format_advisory(rows, u.language)
    except AdvisorUnavailable:
        text = tr(u.language, "advisor_unavailable")
    await m.answer(text, reply_markup=nav_keyboard(u.id, lang=u.language))


@market_router.callback_query(F.data == "advisor:open")
async def cb_advisor(cb: CallbackQuery, session: AsyncSession, prices: PriceService, redis: Redis):
    await cb.answer()
    u = await get_user(session, cb.from_user)
    if not cb.message:
        return
    if not u.is_premium:
        return await cb.message.edit_text(tr(u.language, "advisor_premium_only"), reply_markup=nav_keyboard(u.id, lang=u.language))
    try:
        rows = await build_advisory(prices.http, redis)
        text = format_advisory(rows, u.language)
    except AdvisorUnavailable:
        text = tr(u.language, "advisor_unavailable")
    await edit_message_if_changed(cb.message, text, nav_keyboard(u.id, lang=u.language))
