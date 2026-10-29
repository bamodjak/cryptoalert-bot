import html
from datetime import timedelta
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from . import metrics
from .config import settings
from .db import AdminAudit, Alert, Payment, PremiumInvite, User, utcnow
from .entitlements import refundable_unused_duration
from .middlewares import KILL_KEY
from .i18n import tr, welcome_text
from .parsing import fmt_gwei, fmt_usd, parse_alert, parse_number, parse_percentage, parse_referral
from .prices import PriceError, PriceService

class AlertCustomInput(StatesGroup):
    waiting = State()
    confirming = State()


router = Router(name="user")
admin = Router(name="admin")
admin.message.filter(F.from_user.id.in_(settings.admin_id_set))

HELP = (
    "<b>Welcome to Orbsat Crypto Alerts 👋</b>\n"
    "Your crypto alerts, made simple. No command syntax to learn — just tap through the options.\n\n"
    "🔔 <b>Create an alert</b> and choose a coin, direction, and target.\n"
    "📊 Check BTC, manage alerts, invite friends, or compare plans below.\n\n"
    f"Free: up to {settings.free_alert_limit} active alerts. Premium: {settings.premium_alert_limit} alerts + Ethereum gas alerts for "
    f"{settings.premium_price_stars} ⭐ / {settings.premium_days} days.\n\n"
    "<i>Market data may be delayed. Alerts are best-effort, not financial advice.</i>"
)


def nav_keyboard(user_id: int, show_back: bool = True, lang: str = "en") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=tr(lang, "home_create"), callback_data="create:open")],
        [
            InlineKeyboardButton(text=tr(lang, "btc_price"), callback_data="dash:price"),
            InlineKeyboardButton(text=tr(lang, "gas"), callback_data="dash:gas"),
        ],
        [
            InlineKeyboardButton(text=tr(lang, "my_alerts"), callback_data="dash:alerts"),
            InlineKeyboardButton(text=tr(lang, "premium"), callback_data="dash:premium"),
        ],
        [
            InlineKeyboardButton(text=tr(lang, "referrals"), callback_data="nav:share"),
            InlineKeyboardButton(text=tr(lang, "help"), callback_data="nav:home"),
        ],
    ]
    if user_id in settings.admin_id_set:
        rows.append([
            InlineKeyboardButton(text="🛡 Admin Panel", callback_data="admin:panel")
        ])
    if show_back:
        rows.append([
            InlineKeyboardButton(text=tr(lang, "back"), callback_data="nav:back"),
            InlineKeyboardButton(text=tr(lang, "home"), callback_data="nav:home"),
        ])
    ref_link = f"https://t.me/{settings.bot_username}?start=ref_{user_id}"
    share_url = (
        "https://t.me/share/url?url=" + quote(ref_link)
        + "&text=" + quote("Real-time Crypto Price & Ethereum Gas Alerts!")
    )
    rows.append([InlineKeyboardButton(text=tr(lang, "language"), callback_data="lang:choose")])
    rows.append([InlineKeyboardButton(text=tr(lang, "share"), url=share_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def alerts_keyboard(alerts: list[Alert], user_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"🗑 #{alert.id} · {alert.symbol}", callback_data=f"alertdel:{alert.id}")]
        for alert in alerts[:20]
    ]
    rows.append([
        InlineKeyboardButton(text=tr(lang, "home_create"), callback_data="create:open"),
        InlineKeyboardButton(text=tr(lang, "home"), callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_user(
    session: AsyncSession, tg_user, start_args: str | None = None
) -> User:
    u = await session.get(User, tg_user.id)

    if u is None:
        ref_id = parse_referral(start_args, tg_user.id)

        if ref_id:
            referrer = await session.get(User, ref_id)
            if not referrer:
                ref_id = None

        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy import update

        stmt = (
            pg_insert(User)
            .values(
                id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                language="ar" if (getattr(tg_user, "language_code", None) or "").lower().startswith("ar") else "en",
                referred_by=ref_id,
                affiliation_shares=0,
                referrals_count=0,
            )
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(User.id)
        )
        inserted_id = await session.scalar(stmt)

        if inserted_id is not None and ref_id is not None:
            await session.execute(
                update(User)
                .where(User.id == ref_id)
                .values(
                    referrals_count=func.coalesce(
                        User.referrals_count, 0
                    ) + 1
                )
            )

        u = await session.get(User, tg_user.id)

    if u is None:
        raise RuntimeError("Could not load or create Telegram user")

    u.blocked = False
    u.username = tg_user.username
    u.first_name = tg_user.first_name
    u.last_name = tg_user.last_name
    await session.flush()
    return u


async def active_count(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Alert).where(Alert.user_id == user_id, Alert.active.is_(True))
    ) or 0


@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject, session: AsyncSession):
    u = await get_user(session, m.from_user, command.args)
    start_args = command.args or ""
    if start_args.startswith("invite_"):
        token = start_args.removeprefix("invite_").strip()
        invite = await session.scalar(select(PremiumInvite).where(PremiumInvite.token == token))
        if invite:
            if invite.clicked_at is None:
                invite.clicked_at = utcnow()
                invite.clicked_by = u.id
            if invite.recipient_id != u.id:
                await m.answer(
                    "This Premium invitation was created for a different Telegram account. "
                    "Please switch to the account whose ID received the invitation, or contact the sender.",
                    reply_markup=nav_keyboard(u.id, show_back=False),
                )
                return
            if invite.recipient_started_at is None:
                invite.recipient_started_at = utcnow()
            premium_note = (
                "\n\n🎁 <b>Your invitation is confirmed!</b> "
                "Your Premium access is attached to this Telegram account."
            )
            await m.answer(welcome_text(u.language) + premium_note, reply_markup=nav_keyboard(u.id, show_back=False, lang=u.language))
            return
    await m.answer(welcome_text(u.language), reply_markup=nav_keyboard(u.id, show_back=False, lang=u.language))


@router.message(Command("language"))
async def cmd_language(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    await m.answer(
        tr(u.language, "choose_language"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr("en", "english"), callback_data="lang:set:en"),
            InlineKeyboardButton(text=tr("ar", "arabic"), callback_data="lang:set:ar"),
        ], [InlineKeyboardButton(text=tr(u.language, "home"), callback_data="nav:home")]]),
    )


@router.callback_query(F.data.startswith("lang:"))
async def cb_language(cb: CallbackQuery, session: AsyncSession):
    await cb.answer()
    u = await get_user(session, cb.from_user)
    parts = (cb.data or "").split(":")
    if len(parts) == 2 and parts[1] == "choose":
        text = tr(u.language, "choose_language")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr("en", "english"), callback_data="lang:set:en"),
            InlineKeyboardButton(text=tr("ar", "arabic"), callback_data="lang:set:ar"),
        ], [InlineKeyboardButton(text=tr(u.language, "home"), callback_data="nav:home")]])
    elif len(parts) == 3 and parts[1] == "set" and parts[2] in {"en", "ar"}:
        u.language = parts[2]
        await session.flush()
        text = tr(u.language, "language_saved") + "\\n\\n" + welcome_text(u.language)
        keyboard = nav_keyboard(u.id, show_back=False, lang=u.language)
    else:
        return
    if cb.message:
        await cb.message.edit_text(text, reply_markup=keyboard)


@router.message(Command("help"))
async def cmd_help(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    await m.answer(welcome_text(u.language), reply_markup=nav_keyboard(u.id, show_back=True, lang=u.language))


@router.callback_query(F.data.in_({"nav:home", "nav:back"}))
async def cb_home_or_back(cb: CallbackQuery, session: AsyncSession):
    u = await get_user(session, cb.from_user)
    if cb.message:
        try:
            await cb.message.edit_text(
                welcome_text(u.language), reply_markup=nav_keyboard(u.id, show_back=False, lang=u.language)
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    await cb.answer()


@router.callback_query(F.data == "nav:share")
async def cb_share(cb: CallbackQuery, session: AsyncSession):
    u = await get_user(session, cb.from_user)
    u.affiliation_shares = (u.affiliation_shares or 0) + 1
    await session.flush()
    ref_link = f"https://t.me/{settings.bot_username}?start=ref_{u.id}"
    text = (
        f"🔗 <b>Your Affiliation Link</b>\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"• <b>Affiliation shared:</b> {u.affiliation_shares}\n"
        f"• <b>Friends joined:</b> {u.referrals_count}"
    )
    if cb.message:
        await cb.message.edit_text(text, reply_markup=nav_keyboard(u.id, show_back=True))
    await cb.answer("Affiliation link ready!")


@router.message(Command("price"))
async def cmd_price(m: Message, command: CommandObject, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    if not command.args:
        return await m.answer("Usage: /price btc", reply_markup=nav_keyboard(u.id, lang=u.language))
    try:
        coin = await prices.resolve(command.args.split()[0])
        if not coin:
            return await m.answer("I don't know that coin. Try its ticker, like btc or sol.", reply_markup=nav_keyboard(u.id))
        px = (await prices.prices([coin.id])).get(coin.id)
    except PriceError:
        return await m.answer("The price source is busy. Try again in a minute.", reply_markup=nav_keyboard(u.id))
    if px is None:
        return await m.answer("No price for that coin right now.", reply_markup=nav_keyboard(u.id))
    await m.answer(
        f"<b>{coin.symbol.upper()}</b> ({html.escape(coin.name)}): {fmt_usd(px)}",
        reply_markup=nav_keyboard(u.id),
    )


@router.message(Command("alert"))
async def cmd_alert(m: Message, command: CommandObject, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    try:
        sym, op, target = parse_alert(command.args or "")
    except ValueError:
        return await m.answer("Usage: /alert btc &gt; 70000  or  /alert eth below 2.5k", reply_markup=nav_keyboard(u.id))
    limit = settings.premium_alert_limit if u.is_premium else settings.free_alert_limit
    if await active_count(session, u.id) >= limit:
        extra = "" if u.is_premium else f" /premium raises it to {settings.premium_alert_limit}."
        return await m.answer(f"You're at your limit of {limit} alerts.{extra}", reply_markup=nav_keyboard(u.id))
    try:
        coin = await prices.resolve(sym)
    except PriceError:
        return await m.answer("The price source is busy. Try again in a minute.", reply_markup=nav_keyboard(u.id))
    if not coin:
        return await m.answer("I don't know that coin. Try its ticker, like btc or sol.", reply_markup=nav_keyboard(u.id))
    alert = Alert(user_id=u.id, kind="price", coin_id=coin.id, symbol=coin.symbol.upper(), op=op, target=target)
    session.add(alert)
    await session.flush()
    now = ""
    try:
        px = (await prices.prices([coin.id])).get(coin.id)
        if px is not None:
            now = f" (now {fmt_usd(px)})"
    except PriceError:
        pass
    word = "above" if op == ">" else "below"
    await m.answer(
        f"Alert #{alert.id} set: {alert.symbol} {word} {fmt_usd(target)}{now}.",
        reply_markup=nav_keyboard(u.id),
    )


@router.message(Command("gas"))
async def cmd_gas(m: Message, session: AsyncSession, prices: PriceService):
    u = await get_user(session, m.from_user)
    try:
        g = await prices.gas_gwei()
    except PriceError:
        return await m.answer("The gas source is busy. Try again in a minute.", reply_markup=nav_keyboard(u.id))
    await m.answer(f"Ethereum gas: <b>{fmt_gwei(g)} gwei</b>", reply_markup=nav_keyboard(u.id))


@router.message(Command("gasalert"))
async def cmd_gasalert(m: Message, command: CommandObject, session: AsyncSession):
    u = await get_user(session, m.from_user)
    if not u.is_premium:
        return await m.answer("Gas alerts are a Premium feature. /premium", reply_markup=nav_keyboard(u.id))
    try:
        target = parse_number((command.args or "").replace("gwei", ""))
    except ValueError:
        return await m.answer("Usage: /gasalert 0.5  (alerts when gas drops below 0.5 gwei)", reply_markup=nav_keyboard(u.id))
    if await active_count(session, u.id) >= settings.premium_alert_limit:
        return await m.answer(f"You're at your limit of {settings.premium_alert_limit} alerts.", reply_markup=nav_keyboard(u.id))
    alert = Alert(user_id=u.id, kind="gas", symbol="GAS", op="<", target=target)
    session.add(alert)
    await session.flush()
    await m.answer(f"Alert #{alert.id} set: gas below {target:g} gwei.", reply_markup=nav_keyboard(u.id))


@router.message(Command("alerts"))
async def cmd_alerts(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    rows = (await session.scalars(
        select(Alert).where(Alert.user_id == m.from_user.id, Alert.active.is_(True)).order_by(Alert.id)
    )).all()
    if not rows:
        return await m.answer("No active alerts. Try /alert btc &gt; 70000", reply_markup=nav_keyboard(u.id))
    lines = []
    for a in rows:
        word = "above" if a.op == ">" else "below"
        val = f"{a.target:g} gwei" if a.kind == "gas" else fmt_usd(a.target)
        lines.append(f"#{a.id} {a.symbol} {word} {val}")
    await m.answer(
        "🔔 <b>Your active alerts</b>\n" + "\n".join(lines) + "\n\nTap an alert below to remove it.",
        reply_markup=alerts_keyboard(rows, u.id, lang=u.language),
    )


@router.message(Command("del"))
async def cmd_del(m: Message, command: CommandObject, session: AsyncSession):
    u = await get_user(session, m.from_user)
    try:
        aid = int((command.args or "").strip().lstrip("#"))
    except ValueError:
        return await m.answer("Usage: /del 12", reply_markup=nav_keyboard(u.id))
    res = await session.execute(delete(Alert).where(Alert.id == aid, Alert.user_id == m.from_user.id))
    await m.answer(
        f"Deleted #{aid}." if res.rowcount else "No alert with that id.",
        reply_markup=nav_keyboard(u.id),
    )



@router.message(Command("create"))
async def cmd_create(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    await m.answer("🔔 <b>Create a price alert</b>\nChoose a coin to get started.", reply_markup=create_coin_keyboard(u.id, lang=u.language))


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


@router.callback_query(F.data.startswith("create:"))
async def cb_create_alert(
    cb: CallbackQuery, session: AsyncSession, prices: PriceService, state: FSMContext
):
    # Acknowledge immediately so Telegram's spinner never waits on a market-data API.
    await cb.answer()
    u = await get_user(session, cb.from_user)
    if not cb.message:
        return
    parts = (cb.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "open":
        await state.clear()
        await cb.message.edit_text("🔔 <b>Create a price alert</b>\nChoose a coin.", reply_markup=create_coin_keyboard(u.id))
        return
    if action == "coin" and len(parts) == 3:
        await state.clear()
        symbol = parts[2]
        rows = [
            [InlineKeyboardButton(text=tr(u.language, "rises"), callback_data=f"create:move:{symbol}:up")],
            [InlineKeyboardButton(text=tr(u.language, "falls"), callback_data=f"create:move:{symbol}:down")],
            [InlineKeyboardButton(text=tr(u.language, "choose_another"), callback_data="create:open")],
        ]
        await cb.message.edit_text(f"<b>{symbol.upper()}</b> selected. When should we notify you?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    if action == "move" and len(parts) == 4:
        symbol, direction = parts[2], parts[3]
        if direction not in {"up", "down"}:
            return
        sign = "+" if direction == "up" else "−"
        rows = [
            [InlineKeyboardButton(text=f"{sign}{pct}%", callback_data=f"create:set:{symbol}:{direction}:{pct}") for pct in (1, 3, 5, 10)],
            [InlineKeyboardButton(text=tr(u.language, "custom_pct"), callback_data=f"create:custompct:{symbol}:{direction}")],
            [InlineKeyboardButton(text=tr(u.language, "custom_price"), callback_data=f"create:customprice:{symbol}:{direction}")],
            [InlineKeyboardButton(text=tr(u.language, "back_short"), callback_data=f"create:coin:{symbol}")],
        ]
        word = "above" if direction == "up" else "below"
        await cb.message.edit_text(
            f"<b>{symbol.upper()}</b> selected.\n\nChoose a percentage change or enter your own target. "
            f"For a custom target, you can type a price such as <code>72500</code> or a percentage such as <code>2.5%</code>.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return
    if action in {"custompct", "customprice"} and len(parts) == 4:
        symbol, direction = parts[2], parts[3]
        if direction not in {"up", "down"}:
            await state.clear()
            return
        await state.set_state(AlertCustomInput.waiting)
        await state.update_data(custom_kind=action, symbol=symbol, direction=direction)
        prompt = (
            tr(u.language, "pct_prompt")
            if action == "custompct"
            else tr(u.language, "price_prompt")
        )
        await cb.message.edit_text(
            f"<b>Custom alert · {symbol.upper()}</b>\n{prompt}\n\nSend one value in this chat. Send /cancel to stop."
        )
        return
    if action == "cancel":
        await state.clear()
        await cb.message.edit_text(tr(u.language, "cancelled"), reply_markup=nav_keyboard(u.id))
        return
    if action == "confirm":
        data = await state.get_data()
        if await state.get_state() != AlertCustomInput.confirming.state or not data.get("target"):
            await state.clear()
            await cb.message.edit_text(tr(u.language, "confirm_expired"), reply_markup=create_coin_keyboard(u.id))
            return
        limit = settings.premium_alert_limit if u.is_premium else settings.free_alert_limit
        if await active_count(session, u.id) >= limit:
            await state.clear()
            await cb.message.edit_text(
                tr(u.language, "limit_reached", limit=limit) + ("" if u.is_premium else "\nPremium unlocks more alerts and Ethereum gas alerts."),
                reply_markup=nav_keyboard(u.id),
            )
            return
        alert = Alert(
            user_id=u.id, kind="price", coin_id=data["coin_id"], symbol=data["symbol"],
            op=data["op"], target=float(data["target"]),
        )
        session.add(alert)
        await session.flush()
        await state.clear()
        word = "above" if alert.op == ">" else "below"
        await cb.message.edit_text(
            f"✅ <b>Alert #{alert.id} created</b>\nWe’ll notify you when <b>{alert.symbol}</b> goes {word} <b>{fmt_usd(alert.target)}</b>.",
            reply_markup=nav_keyboard(u.id),
        )
        return
    if action == "set" and len(parts) == 5:
        symbol, direction, pct_text = parts[2], parts[3], parts[4]
        if direction not in {"up", "down"} or pct_text not in {"1", "3", "5", "10"}:
            await cb.message.edit_text("That alert option is no longer valid. Start again.", reply_markup=create_coin_keyboard(u.id))
            return
        limit = settings.premium_alert_limit if u.is_premium else settings.free_alert_limit
        if await active_count(session, u.id) >= limit:
            await cb.message.edit_text(
                f"You have reached your limit of {limit} active alerts." + ("" if u.is_premium else "\nPremium unlocks more alerts and Ethereum gas alerts."),
                reply_markup=nav_keyboard(u.id),
            )
            return
        try:
            coin = await prices.resolve(symbol)
            current = (await prices.prices([coin.id], allow_stale=True)).get(coin.id) if coin else None
        except PriceError:
            current = None
            coin = None
        if not coin or current is None:
            await cb.message.edit_text("Price data is temporarily unavailable. Please try again shortly; cached market data may be available soon.", reply_markup=create_coin_keyboard(u.id))
            return
        pct = float(pct_text) / 100
        target = current * (1 + pct if direction == "up" else 1 - pct)
        op = ">" if direction == "up" else "<"
        alert = Alert(user_id=u.id, kind="price", coin_id=coin.id, symbol=coin.symbol.upper(), op=op, target=target)
        session.add(alert)
        await session.flush()
        word = "above" if direction == "up" else "below"
        await cb.message.edit_text(
            f"✅ <b>Alert created</b>\nWe’ll notify you when <b>{coin.symbol.upper()}</b> goes {word} <b>{fmt_usd(target)}</b>.\n\n"
            f"Reference price: {fmt_usd(current)} · Target offset: {pct_text}%",
            reply_markup=nav_keyboard(u.id),
        )
        return


@router.message(AlertCustomInput.waiting, F.text)
async def receive_custom_alert_value(
    m: Message, state: FSMContext, session: AsyncSession, prices: PriceService
):
    u = await get_user(session, m.from_user)
    raw = (m.text or "").strip()
    if raw.lower() in {"/cancel", "cancel"}:
        await state.clear()
        await m.answer("Custom alert cancelled.", reply_markup=nav_keyboard(u.id))
        return
    data = await state.get_data()
    symbol, direction, kind = data.get("symbol"), data.get("direction"), data.get("custom_kind")
    if not symbol or direction not in {"up", "down"} or kind not in {"custompct", "customprice"}:
        await state.clear()
        await m.answer(tr(u.language, "expired"), reply_markup=create_coin_keyboard(u.id))
        return
    try:
        if kind == "custompct":
            pct = parse_percentage(raw)
        else:
            target = parse_number(raw)
    except ValueError:
        prompt = (tr(u.language, "invalid_pct") if kind == "custompct" else tr(u.language, "invalid_price"))
        await m.answer(f"Invalid value. {prompt} Send /cancel to stop.")
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
        label = f"{pct:g}% change from current price"
    else:
        if (direction == "up" and target <= current) or (direction == "down" and target >= current):
            relation = tr(u.language, "higher_than" if direction == "up" else "lower_than")
            await m.answer(f"For an alert when {symbol.upper()} {'rises' if direction == 'up' else 'falls'}, the target must be {relation} the current price ({fmt_usd(current)}). Enter another value or /cancel.")
            return
        label = tr(u.language, "exact_target")
    op = ">" if direction == "up" else "<"
    await state.update_data(
        coin_id=coin.id, symbol=coin.symbol.upper(), op=op, target=float(target),
        current=float(current), target_label=label,
    )
    await state.set_state(AlertCustomInput.confirming)
    word = "above" if direction == "up" else "below"
    await m.answer(
        f"Please confirm your alert:\n<b>{coin.symbol.upper()}</b> {word} <b>{fmt_usd(target)}</b>\n"
        f"Current price: {fmt_usd(current)}\nTarget type: {label}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr(u.language, "confirm"), callback_data="create:confirm"),
            InlineKeyboardButton(text=tr(u.language, "cancel"), callback_data="create:cancel"),
        ]]),
    )


@router.message(Command("cancel"))
async def cmd_cancel_alert(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Custom alert cancelled.", reply_markup=nav_keyboard(m.from_user.id))


@router.callback_query(F.data.startswith("alertdel:"))
async def cb_delete_alert(cb: CallbackQuery, session: AsyncSession):
    await cb.answer()
    try:
        alert_id = int((cb.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        return
    result = await session.execute(delete(Alert).where(Alert.id == alert_id, Alert.user_id == cb.from_user.id))
    if cb.message:
        message = "✅ Alert deleted." if result.rowcount else "That alert was already removed."
        await cb.message.edit_text(message, reply_markup=nav_keyboard(cb.from_user.id))


@router.callback_query(F.data.startswith("dash:"))
async def cb_dashboard(cb: CallbackQuery, session: AsyncSession, prices: PriceService):
    # Acknowledge Telegram callback queries before any database/network work; Telegram expires them quickly.
    try:
        await cb.answer()
    except TelegramBadRequest:
        # A stale callback cannot be acknowledged, but its handler can still return safely.
        pass

    u = await get_user(session, cb.from_user)
    action = (cb.data or "").split(":", 1)[1]

    if not cb.message:
        return

    if action == "price":
        try:
            coin = await prices.resolve("btc")
            px = (await prices.prices([coin.id])).get(coin.id) if coin else None
            text = f"<b>{coin.symbol.upper()}</b>: {fmt_usd(px)}" if coin and px is not None else "BTC price is unavailable."
        except PriceError:
            text = "The price source is busy. Try again shortly."
    elif action == "gas":
        try:
            text = f"Ethereum gas: <b>{fmt_gwei(await prices.gas_gwei())} gwei</b>"
        except PriceError:
            text = "The gas source is busy. Try again shortly."
    elif action == "alerts":
        rows = (await session.scalars(
            select(Alert).where(Alert.user_id == u.id, Alert.active.is_(True)).order_by(Alert.id)
        )).all()
        if rows:
            lines = []
            for a in rows:
                word = "above" if a.op == ">" else "below"
                value = f"{a.target:g} gwei" if a.kind == "gas" else fmt_usd(a.target)
                lines.append(f"#{a.id} {a.symbol} {word} {value}")
            text = "🔔 <b>Your active alerts</b>\n" + "\n".join(lines)
        else:
            text = tr(u.language, "no_alerts")
    elif action == "premium":
        text = premium_copy()
    else:
        return

    keyboard = (
        alerts_keyboard(rows, u.id) if action == "alerts" and "rows" in locals()
        else premium_keyboard() if action == "premium"
        else nav_keyboard(u.id)
    )
    try:
        await cb.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


@admin.callback_query(F.data.startswith("admin:"))
async def cb_admin_panel(cb: CallbackQuery, session: AsyncSession, redis: Redis):
    if cb.from_user.id not in settings.admin_id_set:
        await cb.answer("Not authorized.", show_alert=True)
        return

    action = (cb.data or "").split(":", 1)[1]
    if action == "panel":
        text = "<b>🛡 Admin Panel</b>\nChoose an action:"
        rows = [
            [InlineKeyboardButton(text="📊 Statistics", callback_data="admin:stats")],
            [
                InlineKeyboardButton(text="👥 Users & Premium", callback_data="admin:users"),
                InlineKeyboardButton(text="💳 Payments", callback_data="admin:payments"),
            ],
            [
                InlineKeyboardButton(text="🎁 Giveaways", callback_data="admin:giveaways"),
                InlineKeyboardButton(text="📣 Broadcast", callback_data="admin:broadcasthelp"),
            ],
            [InlineKeyboardButton(text="🧭 Full command guide", callback_data="admin:guide")],
            [
                InlineKeyboardButton(text="⏸ Pause bot", callback_data="admin:pause"),
                InlineKeyboardButton(text="▶️ Resume bot", callback_data="admin:resume"),
            ],
            [InlineKeyboardButton(text="💸 Refund help", callback_data="admin:refund")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="nav:home")],
        ]
    elif action == "guide":
        text = (
            "<b>🧭 Admin command guide</b>\n\n"
            "<b>Users</b>\n/users [ID or username]\n/grant USER_ID DAYS [reason]\n"
            "/suspend USER_ID [reason]\n/unsuspend USER_ID\n\n"
            "<b>Manual payments</b>\n/manualpay USER_ID METHOD AMOUNT CURRENCY DAYS [reference]\n"
            "Methods: cash, cash_person, stars, baridimob, ccp, bank\n/verify PAYMENT_ID\n"
            "/rejectpay PAYMENT_ID\n/pendingpay — list pending manual payments\n/revenue\n\n"
            "<b>Giveaways</b>\n/giveaway TITLE | DAYS | WINNERS\n/draw GIVEAWAY_ID\n"
            "Participants use /join GIVEAWAY_ID\n\n"
            "<b>Broadcast</b>\n/broadcast announcement text\nPreview first, then confirm."
        )
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "users":
        text = (
            "<b>👥 Users &amp; Premium</b>\n/users [ID or username] — find users\n"
            "/grant USER_ID DAYS [reason] — grant free Premium\n"
            "/suspend USER_ID [reason] — suspend access\n/unsuspend USER_ID — restore access"
        )
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "payments":
        text = (
            "<b>💳 Payments</b>\n/manualpay USER_ID METHOD AMOUNT CURRENCY DAYS [reference]\n"
            "/pendingpay — list pending payments\n/verify PAYMENT_ID — verify and grant Premium\n"
            "/rejectpay PAYMENT_ID — reject payment\n/revenue — revenue by currency"
        )
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "giveaways":
        text = (
            "<b>🎁 Giveaways</b>\n/giveaway TITLE | PREMIUM_DAYS | WINNERS\n"
            "Users join with /join GIVEAWAY_ID\n/draw GIVEAWAY_ID — select and record winners"
        )
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "broadcasthelp":
        text = (
            "<b>📣 Broadcast</b>\n/broadcast your announcement\n"
            "Review the preview, then confirm. Broadcasts go to registered, non-suspended users."
        )
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "stats":
        users = await session.scalar(select(func.count()).select_from(User)) or 0
        premium = await session.scalar(
            select(func.count()).select_from(User).where((User.premium_until > utcnow()) | (User.id == settings.owner_id))
        ) or 0
        alerts = await session.scalar(
            select(func.count()).select_from(Alert).where(Alert.active.is_(True))
        ) or 0
        stars = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.refunded.is_(False))
        ) or 0
        killed = "ON" if await redis.exists(KILL_KEY) else "OFF"
        text = (
            f"<b>📊 Bot Statistics</b>\nUsers: {users}\nPremium: {premium}\n"
            f"Active alerts: {alerts}\nStars earned: {stars}\nPause switch: {killed}"
        )
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "pause":
        await redis.set(KILL_KEY, "1")
        text = "⏸ Pause switch enabled."
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "resume":
        await redis.delete(KILL_KEY)
        text = "▶️ Pause switch disabled."
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "refund":
        text = "💸 To refund a payment, use:\n/refund CHARGE_ID\n\nLegacy/untracked payments require manual review and the explicit override:\n/refund CHARGE_ID ALLOW_LEGACY"
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    else:
        await cb.answer("Unknown action.")
        return

    if cb.message:
        try:
            await cb.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    await cb.answer()

# ---------- Payments (Telegram Stars) ----------

def premium_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Get Premium with Telegram Stars", callback_data="premium:buy")],
        [InlineKeyboardButton(text="⬅️ Home", callback_data="nav:home")],
    ])


def premium_copy() -> str:
    return (
        "<b>⭐ Choose your plan</b>\n\n"
        f"<b>FREE · 0 ⭐</b>\n"
        f"• Up to {settings.free_alert_limit} active alerts\n"
        "• Core crypto price alerts\n"
        "• Telegram notifications\n\n"
        f"<b>PREMIUM · {settings.premium_price_stars} ⭐ / {settings.premium_days} days</b>\n"
        f"• Up to {settings.premium_alert_limit} active alerts\n"
        "• Ethereum gas alerts\n"
        "• Keep using the same account; no subscription website needed\n\n"
        "Pay securely through Telegram Stars. Alerts are best-effort and market data may be delayed."
    )


@router.message(Command("premium"))
async def cmd_premium(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    if u.id == settings.owner_id:
        return await m.answer("⭐ Premium is enabled for the owner account at no charge.")
    status = (
        f"\n\nYour Premium is active until {u.premium_until:%Y-%m-%d} UTC. Buying again adds {settings.premium_days} days."
        if u.is_premium and u.premium_until else ""
    )
    await m.answer(premium_copy() + status, reply_markup=premium_keyboard())


@router.callback_query(F.data == "premium:buy")
async def cb_premium_buy(cb: CallbackQuery, session: AsyncSession, bot: Bot):
    await cb.answer()
    u = await get_user(session, cb.from_user)
    if u.id == settings.owner_id:
        if cb.message:
            await cb.message.answer("⭐ Premium is already enabled for the owner account.")
        return
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=f"Orbsat Premium · {settings.premium_days} days",
        description=f"Up to {settings.premium_alert_limit} active alerts and Ethereum gas alerts for {settings.premium_days} days.",
        payload=f"premium:{settings.premium_days}",
        currency="XTR",
        prices=[LabeledPrice(label="Orbsat Premium", amount=settings.premium_price_stars)],
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    valid_invoice = (
        q.invoice_payload == f"premium:{settings.premium_days}"
        and q.currency == "XTR"
        and q.total_amount == settings.premium_price_stars
    )
    if valid_invoice:
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="This invoice is no longer valid.")


@router.message(F.successful_payment)
async def paid(m: Message, session: AsyncSession):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    sp = m.successful_payment
    u = await get_user(session, m.from_user)
    if (
        sp.invoice_payload != f"premium:{settings.premium_days}"
        or sp.currency != "XTR"
        or sp.total_amount != settings.premium_price_stars
    ):
        inserted = await session.scalar(
            pg_insert(Payment)
            .values(
                user_id=u.id, charge_id=sp.telegram_payment_charge_id, amount=sp.total_amount,
                entitlement_tracked=False,
            )
            .on_conflict_do_nothing(index_elements=["charge_id"])
            .returning(Payment.id)
        )
        if inserted is None:
            await m.answer("This payment was already recorded; no duplicate action was taken.")
        else:
            await m.answer(f"Payment recorded but could not be matched to the current Premium offer. No Premium was added. Contact {settings.support_contact} with this payment.")
        return
    days = settings.premium_days
    base = u.premium_until if u.premium_until and u.premium_until > utcnow() else utcnow()
    expiry = base + timedelta(days=days)
    inserted = await session.scalar(
        pg_insert(Payment)
        .values(
            user_id=u.id, charge_id=sp.telegram_payment_charge_id, amount=sp.total_amount,
            premium_days=days, entitlement_start=base, entitlement_end=expiry, entitlement_tracked=True,
        )
        .on_conflict_do_nothing(index_elements=["charge_id"])
        .returning(Payment.id)
    )
    if inserted is None:
        await m.answer("This payment was already processed; Premium was not added twice.")
        return
    u.premium_until = expiry
    metrics.PAYMENTS.inc()
    await m.answer(
        f"Thank you! Premium is active until {u.premium_until:%Y-%m-%d} UTC.",
        reply_markup=nav_keyboard(u.id),
    )


@router.message(Command("paysupport"))
async def cmd_paysupport(m: Message):
    await m.answer(f"Payment problem or refund request? Message {settings.support_contact} with the date of your payment.")


@router.message(Command("terms"))
async def cmd_terms(m: Message):
    await m.answer(
        "Prices come from public sources and can be delayed or wrong. Alerts are best-effort and are not "
        "financial advice. Premium is a digital service paid in Telegram Stars; refunds via /paysupport."
    )


# ---------- Admin ----------

@admin.message(Command("kill"))
async def cmd_kill(m: Message, redis: Redis):
    await redis.set(KILL_KEY, "1")
    await m.answer("Kill switch ON. Users are blocked and alerts are paused. /resume to undo.")


@admin.message(Command("resume"))
async def cmd_resume(m: Message, redis: Redis):
    await redis.delete(KILL_KEY)
    await m.answer("Kill switch OFF. Bot is live.")


@admin.message(Command("stats"))
async def cmd_stats(m: Message, session: AsyncSession, redis: Redis):
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    premium = await session.scalar(select(func.count()).select_from(User).where((User.premium_until > utcnow()) | (User.id == settings.owner_id))) or 0
    alerts = await session.scalar(select(func.count()).select_from(Alert).where(Alert.active.is_(True))) or 0
    stars = await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.refunded.is_(False))) or 0
    shared = await session.scalar(select(func.coalesce(func.sum(User.affiliation_shares), 0))) or 0
    referrals = await session.scalar(select(func.coalesce(func.sum(User.referrals_count), 0))) or 0
    killed = "ON" if await redis.exists(KILL_KEY) else "off"
    await m.answer(
        f"Users: {users}\n"
        f"Premium: {premium}\n"
        f"Active alerts: {alerts}\n"
        f"Stars earned: {stars}\n"
        f"Affiliation shared: {shared} (referrals joined: {referrals})\n"
        f"Kill switch: {killed}"
    )


@admin.message(Command("refund"))
async def cmd_refund(m: Message, command: CommandObject, session: AsyncSession, bot: Bot):
    parts = (command.args or "").split()
    charge = parts[0] if parts else ""
    allow_legacy = len(parts) > 1 and parts[1].upper() == "ALLOW_LEGACY"
    p = await session.scalar(select(Payment).where(Payment.charge_id == charge).with_for_update()) if charge else None
    if not p or p.refunded:
        return await m.answer("Usage: /refund CHARGE_ID (unrefunded payment)")
    if not p.entitlement_tracked and not allow_legacy:
        return await m.answer(
            "This older/unmatched payment has no Premium entitlement record, so no refund was issued. "
            "Review the account manually first. Explicit override: /refund CHARGE_ID ALLOW_LEGACY "
            "(Premium expiry will remain unchanged)."
        )
    await bot.refund_star_payment(user_id=p.user_id, telegram_payment_charge_id=p.charge_id)
    p.refunded = True
    u = await session.get(User, p.user_id)
    if u and p.entitlement_tracked and p.entitlement_start and p.entitlement_end and u.premium_until:
        now = utcnow()
        remaining = refundable_unused_duration(
            p.entitlement_start, p.entitlement_end, u.premium_until, now
        )
        if remaining:
            u.premium_until = max(now, u.premium_until - remaining)
            future_payments = (await session.scalars(
                select(Payment)
                .where(
                    Payment.user_id == p.user_id,
                    Payment.id != p.id,
                    Payment.refunded.is_(False),
                    Payment.entitlement_tracked.is_(True),
                    Payment.entitlement_start >= p.entitlement_end,
                )
                .with_for_update()
            )).all()
            for future_payment in future_payments:
                future_payment.entitlement_start -= remaining
                future_payment.entitlement_end -= remaining
        note = f" Premium expiry adjusted by {remaining.days}d {remaining.seconds // 3600}h for unused time."
    else:
        note = " This charge has no entitlement ledger; Premium expiry was left unchanged."
    session.add(AdminAudit(admin_id=m.from_user.id, action="stars_refund", target_user_id=p.user_id, details=f"payment_id={p.id}; amount={p.amount}; entitlement_tracked={p.entitlement_tracked}"))
    await m.answer(f"Refunded {p.amount} Stars to {p.user_id}.{note}")
