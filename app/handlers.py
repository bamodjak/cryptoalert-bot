import html
from datetime import timedelta
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
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
from .db import AdminAudit, Alert, Payment, User, utcnow
from .entitlements import refundable_unused_duration
from .middlewares import KILL_KEY
from .parsing import fmt_gwei, fmt_usd, parse_alert, parse_number, parse_referral
from .prices import PriceError, PriceService

router = Router(name="user")
admin = Router(name="admin")
admin.message.filter(F.from_user.id.in_(settings.admin_id_set))

HELP = (
    "<b>Orbsat Crypto Alerts</b>\n"
    "Smart price alerts, without command syntax. Choose a coin, pick a target, and we will notify you.\n\n"
    "🔔 <b>Create an alert</b> from the button below.\n"
    "📈 Check BTC, ⛽ Ethereum gas, manage alerts, or explore Premium from the menu.\n\n"
    f"Free: up to {settings.free_alert_limit} active alerts. Premium: {settings.premium_alert_limit} alerts + Ethereum gas alerts for "
    f"{settings.premium_price_stars} ⭐ / {settings.premium_days} days.\n\n"
    "<i>Market data may be delayed. Alerts are best-effort, not financial advice.</i>"
)


def nav_keyboard(user_id: int, show_back: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="₿ BTC Price", callback_data="dash:price"),
            InlineKeyboardButton(text="⛽ Ethereum Gas", callback_data="dash:gas"),
        ],
        [
            InlineKeyboardButton(text="🔔 My Alerts", callback_data="dash:alerts"),
            InlineKeyboardButton(text="⭐ Premium", callback_data="dash:premium"),
        ],
        [
            InlineKeyboardButton(text="🔗 Referrals", callback_data="nav:share"),
            InlineKeyboardButton(text="ℹ️ Help", callback_data="nav:home"),
        ],
    ]
    if user_id in settings.admin_id_set:
        rows.append([
            InlineKeyboardButton(text="🛡 Admin Panel", callback_data="admin:panel")
        ])
    if show_back:
        rows.append([
            InlineKeyboardButton(text="⬅️ Back", callback_data="nav:back"),
            InlineKeyboardButton(text="🏠 Home", callback_data="nav:home"),
        ])
    ref_link = f"https://t.me/{settings.bot_username}?start=ref_{user_id}"
    share_url = (
        "https://t.me/share/url?url=" + quote(ref_link)
        + "&text=" + quote("Real-time Crypto Price & Ethereum Gas Alerts!")
    )
    rows.append([
        InlineKeyboardButton(text="📤 Forward Referral Link", url=share_url)
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
    await session.flush()
    return u


async def active_count(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Alert).where(Alert.user_id == user_id, Alert.active.is_(True))
    ) or 0


@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject, session: AsyncSession):
    u = await get_user(session, m.from_user, command.args)
    await m.answer(HELP, reply_markup=nav_keyboard(u.id, show_back=False))


@router.message(Command("help"))
async def cmd_help(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    await m.answer(HELP, reply_markup=nav_keyboard(u.id, show_back=True))


@router.callback_query(F.data.in_({"nav:home", "nav:back"}))
async def cb_home_or_back(cb: CallbackQuery, session: AsyncSession):
    u = await get_user(session, cb.from_user)
    if cb.message:
        try:
            await cb.message.edit_text(
                HELP, reply_markup=nav_keyboard(u.id, show_back=False)
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
        return await m.answer("Usage: /price btc", reply_markup=nav_keyboard(u.id))
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
        reply_markup=alerts_keyboard(rows, u.id),
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
    await m.answer("🔔 <b>Create a price alert</b>\nChoose a coin to get started.", reply_markup=create_coin_keyboard(u.id))


def create_coin_keyboard(user_id: int) -> InlineKeyboardMarkup:
    coins = [
        ("₿ Bitcoin", "btc"), ("Ξ Ethereum", "eth"), ("◎ Solana", "sol"),
        ("BNB", "bnb"), ("XRP", "xrp"), ("DOGE", "doge"),
        ("TON", "ton"), ("TRX", "trx"), ("AVAX", "avax"),
    ]
    rows = [[InlineKeyboardButton(text=label, callback_data=f"create:coin:{symbol}") for label, symbol in coins[i:i + 3]] for i in range(0, len(coins), 3)]
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("create:"))
async def cb_create_alert(cb: CallbackQuery, session: AsyncSession, prices: PriceService):
    await cb.answer()
    u = await get_user(session, cb.from_user)
    if not cb.message:
        return
    parts = (cb.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    if action == "open":
        await cb.message.edit_text("🔔 <b>Create a price alert</b>\nChoose a coin.", reply_markup=create_coin_keyboard(u.id))
        return
    if action == "coin" and len(parts) == 3:
        symbol = parts[2]
        rows = [
            [InlineKeyboardButton(text="📈 Alert when it rises", callback_data=f"create:move:{symbol}:up")],
            [InlineKeyboardButton(text="📉 Alert when it falls", callback_data=f"create:move:{symbol}:down")],
            [InlineKeyboardButton(text="⬅️ Choose another coin", callback_data="create:open")],
        ]
        await cb.message.edit_text(f"<b>{symbol.upper()}</b> selected. When should we notify you?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    if action == "move" and len(parts) == 4:
        symbol, direction = parts[2], parts[3]
        try:
            coin = await prices.resolve(symbol)
            current = (await prices.prices([coin.id])).get(coin.id) if coin else None
        except PriceError:
            current = None
            coin = None
        if not coin or current is None:
            await cb.message.edit_text("Price data is temporarily unavailable. Please try again shortly.", reply_markup=create_coin_keyboard(u.id))
            return
        sign = "+" if direction == "up" else "−"
        rows = [[InlineKeyboardButton(text=f"{sign}{pct}%", callback_data=f"create:set:{symbol}:{direction}:{pct}") for pct in (1, 3, 5, 10)]]
        rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data=f"create:coin:{symbol}")])
        word = "above" if direction == "up" else "below"
        await cb.message.edit_text(
            f"<b>{coin.name} ({coin.symbol.upper()})</b>\nCurrent price: <b>{fmt_usd(current)}</b>\n\nChoose how far {word} the current price the alert should be.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
            current = (await prices.prices([coin.id])).get(coin.id) if coin else None
        except PriceError:
            current = None
            coin = None
        if not coin or current is None:
            await cb.message.edit_text("Price data is temporarily unavailable. Please try again shortly.", reply_markup=create_coin_keyboard(u.id))
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
            text = "You have no active alerts yet. Tap below to create your first alert."
    elif action == "premium":
        text = (
            f"⭐ Premium: {settings.premium_price_stars} Stars for {settings.premium_days} days.\n"
            f"Includes up to {settings.premium_alert_limit} alerts and Ethereum gas alerts.\n"
            "Use /premium to purchase through Telegram."
        )
    else:
        return

    keyboard = alerts_keyboard(rows, u.id) if action == "alerts" and "rows" in locals() else nav_keyboard(u.id)
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

@router.message(Command("premium"))
async def cmd_premium(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    if u.id == settings.owner_id:
        return await m.answer("Owner account: Premium is enabled for free.")
    if u.is_premium:
        await m.answer(f"Premium is active until {u.premium_until:%Y-%m-%d} UTC. Paying again adds {settings.premium_days} days.")
    await m.answer_invoice(
        title=f"Premium · {settings.premium_days} days",
        description=f"{settings.premium_alert_limit} alerts and Ethereum gas alerts for {settings.premium_days} days.",
        payload=f"premium:{settings.premium_days}",
        currency="XTR",
        prices=[LabeledPrice(label="Premium", amount=settings.premium_price_stars)],
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
