from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import User, Watchlist
from .assets import CORE_ASSETS, get_asset
from .asset_detail import asset_detail_keyboard, asset_detail_text
from .users import get_user
from .parsing import fmt_usd
from .prices import PriceError, PriceService
from .i18n import tr

router = Router(name="watchlists")
MAX_WATCHLIST_ITEMS = 10
SUPPORTED = tuple((asset.symbol, asset.name) for asset in CORE_ASSETS)


def _label(language: str, english: str, arabic: str) -> str:
    return arabic if language == "ar" else english


def watchlist_keyboard(rows: list[Watchlist], language: str = "en") -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text=row.symbol.upper(), callback_data=f"wl:view:{row.id}"),
            InlineKeyboardButton(text="✖", callback_data=f"wl:del:{row.id}"),
        ]
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
        body = tr(user.language, "watchlist_empty")
    else:
        try:
            quotes = await prices.prices([row.coin_id for row in rows], allow_stale=True)
        except PriceError:
            quotes = {}
        lines = [tr(user.language, "watchlist_header")]
        for row in rows:
            value = quotes.get(row.coin_id)
            price = fmt_usd(value) if value is not None else tr(user.language, "watchlist_quote_unavailable")
            lines.append(f"• <b>{row.symbol.upper()}</b> — {price}")
        body = "\n".join(lines)
    body += "\n\n" + tr(user.language, "watchlist_count", count=len(rows), limit=MAX_WATCHLIST_ITEMS)
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
async def cb_watchlist_add(callback: CallbackQuery, session: AsyncSession):
    user = await get_user(session, callback.from_user)
    if callback.message:
        await callback.message.edit_text(
            tr(user.language, "watchlist_choose_asset", limit=MAX_WATCHLIST_ITEMS),
            reply_markup=asset_picker_keyboard(user.language),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("wl:add:"))
async def cb_watchlist_add_asset(callback: CallbackQuery, session: AsyncSession, prices: PriceService):
    user = await get_user(session, callback.from_user)
    symbol = (callback.data or "").rsplit(":", 1)[-1].lower()
    coin = await prices.resolve(symbol)
    if coin is None:
        user = await get_user(session, callback.from_user)
        await callback.answer(tr(user.language, "watchlist_unsupported"), show_alert=True)
        return
    # Serialize concurrent additions for this account before enforcing its cap.
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    rows = list((await session.scalars(select(Watchlist).where(Watchlist.user_id == user.id))).all())
    if any(row.coin_id == coin.id for row in rows):
        await callback.answer(tr(user.language, "watchlist_duplicate", symbol=symbol.upper()), show_alert=True)
        return
    if len(rows) >= MAX_WATCHLIST_ITEMS:
        await callback.answer(tr(user.language, "watchlist_limit", limit=MAX_WATCHLIST_ITEMS), show_alert=True)
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
    await callback.answer(tr(user.language, "watchlist_added", symbol=symbol.upper()))


@router.callback_query(F.data.startswith("wl:view:"))
async def cb_watchlist_view(callback: CallbackQuery, session: AsyncSession, prices: PriceService):
    user = await get_user(session, callback.from_user)
    try:
        row_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer(tr(user.language, "watchlist_item_invalid"), show_alert=True)
        return
    row = await session.scalar(
        select(Watchlist).where(Watchlist.id == row_id, Watchlist.user_id == user.id)
    )
    if row is None:
        await callback.answer(tr(user.language, "watchlist_item_removed"), show_alert=True)
        return
    try:
        quote = (await prices.prices([row.coin_id], allow_stale=True)).get(row.coin_id)
    except PriceError:
        quote = None
    asset = get_asset(row.coin_id)
    coin = {
        "name": asset.name if asset else row.symbol.upper(),
        "symbol": row.symbol,
        "price_usd": quote,
        "rank": None,
        "change_24h": None,
    }
    if callback.message:
        await callback.message.edit_text(
            asset_detail_text(coin, user.language, {"source": "watchlist quote", "stale": False}),
            reply_markup=asset_detail_keyboard(row.symbol, user.language, "wl:open"),
        )
    await callback.answer()
@router.callback_query(F.data.startswith("wl:del:"))
async def cb_watchlist_remove(callback: CallbackQuery, session: AsyncSession, prices: PriceService):
    user = await get_user(session, callback.from_user)
    try:
        row_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer(tr(user.language, "watchlist_item_invalid"), show_alert=True)
        return
    row = await session.scalar(
        select(Watchlist).where(Watchlist.id == row_id, Watchlist.user_id == user.id)
    )
    if row is None:
        await callback.answer(tr(user.language, "watchlist_item_removed"), show_alert=True)
        return
    await session.delete(row)
    await session.flush()
    text, keyboard = await render_watchlist(session, user, prices)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer(tr(user.language, "watchlist_removed", symbol=row.symbol.upper()))
