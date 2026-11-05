from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import User, Watchlist
from .handlers import get_user
from .parsing import fmt_usd
from .prices import PriceError, PriceService

router = Router(name="watchlists")
MAX_WATCHLIST_ITEMS = 10
SUPPORTED = (
    ("btc", "Bitcoin"), ("eth", "Ethereum"), ("sol", "Solana"),
    ("bnb", "BNB"), ("xrp", "XRP"), ("ada", "Cardano"),
    ("doge", "Dogecoin"), ("ton", "Toncoin"), ("trx", "TRON"),
    ("avax", "Avalanche"), ("dot", "Polkadot"), ("link", "Chainlink"),
    ("ltc", "Litecoin"), ("usdt", "Tether"), ("usdc", "USDC"),
)


def _label(language: str, english: str, arabic: str) -> str:\n    return arabic if language == "ar" else english\n\n\ndef watchlist_keyboard(rows: list[Watchlist], language: str = "en") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"✖ {row.symbol.upper()}", callback_data=f"wl:del:{row.id}")]
        for row in rows
    ]
    buttons.append([InlineKeyboardButton(text=_label(language, "＋ Add asset", "＋ إضافة أصل"), callback_data="wl:add")])
    buttons.append([InlineKeyboardButton(text=_label(language, "⌂ Dashboard", "⌂ الرئيسية"), callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def asset_picker_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"{symbol.upper()} · {name}", callback_data=f"wl:add:{symbol}")]
        for symbol, name in SUPPORTED
    ]
    buttons.append([InlineKeyboardButton(text=_label(language, "‹ Back to watchlist", "‹ العودة إلى قائمة المتابعة"), callback_data="wl:open")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def render_watchlist(session: AsyncSession, user: User, prices: PriceService) -> tuple[str, InlineKeyboardMarkup]:
    rows = list((await session.scalars(
        select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.created_at, Watchlist.id)
    )).all())
    if not rows:
        body = "⭐ <b>Your watchlist is empty</b>\nAdd assets to keep their latest prices together."
    else:
        try:
            quotes = await prices.prices([row.coin_id for row in rows], allow_stale=True)
        except PriceError:
            quotes = {}
        lines = [_label(user.language, "⭐ <b>Your watchlist</b>", "⭐ <b>قائمة المتابعة</b>")]
        for row in rows:
            value = quotes.get(row.coin_id)
            price = fmt_usd(value) if value is not None else _label(user.language, "Quote unavailable", "السعر غير متاح")
            lines.append(f"• <b>{row.symbol.upper()}</b> — {price}")
        body = "\n".join(lines)
    body += f"\n\n{len(rows)}/{MAX_WATCHLIST_ITEMS} assets"
    return body, watchlist_keyboard(rows, user.language)


@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message, session: AsyncSession, prices: PriceService):
    user = await get_user(session, message.from_user)
    text, keyboard = await render_watchlist(session, user, prices)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "wl:open")
async def cb_watchlist(callback: CallbackQuery, session: AsyncSession, prices: PriceService):
    user = await get_user(session, callback.from_user)
    text, keyboard = await render_watchlist(session, user, prices)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "wl:add")
async def cb_watchlist_add(callback: CallbackQuery):
    if callback.message:
        await callback.message.edit_text(
            "Choose an asset to add. Your watchlist supports up to 10 assets.",
            reply_markup=asset_picker_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("wl:add:"))
async def cb_watchlist_add_asset(callback: CallbackQuery, session: AsyncSession, prices: PriceService):
    user = await get_user(session, callback.from_user)
    symbol = (callback.data or "").rsplit(":", 1)[-1].lower()
    if symbol not in {item[0] for item in SUPPORTED}:
        await callback.answer("Unsupported asset.", show_alert=True)
        return
    coin = await prices.resolve(symbol)
    if coin is None:
        await callback.answer("Asset could not be resolved. Try again later.", show_alert=True)
        return
    count = await session.scalar(
        select(Watchlist.id).where(Watchlist.user_id == user.id).limit(MAX_WATCHLIST_ITEMS)
    )
    rows = list((await session.scalars(select(Watchlist).where(Watchlist.user_id == user.id))).all())
    if any(row.coin_id == coin.id for row in rows):
        await callback.answer(f"{symbol.upper()} is already in your watchlist.", show_alert=True)
        return
    if len(rows) >= MAX_WATCHLIST_ITEMS:
        await callback.answer(f"Watchlist limit is {MAX_WATCHLIST_ITEMS} assets.", show_alert=True)
        return
    await session.execute(
        pg_insert(Watchlist)
        .values(user_id=user.id, coin_id=coin.id, symbol=coin.symbol)
        .on_conflict_do_nothing(constraint="uq_watchlist_user_coin")
    )
    await session.flush()
    text, keyboard = await render_watchlist(session, user, prices)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer(f"Added {symbol.upper()}.")


@router.callback_query(F.data.startswith("wl:del:"))
async def cb_watchlist_remove(callback: CallbackQuery, session: AsyncSession, prices: PriceService):
    user = await get_user(session, callback.from_user)
    try:
        row_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Invalid watchlist item.", show_alert=True)
        return
    row = await session.scalar(
        select(Watchlist).where(Watchlist.id == row_id, Watchlist.user_id == user.id)
    )
    if row is None:
        await callback.answer("That item is already removed.", show_alert=True)
        return
    await session.delete(row)
    await session.flush()
    text, keyboard = await render_watchlist(session, user, prices)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer(f"Removed {row.symbol.upper()}.")
