"""Telegram handlers for alert creation, management and history."""
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import Alert, utcnow
from .i18n import tr
from .keyboards import alerts_keyboard, edit_message_if_changed, nav_keyboard
from .parsing import fmt_gwei, fmt_usd, parse_alert, parse_number, parse_percentage
from .prices import PriceError, PriceService
from .users import get_user

log = logging.getLogger(__name__)
def premium_limit_keyboard(language: str = "en") -> InlineKeyboardMarkup:
    """Offer a clear, optional plan-comparison route at Premium limits."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "premium_compare"), callback_data="dash:premium")],
        [InlineKeyboardButton(text=tr(language, "home"), callback_data="nav:home")],
    ])


alert_router = Router(name="alerts")


def is_non_command_text(message: Message) -> bool:
    """Keep slash commands routable while an interactive text prompt is active."""
    return bool(message.text) and not message.text.startswith("/")


class AlertCustomInput(StatesGroup):
    waiting = State()
    confirming = State()


class AlertEditInput(StatesGroup):
    waiting = State()


async def active_count(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Alert).where(
            Alert.user_id == user_id, Alert.active.is_(True), Alert.entitlement_paused.is_(False)
        )
    ) or 0



@alert_router.message(Command("alert"))
async def cmd_alert(m: Message, command: CommandObject, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    try:
        sym, op, target = parse_alert(command.args or "")
    except ValueError:
        return await m.answer(tr(u.language, "alert_usage"), reply_markup=nav_keyboard(u.id, lang=u.language))
    limit = settings.premium_alert_limit if u.is_premium else settings.free_alert_limit
    if await active_count(session, u.id) >= limit:
        extra = "" if u.is_premium else "\n" + tr(u.language, "premium_more")
        return await m.answer(tr(u.language, "limit_reached", limit=limit) + extra, reply_markup=premium_limit_keyboard(u.language) if not u.is_premium else nav_keyboard(u.id, lang=u.language))
    try:
        coin = await prices.resolve(sym)
    except PriceError:
        return await m.answer(tr(u.language, "price_busy"), reply_markup=nav_keyboard(u.id, lang=u.language))
    if not coin:
        return await m.answer(tr(u.language, "unknown_coin"), reply_markup=nav_keyboard(u.id, lang=u.language))
    alert = Alert(user_id=u.id, kind="price", coin_id=coin.id, symbol=coin.symbol.upper(), op=op, target=target)
    session.add(alert)
    await session.flush()
    px = None
    try:
        px = (await prices.prices([coin.id])).get(coin.id)
    except PriceError:
        pass
    await m.answer(
        tr(u.language, "alert_created", id=alert.id, symbol=alert.symbol, word=tr(u.language, "above" if op == ">" else "below"), target=fmt_usd(target)) + (f"\n{tr(u.language, 'current_price')}: {fmt_usd(px)}" if 'px' in locals() and px is not None else ""),
        reply_markup=nav_keyboard(u.id, lang=u.language),
    )


@alert_router.message(Command("gas"))
async def cmd_gas(m: Message, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    try:
        g = await prices.gas_gwei()
    except PriceError:
        return await m.answer(tr(u.language, "gas_busy"), reply_markup=nav_keyboard(u.id, lang=u.language))
    await m.answer(tr(u.language, "gas_price", value=fmt_gwei(g)), reply_markup=nav_keyboard(u.id, lang=u.language))


@alert_router.message(Command("gasalert"))
async def cmd_gasalert(m: Message, command: CommandObject, session: AsyncSession):
    u = await get_user(session, m.from_user)
    if not u.is_premium:
        return await m.answer(tr(u.language, "gas_premium_only"), reply_markup=premium_limit_keyboard(u.language))
    try:
        target = parse_number((command.args or "").replace("gwei", ""))
    except ValueError:
        return await m.answer(tr(u.language, "gasalert_usage"), reply_markup=nav_keyboard(u.id, lang=u.language))
    if await active_count(session, u.id) >= settings.premium_alert_limit:
        return await m.answer(tr(u.language, "limit_reached", limit=settings.premium_alert_limit), reply_markup=nav_keyboard(u.id, lang=u.language))
    alert = Alert(user_id=u.id, kind="gas", symbol="GAS", op="<", target=target)
    session.add(alert)
    await session.flush()
    await m.answer(tr(u.language, "gas_alert_created", id=alert.id, target=target), reply_markup=nav_keyboard(u.id, lang=u.language))


@alert_router.message(Command("alerts"))
async def cmd_alerts(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    rows = (await session.scalars(
        select(Alert).where(Alert.user_id == m.from_user.id, Alert.active.is_(True)).order_by(Alert.id)
    )).all()
    if not rows:
        return await m.answer(tr(u.language, "no_active_alerts_command"), reply_markup=nav_keyboard(u.id, lang=u.language))
    lines = []
    for a in rows:
        word = tr(u.language, "above" if a.op == ">" else "below")
        val = f"{a.target:g} gwei" if a.kind == "gas" else fmt_usd(a.target)
        paused = f"⏸ {tr(u.language, 'entitlement_paused')} · " if a.entitlement_paused else ""
        snoozed = f"⏸ {tr(u.language, 'alert_snoozed')} · " if a.snoozed_until and a.snoozed_until > utcnow() else ""
        lines.append(f"{paused}{snoozed}#{a.id} {a.symbol} {word} {val}")
    await m.answer(
        tr(u.language, "active_alerts_header") + "\n" + "\n".join(lines) + "\n\n" + tr(u.language, "remove_alert_hint"),
        reply_markup=alerts_keyboard(rows, u.id, lang=u.language),
    )


def alert_history_text(rows: list[Alert], language: str, timezone_name: str = "UTC") -> str:
    if not rows:
        return tr(language, "alert_history_empty")
    lines = [tr(language, "alert_history_title")]
    try:
        display_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        display_timezone = ZoneInfo("UTC")
        timezone_name = "UTC"
    for alert in rows:
        relation = tr(language, "above" if alert.op == ">" else "below")
        target = f"{alert.target:g} gwei" if alert.kind == "gas" else fmt_usd(alert.target)
        date = alert.triggered_at.astimezone(display_timezone).strftime("%Y-%m-%d %H:%M") if alert.triggered_at else "—"
        lines.append(tr(language, "alert_history_row", id=alert.id, symbol=alert.symbol, relation=relation, target=target, date=date, timezone=timezone_name))
    return "\n".join(lines)


@alert_router.message(Command("history"))
async def cmd_alert_history(message: Message, session: AsyncSession):
    user = await get_user(session, message.from_user)
    rows = (await session.scalars(
        select(Alert).where(Alert.user_id == user.id, Alert.triggered_at.is_not(None))
        .order_by(Alert.triggered_at.desc()).limit(20)
    )).all()
    await message.answer(alert_history_text(rows, user.language, user.timezone), reply_markup=nav_keyboard(user.id, lang=user.language))


@alert_router.callback_query(F.data == "alerts:history")
async def cb_alert_history(callback: CallbackQuery, session: AsyncSession):
    user = await get_user(session, callback.from_user)
    rows = (await session.scalars(
        select(Alert).where(Alert.user_id == user.id, Alert.triggered_at.is_not(None))
        .order_by(Alert.triggered_at.desc()).limit(20)
    )).all()
    if callback.message:
        await edit_message_if_changed(callback.message, alert_history_text(rows, user.language, user.timezone), nav_keyboard(user.id, lang=user.language))
    await callback.answer()
@alert_router.message(Command("del"))
async def cmd_del(m: Message, command: CommandObject, session: AsyncSession):
    u = await get_user(session, m.from_user)
    try:
        aid = int((command.args or "").strip().lstrip("#"))
    except ValueError:
        return await m.answer(tr(u.language, "delete_usage"), reply_markup=nav_keyboard(u.id, lang=u.language))
    res = await session.execute(delete(Alert).where(Alert.id == aid, Alert.user_id == m.from_user.id))
    await m.answer(
        tr(u.language, "alert_deleted", id=aid) if res.rowcount else tr(u.language, "alert_not_found"),
        reply_markup=nav_keyboard(u.id, lang=u.language),
    )



@alert_router.message(Command("create"))
async def cmd_create(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    await m.answer(f"{tr(u.language, 'create_title')}\n{tr(u.language, 'choose_coin')}", reply_markup=create_coin_keyboard(u.id, lang=u.language))


def create_coin_keyboard(user_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    coins = [
        ("₿ Bitcoin", "btc"), ("Ξ Ethereum", "eth"), ("◎ Solana", "sol"),
        ("BNB", "bnb"), ("XRP", "xrp"), ("DOGE", "doge"),
        ("ADA", "ada"), ("TON", "ton"), ("TRX", "trx"),
        ("AVAX", "avax"), ("DOT", "dot"), ("LINK", "link"),
        ("LTC", "ltc"), ("USDT", "usdt"), ("USDC", "usdc"),
    ]
    rows = [[InlineKeyboardButton(text=label, callback_data=f"create:coin:{symbol}") for label, symbol in coins[i:i + 3]] for i in range(0, len(coins), 3)]
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def edit_if_changed(message: Message, text: str, **kwargs) -> None:
    """Ignore duplicate callback attempts that leave the message unchanged."""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def claim_create_action(redis: Redis, user_id: int, message_id: int, action: str) -> bool:
    """Allow only the first alert-creation action from a given prompt message."""
    key = f"bot:create-alert:once:{user_id}:{message_id}:{action}"
    return bool(await redis.set(key, "1", ex=30, nx=True))


@alert_router.callback_query(F.data.startswith("create:"))
async def cb_create_alert(
    cb: CallbackQuery, session: AsyncSession, prices: PriceService, state: FSMContext, redis: Redis
):
    # Acknowledge immediately so Telegram's spinner never waits on a market-data API.
    await cb.answer()
    u = await get_user(session, cb.from_user)
    if not cb.message:
        return
    parts = (cb.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action in {"confirm", "set"} and not await claim_create_action(
        redis, u.id, cb.message.message_id, action
    ):
        log.debug("ignored duplicate alert-creation callback action=%s", action)
        return
    if action == "open":
        await state.clear()
        await edit_if_changed(cb.message, f"{tr(u.language, 'create_title')}\n{tr(u.language, 'choose_coin_short')}", reply_markup=create_coin_keyboard(u.id, lang=u.language))
        return
    if action == "coin" and len(parts) == 3:
        await state.clear()
        symbol = parts[2]
        rows = [
            [InlineKeyboardButton(text=tr(u.language, "rises"), callback_data=f"create:move:{symbol}:up")],
            [InlineKeyboardButton(text=tr(u.language, "falls"), callback_data=f"create:move:{symbol}:down")],
            [InlineKeyboardButton(text=tr(u.language, "choose_another"), callback_data="create:open")],
        ]
        await edit_if_changed(cb.message, f"<b>{symbol.upper()}</b> {tr(u.language, 'direction_prompt')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    if action == "move" and len(parts) == 4:
        symbol, direction = parts[2], parts[3]
        if direction not in {"up", "down"}:
            await state.clear()
            await edit_if_changed(
                cb.message,
                tr(u.language, "invalid_option"),
                reply_markup=create_coin_keyboard(u.id, lang=u.language),
            )
            return
        sign = "+" if direction == "up" else "−"
        rows = [
            [InlineKeyboardButton(text=f"{sign}{pct}%", callback_data=f"create:set:{symbol}:{direction}:{pct}") for pct in (1, 3, 5, 10)],
            [InlineKeyboardButton(text=tr(u.language, "custom_pct"), callback_data=f"create:custompct:{symbol}:{direction}")],
            [InlineKeyboardButton(text=tr(u.language, "custom_price"), callback_data=f"create:customprice:{symbol}:{direction}")],
            [InlineKeyboardButton(text=tr(u.language, "back_short"), callback_data=f"create:coin:{symbol}")],
        ]
        try:
            coin = await prices.resolve(symbol)
            current = (await prices.prices([coin.id], allow_stale=True)).get(coin.id) if coin else None
        except PriceError:
            current = None
        prompt_key = "target_prompt" if current is not None else "target_prompt_no_price"
        prompt = tr(
            u.language,
            prompt_key,
            **({"current_price": fmt_usd(current)} if current is not None else {}),
        )
        await edit_if_changed(
            cb.message,
            f"<b>{symbol.upper()}</b> {prompt}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return
    if action in {"custompct", "customprice"} and len(parts) == 4:
        symbol, direction = parts[2], parts[3]
        if direction not in {"up", "down"}:
            await state.clear()
            await edit_if_changed(
                cb.message,
                tr(u.language, "invalid_option"),
                reply_markup=create_coin_keyboard(u.id, lang=u.language),
            )
            return
        await state.set_state(AlertCustomInput.waiting)
        await state.update_data(custom_kind=action, symbol=symbol, direction=direction)
        prompt = (
            tr(u.language, "pct_prompt")
            if action == "custompct"
            else tr(u.language, "price_prompt")
        )
        await edit_if_changed(
            cb.message,
            f"<b>{tr(u.language, 'create_title')} · {symbol.upper()}</b>\n{prompt}\n\n{tr(u.language, 'send_value')}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=tr(u.language, "cancel"), callback_data="create:cancel"),
            ]]),
        )
        return
    if action == "cancel":
        await state.clear()
        await edit_if_changed(cb.message, tr(u.language, "cancelled"), reply_markup=nav_keyboard(u.id, lang=u.language))
        return
    if action == "confirm":
        data = await state.get_data()
        if await state.get_state() != AlertCustomInput.confirming.state or not data.get("target"):
            await state.clear()
            await edit_if_changed(cb.message, tr(u.language, "confirm_expired"), reply_markup=create_coin_keyboard(u.id, lang=u.language))
            return
        limit = settings.premium_alert_limit if u.is_premium else settings.free_alert_limit
        if await active_count(session, u.id) >= limit:
            await state.clear()
            await edit_if_changed(cb.message,
                tr(u.language, "limit_reached", limit=limit) + ("" if u.is_premium else "\n" + tr(u.language, "premium_more")),
                reply_markup=premium_limit_keyboard(u.language) if not u.is_premium else nav_keyboard(u.id, lang=u.language),
            )
            return
        alert = Alert(
            user_id=u.id, kind="price", coin_id=data["coin_id"], symbol=data["symbol"],
            op=data["op"], target=float(data["target"]),
        )
        session.add(alert)
        await session.flush()
        await state.clear()
        await edit_if_changed(cb.message,
            tr(u.language, "alert_created", id=alert.id, symbol=alert.symbol, word=tr(u.language, "above" if alert.op == ">" else "below"), target=fmt_usd(alert.target)),
            reply_markup=nav_keyboard(u.id, lang=u.language),
        )
        return
    if action == "set" and len(parts) == 5:
        symbol, direction, pct_text = parts[2], parts[3], parts[4]
        if direction not in {"up", "down"} or pct_text not in {"1", "3", "5", "10"}:
            await edit_if_changed(cb.message, tr(u.language, "invalid_option"), reply_markup=create_coin_keyboard(u.id, lang=u.language))
            return
        limit = settings.premium_alert_limit if u.is_premium else settings.free_alert_limit
        if await active_count(session, u.id) >= limit:
            await edit_if_changed(cb.message,
                tr(u.language, "limit_reached", limit=limit) + ("" if u.is_premium else "\n" + tr(u.language, "premium_more")),
                reply_markup=premium_limit_keyboard(u.language) if not u.is_premium else nav_keyboard(u.id, lang=u.language),
            )
            return
        try:
            coin = await prices.resolve(symbol)
            current = (await prices.prices([coin.id], allow_stale=True)).get(coin.id) if coin else None
        except PriceError:
            current = None
            coin = None
        if not coin or current is None:
            await edit_if_changed(cb.message, tr(u.language, "market_unavailable"), reply_markup=create_coin_keyboard(u.id, lang=u.language))
            return
        pct = float(pct_text) / 100
        target = current * (1 + pct if direction == "up" else 1 - pct)
        op = ">" if direction == "up" else "<"
        alert = Alert(user_id=u.id, kind="price", coin_id=coin.id, symbol=coin.symbol.upper(), op=op, target=target)
        session.add(alert)
        await session.flush()
        word = "above" if direction == "up" else "below"
        await edit_if_changed(cb.message,
            tr(u.language, "alert_created", id=alert.id, symbol=coin.symbol.upper(), word=tr(u.language, "above" if op == ">" else "below"), target=fmt_usd(target)) + "\n\n"
            f"{tr(u.language, 'reference_price')}: {fmt_usd(current)} · {tr(u.language, 'target_offset')}: {pct_text}%",
            reply_markup=nav_keyboard(u.id, lang=u.language),
        )
        return


@alert_router.message(AlertCustomInput.waiting, is_non_command_text)
async def receive_custom_alert_value(
    m: Message, state: FSMContext, session: AsyncSession, prices: PriceService
):
    u = await get_user(session, m.from_user)
    raw = (m.text or "").strip()
    if raw.lower() in {"/cancel", "cancel"}:
        await state.clear()
        await m.answer(tr(u.language, "cancelled"), reply_markup=nav_keyboard(u.id, lang=u.language))
        return
    data = await state.get_data()
    symbol, direction, kind = data.get("symbol"), data.get("direction"), data.get("custom_kind")
    if not symbol or direction not in {"up", "down"} or kind not in {"custompct", "customprice"}:
        await state.clear()
        await m.answer(tr(u.language, "expired"), reply_markup=create_coin_keyboard(u.id, lang=u.language))
        return
    try:
        if kind == "custompct":
            pct = parse_percentage(raw, direction=direction)
        else:
            target = parse_number(raw)
    except ValueError:
        prompt = (tr(u.language, "invalid_pct") if kind == "custompct" else tr(u.language, "invalid_price"))
        await m.answer(f"{tr(u.language, 'invalid_value')} {prompt} {tr(u.language, 'send_value')}")
        return
    try:
        coin = await prices.resolve(symbol)
        current = (await prices.prices([coin.id])).get(coin.id) if coin else None
    except PriceError:
        coin, current = None, None
    if not coin or current is None:
        await m.answer(tr(u.language, "market_unavailable"))
        return
    if kind == "custompct":
        target = current * (1 + pct / 100 if direction == "up" else 1 - pct / 100)
        label = f"{tr(u.language, 'change_from_current')} ({pct:g}%)"
    else:
        if (direction == "up" and target <= current) or (direction == "down" and target >= current):
            relation = tr(u.language, "higher_than" if direction == "up" else "lower_than")
            await m.answer(tr(
                u.language, "target_direction_invalid", symbol=symbol.upper(),
                direction=tr(u.language, "rises_word" if direction == "up" else "falls_word"),
                relation=relation, current=fmt_usd(current),
            ))
            return
        label = tr(u.language, "exact_target")
    op = ">" if direction == "up" else "<"
    await state.update_data(
        coin_id=coin.id, symbol=coin.symbol.upper(), op=op, target=float(target),
        current=float(current), target_label=label,
    )
    await state.set_state(AlertCustomInput.confirming)
    relation = tr(u.language, "above" if direction == "up" else "below")
    await m.answer(
        f"{tr(u.language, 'confirm_prompt')}\n{tr(u.language, 'confirm_alert_line', symbol=coin.symbol.upper(), relation=relation, target=fmt_usd(target))}\n"
        f"{tr(u.language, 'current_price')}: {fmt_usd(current)}\n{tr(u.language, 'target_type')}: {label}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr(u.language, "confirm"), callback_data="create:confirm"),
            InlineKeyboardButton(text=tr(u.language, "cancel"), callback_data="create:cancel"),
        ]]),
    )


@alert_router.message(Command("cancel"))
async def cmd_cancel_alert(m: Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    u = await get_user(session, m.from_user)
    await m.answer(tr(u.language, "cancelled"), reply_markup=nav_keyboard(u.id, lang=u.language))


@alert_router.callback_query(F.data.startswith("alertedit:"))
async def cb_edit_alert(cb: CallbackQuery, session: AsyncSession, state: FSMContext):
    user = await get_user(session, cb.from_user)
    if cb.data == "alertedit:cancel":
        await state.clear()
        if cb.message:
            await edit_message_if_changed(
                cb.message, tr(user.language, "cancelled"),
                reply_markup=nav_keyboard(user.id, lang=user.language),
            )
        await cb.answer()
        return
    try:
        alert_id = int((cb.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer(tr(user.language, "invalid_option"), show_alert=True)
        return
    alert = await session.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id, Alert.active.is_(True))
    )
    if alert is None:
        await cb.answer(tr(user.language, "alert_not_found"), show_alert=True)
        return
    await state.clear()
    await state.set_state(AlertEditInput.waiting)
    await state.update_data(alert_id=alert.id)
    if cb.message:
        await cb.message.answer(
            tr(user.language, "alert_edit_prompt", symbol=alert.symbol or "GAS"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=tr(user.language, "cancel"), callback_data="alertedit:cancel"),
            ]]),
        )
    await cb.answer()


@alert_router.message(AlertEditInput.waiting, is_non_command_text)
async def receive_alert_edit_value(message: Message, state: FSMContext, session: AsyncSession):
    user = await get_user(session, message.from_user)
    raw = (message.text or "").strip()
    if raw.lower() in {"/cancel", "cancel"}:
        await state.clear()
        await message.answer(tr(user.language, "cancelled"), reply_markup=nav_keyboard(user.id, lang=user.language))
        return
    data = await state.get_data()
    try:
        target = parse_number(raw)
    except ValueError:
        await message.answer(tr(user.language, "alert_edit_invalid"))
        return
    alert_id = data.get("alert_id")
    alert = await session.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id, Alert.active.is_(True))
    )
    if alert is None:
        await state.clear()
        await message.answer(tr(user.language, "alert_not_found"), reply_markup=nav_keyboard(user.id, lang=user.language))
        return
    alert.target = target
    await session.flush()
    await state.clear()
    formatted = f"{target:g} gwei" if alert.kind == "gas" else fmt_usd(target)
    await message.answer(
        tr(user.language, "alert_updated", id=alert.id, target=formatted),
        reply_markup=nav_keyboard(user.id, lang=user.language),
    )
@alert_router.callback_query(F.data.startswith("alertsnooze:"))
async def cb_snooze_alert(cb: CallbackQuery, session: AsyncSession):
    user = await get_user(session, cb.from_user)
    try:
        alert_id = int((cb.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer(tr(user.language, "invalid_option"), show_alert=True)
        return
    alert = await session.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id, Alert.active.is_(True))
    )
    if alert is None:
        await cb.answer(tr(user.language, "alert_not_found"), show_alert=True)
        return
    alert.snoozed_until = utcnow() + timedelta(hours=1)
    await session.flush()
    rows = (await session.scalars(
        select(Alert).where(Alert.user_id == user.id, Alert.active.is_(True)).order_by(Alert.id)
    )).all()
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=alerts_keyboard(rows, user.id, lang=user.language))
    await cb.answer(tr(user.language, "alert_snoozed_toast"))


@alert_router.callback_query(F.data.startswith("alertresume:"))
async def cb_resume_alert(cb: CallbackQuery, session: AsyncSession):
    user = await get_user(session, cb.from_user)
    try:
        alert_id = int((cb.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer(tr(user.language, "invalid_option"), show_alert=True)
        return
    alert = await session.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id, Alert.active.is_(True))
    )
    if alert is None:
        await cb.answer(tr(user.language, "alert_not_found"), show_alert=True)
        return
    alert.snoozed_until = None
    await session.flush()
    rows = (await session.scalars(
        select(Alert).where(Alert.user_id == user.id, Alert.active.is_(True)).order_by(Alert.id)
    )).all()
    if cb.message:
        await cb.message.edit_reply_markup(reply_markup=alerts_keyboard(rows, user.id, lang=user.language))
    await cb.answer(tr(user.language, "alert_resumed_toast"))
@alert_router.callback_query(F.data.startswith("alertdel:"))
async def cb_delete_alert(cb: CallbackQuery, session: AsyncSession):
    await cb.answer()
    try:
        alert_id = int((cb.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        return
    u = await get_user(session, cb.from_user)
    result = await session.execute(delete(Alert).where(Alert.id == alert_id, Alert.user_id == u.id))
    if cb.message:
        message = tr(u.language, "deleted") if result.rowcount else tr(u.language, "already_removed")
        await edit_message_if_changed(cb.message, message, reply_markup=nav_keyboard(u.id, lang=u.language))
