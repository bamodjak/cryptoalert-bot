from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
from .keyboards import edit_message_if_changed

router = Router(name="watchlists")
MAX_WATCHLIST_ITEMS = 10
SUPPORTED = tuple((asset.symbol, asset.name) for asset in CORE_ASSETS)


class CustomAssetInput(StatesGroup):
    waiting_for_coingecko_id = State()


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
    buttons.append([InlineKeyboardButton(text=_label(language, "＋ Add another crypto (Premium)", "＋ إضافة عملة أخرى (بريميوم)"), callback_data="wl:addcustom")])
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
        await edit_message_if_changed(callback.message, text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "wl:add")
async def cb_watchlist_add(callback: CallbackQuery, session: AsyncSession):
    user = await get_user(session, callback.from_user)
    if callback.message:
        await edit_message_if_changed(
            callback.message, tr(user.language, "watchlist_choose_asset", limit=MAX_WATCHLIST_ITEMS),
            reply_markup=asset_picker_keyboard(user.language),
        )
    await callback.answer()


@router.callback_query(F.data == "wl:addcustom")
async def cb_watchlist_add_custom(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await get_user(session, callback.from_user)
    if not user.is_premium:
        await callback.answer("Custom assets are available to Premium members. Use /premium.", show_alert=True)
        return
    await state.set_state(CustomAssetInput.waiting_for_coingecko_id)
    if callback.message:
        await callback.message.answer(
            "Add a crypto to your Watchlist (Premium)\n\n"
            "1. Open https://www.coingecko.com/en/coins\n"
            "2. Search the exact project name and open its page.\n"
            "3. Copy the final part of its URL (the CoinGecko ID).\n"
            "   Example: https://www.coingecko.com/en/coins/bitcoin → bitcoin\n"
            "4. Send only that ID here.\n\n"
            "I will verify the asset name, ticker and price before adding it. Send /cancel to stop."
        )
    await callback.answer()


@router.message(CustomAssetInput.waiting_for_coingecko_id, Command("cancel"))
async def cancel_custom_asset_input(message: Message, state: FSMContext):
    """Exit custom-asset entry explicitly instead of treating /cancel as an asset ID."""
    await state.clear()
    await message.answer("Custom asset entry cancelled. Use /watchlist to continue.")


@router.message(CustomAssetInput.waiting_for_coingecko_id, F.text, ~F.text.startswith("/"))
async def receive_custom_asset_id(message: Message, session: AsyncSession, prices: PriceService, state: FSMContext):
    user = await get_user(session, message.from_user)
    if not user.is_premium:
        await state.clear()
        await message.answer("Custom assets are a Premium feature. Use /premium.")
        return
    asset_id = (message.text or "").strip().lower()
    import re
    if not re.fullmatch(r"[a-z0-9-]{1,80}", asset_id):
        await message.answer("Invalid ID format. Send the CoinGecko ID only, or /cancel.")
        return
    try:
        metadata = await prices._get(f"/coins/{asset_id}", {"localization":"false","tickers":"false","market_data":"false","community_data":"false","developer_data":"false","sparkline":"false"})
        coin_name = str(metadata.get("name") or "").strip()
        symbol = str(metadata.get("symbol") or "").strip().lower()
        canonical_id = str(metadata.get("id") or "").strip().lower()
        if canonical_id != asset_id or not coin_name or not re.fullmatch(r"[a-z0-9]{1,12}", symbol):
            raise ValueError("Incomplete asset metadata")
        quotes = await prices.prices([canonical_id])
        if canonical_id not in quotes:
            raise PriceError("No current quote for this asset")
    except (PriceError, ValueError, KeyError, TypeError):
        await message.answer("Could not verify that CoinGecko ID or retrieve a current USD price. Check the ID and try again, or /cancel.")
        return
    await session.scalar(select(User).where(User.id == user.id).with_for_update())
    rows = list((await session.scalars(select(Watchlist).where(Watchlist.user_id == user.id))).all())
    if any(row.coin_id == canonical_id for row in rows):
        await state.clear()
        await message.answer(f"{coin_name} ({symbol.upper()}) is already in your Watchlist.")
        return
    if len(rows) >= MAX_WATCHLIST_ITEMS:
        await state.clear()
        await message.answer(f"Your Watchlist is full ({MAX_WATCHLIST_ITEMS} assets). Remove one before adding another.")
        return
    await session.execute(pg_insert(Watchlist).values(user_id=user.id, coin_id=canonical_id, symbol=symbol).on_conflict_do_nothing(constraint="uq_watchlist_user_coin"))
    await session.flush()
    await state.clear()
    text, keyboard = await render_watchlist(session, user, prices)
    await message.answer(f"Verified and added <b>{coin_name}</b> ({symbol.upper()})\n\n{text}", reply_markup=keyboard)

@router.callback_query(F.data.startswith("wl:add:"))
async def cb_watchlist_add_asset(callback: CallbackQuery, session: AsyncSession, prices: PriceService):
    user = await get_user(session, callback.from_user)
    symbol = (callback.data or "").rsplit(":", 1)[-1].lower()
    try:
        coin = await prices.resolve(symbol)
    except PriceError:
        await callback.answer(tr(user.language, "price_busy"), show_alert=True)
        return
    if coin is None:
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
        await edit_message_if_changed(
            callback.message, asset_detail_text(coin, user.language, {"source": "market data", "stale": False}, advanced=user.advanced_mode),
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
