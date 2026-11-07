import html
import time
from datetime import timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from .offer import get_offer, set_offer_value
from .db import AdminAudit, Alert, ManualPayment, Payment, PremiumInvite, User, utcnow
from .entitlements import premium_payment_matches_offer, refundable_unused_duration
from .middlewares import KILL_KEY
from .i18n import tr, welcome_text
from .parsing import fmt_gwei, fmt_usd, parse_alert, parse_number, parse_percentage
from .prices import PriceError, PriceService
from .users import get_user
from .keyboards import alerts_keyboard, edit_message_if_changed, nav_keyboard, persistent_menu_keyboard
from .alert_handlers import (
    alert_router, active_count, alert_history_text, claim_create_action,
    create_coin_keyboard, edit_if_changed,
)
from .market_handlers import (
    market_router, cmd_market, cmd_movers, crypto_prices_keyboard, crypto_prices_text,
    market_movers_keyboard, market_movers_text, market_snapshot_text,
)

router = Router(name="user")
router.include_router(market_router)
router.include_router(alert_router)
admin = Router(name="admin")
admin.message.filter(F.from_user.id.in_(settings.admin_id_set))
admin.message.filter(F.chat.type == "private")
admin.callback_query.filter(F.from_user.id.in_(settings.admin_id_set), F.message.chat.type == "private")



@admin.message(Command("admin"))
async def cmd_admin(m: Message):
    """Open the private admin panel for configured administrators only."""
    rows = [
        [InlineKeyboardButton(text="📊 Statistics", callback_data="admin:stats")],
        [InlineKeyboardButton(text="⚙️ Premium pricing & duration", callback_data="admin:offer")],
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
    await m.answer(
        "<b>🛡 Admin Panel</b>\nChoose an action:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject, session: AsyncSession, redis: Redis | None = None):
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
                    reply_markup=nav_keyboard(u.id, show_back=False, lang=u.language),
                )
                return
            if invite.recipient_started_at is None:
                invite.recipient_started_at = utcnow()
            premium_note = (
                "\n\n🎁 <b>Your invitation is confirmed!</b> "
                "Your Premium access is attached to this Telegram account."
            )
            await m.answer(
                welcome_text(u.language) + premium_note,
                reply_markup=nav_keyboard(u.id, show_back=False, lang=u.language),
            )
            return
    price, days = await get_offer(redis) if redis is not None else (settings.premium_price_stars, settings.premium_days)
    welcome = welcome_text(u.language, stars=price, days=days)
    if getattr(u, "is_premium", False) and getattr(u, "premium_until", None):
        welcome += premium_member_status(u.language, u.premium_until.strftime("%Y-%m-%d"))
    await m.answer(
        welcome,
        reply_markup=nav_keyboard(u.id, show_back=False, lang=u.language),
    )


@router.message(F.text.in_({tr(lang, key) for lang in ("en", "ar") for key in ("quick_dashboard", "quick_markets", "quick_alerts", "quick_premium")}))
async def persistent_menu_action(m: Message, session: AsyncSession, prices: PriceService, redis: Redis | None = None):
    """Handle visible quick-action labels without requiring slash-command syntax."""
    u = await get_user(session, m.from_user)
    action_label = (m.text or "").strip()
    action = {
        tr(language, key): key
        for language in ("en", "ar")
        for key in ("quick_dashboard", "quick_markets", "quick_alerts", "quick_premium")
    }.get(action_label)
    if action == "quick_dashboard":
        price, days = await get_offer(redis) if redis is not None else (settings.premium_price_stars, settings.premium_days)
        welcome = welcome_text(u.language, stars=price, days=days)
        if u.is_premium and u.premium_until:
            welcome += premium_member_status(u.language, u.premium_until.strftime("%Y-%m-%d"))
        await m.answer(welcome, reply_markup=nav_keyboard(u.id, show_back=False, lang=u.language))
    elif action == "quick_markets":
        try:
            text = market_snapshot_text(await prices.market_overview(), u.language)
        except PriceError:
            text = tr(u.language, "market_snapshot_unavailable")
        await m.answer(text, reply_markup=nav_keyboard(u.id, lang=u.language))
    elif action == "quick_alerts":
        rows = (await session.scalars(
            select(Alert).where(Alert.user_id == u.id, Alert.active.is_(True)).order_by(Alert.id)
        )).all()
        if not rows:
            await m.answer(tr(u.language, "no_alerts"), reply_markup=nav_keyboard(u.id, lang=u.language))
            return
        lines = []
        for alert in rows:
            word = tr(u.language, "above" if alert.op == ">" else "below")
            value = f"{alert.target:g} gwei" if alert.kind == "gas" else fmt_usd(alert.target)
            paused = f"⏸ {tr(u.language, 'entitlement_paused')} · " if alert.entitlement_paused else ""
            lines.append(f"{paused}#{alert.id} {alert.symbol} {word} {value}")
        await m.answer(
            tr(u.language, "active_alerts_header") + "\n" + "\n".join(lines) + "\n\n" + tr(u.language, "remove_alert_hint"),
            reply_markup=alerts_keyboard(rows, u.id, lang=u.language),
        )
    elif action == "quick_premium":
        if u.id == settings.owner_id:
            await m.answer(tr(u.language, "premium_owner"))
            return
        price, days = await get_offer(redis) if redis is not None else (settings.premium_price_stars, settings.premium_days)
        status = premium_member_status(u.language, u.premium_until.strftime("%Y-%m-%d")) if u.is_premium and u.premium_until else ""
        await m.answer(premium_copy(u.language, price, days) + status, reply_markup=premium_keyboard(u.language))


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
        text = tr(u.language, "language_saved") + "\n\n" + welcome_text(u.language)
        keyboard = nav_keyboard(u.id, show_back=False, lang=u.language)
    else:
        return
    if cb.message:
        await edit_message_if_changed(cb.message, text, reply_markup=keyboard)


@router.message(Command("help"))
async def cmd_help(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    await m.answer(tr(u.language, "help_text"), reply_markup=nav_keyboard(u.id, show_back=True, lang=u.language))


@router.callback_query(F.data.in_({"nav:home", "nav:back", "nav:help"}))
async def cb_home_or_back(cb: CallbackQuery, session: AsyncSession):
    u = await get_user(session, cb.from_user)
    if cb.message:
        body = tr(u.language, "help_text") if cb.data == "nav:help" else welcome_text(u.language)
        try:
            await cb.message.edit_text(
                body, reply_markup=nav_keyboard(u.id, show_back=False, lang=u.language)
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    await cb.answer()


@router.callback_query(F.data == "nav:share")
async def cb_share(cb: CallbackQuery, session: AsyncSession):
    u = await get_user(session, cb.from_user)
    ref_link = f"https://t.me/{settings.bot_username}?start=ref_{u.id}"
    text = (
        f"{tr(u.language, 'affiliation_title')}\n\n"
        f"<code>{ref_link}</code>\n\n"
        f"{tr(u.language, 'affiliation_shared', count=u.affiliation_shares)}\n"
        f"{tr(u.language, 'friends_joined', count=u.referrals_count)}"
    )
    if cb.message:
        await edit_message_if_changed(cb.message, text, reply_markup=nav_keyboard(u.id, show_back=True, lang=u.language))
    await cb.answer(tr(u.language, "affiliation_ready"))


@router.callback_query(F.data.in_({"dash:price", "dash:gas", "dash:alerts", "dash:premium"}))
async def cb_dashboard(cb: CallbackQuery, session: AsyncSession, prices: PriceService, redis: Redis):
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
            payload = await prices.top_coins()
            text = crypto_prices_text(payload, u.language)
            await edit_message_if_changed(cb.message, text, reply_markup=crypto_prices_keyboard(payload["coins"], u.language))
            return
        except PriceError:
            text = tr(u.language, "crypto_prices_unavailable")
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
                paused = f"⏸ {tr(u.language, 'entitlement_paused')} · " if a.entitlement_paused else ""
                snoozed = f"⏸ {tr(u.language, 'alert_snoozed')} · " if a.snoozed_until and a.snoozed_until > utcnow() else ""
                lines.append(f"{paused}{snoozed}#{a.id} {a.symbol} {word} {value}")
            text = "🔔 <b>Your active alerts</b>\n" + "\n".join(lines)
        else:
            text = tr(u.language, "no_alerts")
    elif action == "premium":
        price, days = await get_offer(redis)
        text = premium_copy(u.language, price, days)
        if u.is_premium and u.premium_until:
            text += premium_member_status(u.language, u.premium_until.strftime("%Y-%m-%d"))
    else:
        return

    keyboard = (
        alerts_keyboard(rows, u.id, lang=u.language) if action == "alerts" and "rows" in locals()
        else premium_keyboard(u.language) if action == "premium"
        else nav_keyboard(u.id, lang=u.language)
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
    if action == "offer":
        price, days = await get_offer(redis)
        text = f"<b>⚙️ Premium offer settings</b>\nCurrent price: <b>{price} Stars</b>\nPremium duration: <b>{days} days</b>\n\nChoose a value to edit. Changes affect new invoices; existing invoices retain their quoted amount and duration."
        rows = [
            [InlineKeyboardButton(text="💫 Change Stars price", callback_data="admin:offer:set:price")],
            [InlineKeyboardButton(text="📅 Change Premium days", callback_data="admin:offer:set:days")],
            [InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")],
        ]
    elif action.startswith("offer:set:"):
        field = action.rsplit(":", 1)[-1]
        if field not in {"price", "days"}:
            await cb.answer("Unknown Premium setting.", show_alert=True)
            return
        await redis.set(f"admin:offer:input:{cb.from_user.id}", field, ex=600)
        prompt = "Send the new price in Telegram Stars (1–100000)." if field == "price" else "Send the Premium duration in days (1–3650)."
        if cb.message:
            await cb.message.answer(prompt, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Cancel", callback_data="admin:offer:cancel")
            ]]))
        await cb.answer()
        return
    elif action == "offer:cancel":
        await redis.delete(f"admin:offer:input:{cb.from_user.id}")
        if cb.message:
            await cb.message.answer("Premium offer edit cancelled.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Premium offer settings", callback_data="admin:offer")
            ]]))
        await cb.answer()
        return
    elif action == "panel":
        text = "<b>🛡 Admin Panel</b>\nChoose an action:"
        rows = [
            [InlineKeyboardButton(text="📊 Statistics", callback_data="admin:stats")],
            [InlineKeyboardButton(text="⚙️ Premium pricing & duration", callback_data="admin:offer")],
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
            "/rejectpay PAYMENT_ID\n/pendingpay — list pending manual payments\n/revenue\n"
            "/refund CHARGE_ID — refund Stars\n/refundpending — list uncertain refunds\n"
            "/refundresolve CHARGE_ID REFUNDED|NOT_REFUNDED — reconcile after checking Telegram\n\n"
            "<b>Giveaways</b>\n/giveaway TITLE | DAYS | WINNERS\n/draw GIVEAWAY_ID\n"
            "Participants use /join GIVEAWAY_ID\n\n"
            "<b>Broadcast</b>\n/broadcast announcement text\n"
            "Or reply to a message (text, photo, video or document) with /broadcast.\n"
            "Preview first, then confirm; delivery targets registered, non-suspended users."
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
        rows = [
            [InlineKeyboardButton(text="🧾 Review pending payments", callback_data="admin:payments:pending")],
            [InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")],
        ]
    elif action == "giveaways":
        text = (
            "<b>🎁 Giveaways</b>\n/giveaway TITLE | PREMIUM_DAYS | WINNERS\n"
            "Users join with /join GIVEAWAY_ID\n/draw GIVEAWAY_ID — select and record winners"
        )
        rows = [[InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]]
    elif action == "broadcasthelp":
        text = (
            "<b>📣 Broadcast</b>\n"
            "Reply to any Telegram message (text, photo, video or document) with /broadcast to copy it.\n"
            "Or send /broadcast your announcement text.\n"
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
        suspended = await session.scalar(
            select(func.count()).select_from(User).where(User.suspended.is_(True))
        ) or 0
        blocked = await session.scalar(
            select(func.count()).select_from(User).where(User.blocked.is_(True))
        ) or 0
        new_users = await session.scalar(
            select(func.count()).select_from(User).where(User.created_at >= utcnow() - timedelta(days=7))
        ) or 0
        pending_manual = await session.scalar(
            select(func.count()).select_from(ManualPayment).where(ManualPayment.status == "pending")
        ) or 0
        stars = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.refunded.is_(False))
        ) or 0
        killed = "ON" if await redis.exists(KILL_KEY) else "OFF"
        text = (
            f"<b>📊 Bot Statistics</b>\n"
            f"Registered users: {users} (+{new_users} in 7 days)\n"
            f"Premium accounts: {premium}\n"
            f"Active alerts: {alerts}\n"
            f"Suspended / blocked: {suspended} / {blocked}\n"
            f"Unrefunded Telegram Stars: {stars}\n"
            f"Pending manual payments: {pending_manual}\n"
            f"Pause switch: {killed}"
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


@admin.message(F.text.regexp(r"^\\d{1,6}$"))
async def save_offer_edit(message: Message, redis: Redis, session: AsyncSession):
    key = f"admin:offer:input:{message.from_user.id}"
    field = await redis.get(key)
    if field not in {"price", "days"}:
        return
    try:
        price, days = await set_offer_value(redis, field, int(message.text))
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await redis.delete(key)
    session.add(AdminAudit(
        admin_id=message.from_user.id, action="premium_offer_update",
        details=f"{field}={int(message.text)}",
    ))
    await session.commit()
    await message.answer(
        f"Premium offer updated.\nPrice: <b>{price} Stars</b>\nDuration: <b>{days} days</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Premium offer settings", callback_data="admin:offer")
        ]]),
    )


# ---------- Payments (Telegram Stars) ----------

def premium_keyboard(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(lang, "buy_premium"), callback_data="premium:buy")],
        [InlineKeyboardButton(text=tr(lang, "home"), callback_data="nav:home")],
        [InlineKeyboardButton(text=tr(lang, "language"), callback_data="lang:choose")],
    ])


def premium_copy(lang: str = "en", price: int | None = None, days: int | None = None) -> str:
    price = settings.premium_price_stars if price is None else price
    days = settings.premium_days if days is None else days
    return (
        tr(lang, "premium_title") + "\n\n"
        + tr(lang, "free_plan", free=settings.free_alert_limit) + "\n\n"
        + tr(lang, "premium_plan", stars=price, days=days, premium=settings.premium_alert_limit)
        + "\n\n" + tr(lang, "pay_note")
    )


def premium_member_status(lang: str, expiry: str) -> str:
    if lang == "ar":
        return (f"\n\n⭐ <b>أنت الآن عضو بريميوم</b> · الاشتراك نشط حتى {expiry} UTC.\n"
                "مزاياك: التحليل والإشارات الكمية، لوحة السوق المباشر، حتى 500 تنبيه نشط، وتنبيهات الغاز.\n"
                "هذه المزايا مفعّلة لحسابك؛ استخدم أزرار بريميوم في القائمة الرئيسية.")
    return (f"\n\n⭐ <b>YOU ARE A PREMIUM MEMBER</b> · Active until {expiry} UTC.\n"
            "Your paid benefits: Premium quantitative signals, Live Market dashboard, up to 500 active alerts, and Ethereum gas alerts.\n"
            "These features are enabled on your account. Open them from the main menu.")


@router.message(Command("premium"))
async def cmd_premium(m: Message, session: AsyncSession, redis: Redis):
    u = await get_user(session, m.from_user)
    if u.id == settings.owner_id:
        return await m.answer(tr(u.language, "premium_owner"))
    price, days = await get_offer(redis)
    status = premium_member_status(u.language, u.premium_until.strftime("%Y-%m-%d")) if u.is_premium and u.premium_until else ""
    await m.answer(premium_copy(u.language, price, days) + status, reply_markup=premium_keyboard(u.language))


@router.callback_query(F.data == "premium:buy")
async def cb_premium_buy(cb: CallbackQuery, session: AsyncSession, bot: Bot, redis: Redis):
    await cb.answer()
    u = await get_user(session, cb.from_user)
    if u.id == settings.owner_id:
        if cb.message:
            await edit_if_changed(cb.message, tr(u.language, "premium_owner"))
        return
    price, days = await get_offer(redis)
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=tr(u.language, "premium_invoice_title", days=days),
        description=tr(u.language, "premium_invoice_description", limit=settings.premium_alert_limit, days=days),
        payload=f"premium:{days}:{price}",
        currency="XTR",
        prices=[LabeledPrice(label=tr(u.language, "premium_label"), amount=price)],
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, session: AsyncSession, redis: Redis):
    u = await get_user(session, q.from_user)
    price, days = await get_offer(redis)
    valid_invoice = premium_payment_matches_offer(
        q.invoice_payload, q.currency, q.total_amount, days, price,
    )
    if valid_invoice:
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message=tr(u.language, "invoice_invalid"))


@router.message(F.successful_payment)
async def paid(m: Message, session: AsyncSession, redis: Redis):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    sp = m.successful_payment
    u = await get_user(session, m.from_user)
    current_price, current_days = await get_offer(redis)
    if not premium_payment_matches_offer(
        sp.invoice_payload, sp.currency, sp.total_amount, current_days, current_price,
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
            await m.answer(tr(u.language, "payment_duplicate_recorded"))
        else:
            # Persist unmatched charges before telling the user support must review them.
            await session.commit()
            await m.answer(tr(u.language, "payment_mismatch_contact", support=settings.support_contact))
        return
    # Serialize concurrent purchases for the same user so two successful charges
    # cannot calculate the same expiry and overwrite one another.
    u = await session.scalar(
        select(User).where(User.id == u.id).with_for_update().execution_options(populate_existing=True)
    )
    if u is None:
        raise RuntimeError("Payment user disappeared before entitlement update")
    try:
        days = int(sp.invoice_payload.split(":")[1]) if len(sp.invoice_payload.split(":")) == 3 else current_days
    except (ValueError, IndexError):
        days = current_days
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
        await m.answer(tr(u.language, "payment_duplicate_processed"))
        return
    u.premium_until = expiry
    # Do not tell the customer Premium is active until the charge ledger and
    # entitlement have committed successfully.
    await session.commit()
    metrics.PAYMENTS.inc()
    await m.answer(
        tr(u.language, "payment_success", date=u.premium_until.strftime("%Y-%m-%d"))
        + premium_member_status(u.language, u.premium_until.strftime("%Y-%m-%d")),
        reply_markup=nav_keyboard(u.id, lang=u.language),
    )


@router.message(Command("paysupport"))
async def cmd_paysupport(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    await m.answer(tr(u.language, "payment_support", support=settings.support_contact))


@router.message(Command("terms"))
async def cmd_terms(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
    await m.answer(tr(u.language, "terms_text"))


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
    suspended = await session.scalar(select(func.count()).select_from(User).where(User.suspended.is_(True))) or 0
    blocked = await session.scalar(select(func.count()).select_from(User).where(User.blocked.is_(True))) or 0
    new_users = await session.scalar(select(func.count()).select_from(User).where(User.created_at >= utcnow() - timedelta(days=7))) or 0
    pending_manual = await session.scalar(select(func.count()).select_from(ManualPayment).where(ManualPayment.status == "pending")) or 0
    stars = await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.refunded.is_(False))) or 0
    shared = await session.scalar(select(func.coalesce(func.sum(User.affiliation_shares), 0))) or 0
    referrals = await session.scalar(select(func.coalesce(func.sum(User.referrals_count), 0))) or 0
    killed = "ON" if await redis.exists(KILL_KEY) else "off"
    await m.answer(
        f"Users: {users} (+{new_users} in 7 days)\n"
        f"Premium: {premium}\n"
        f"Active alerts: {alerts}\n"
        f"Suspended / blocked: {suspended} / {blocked}\n"
        f"Unrefunded Telegram Stars: {stars}\n"
        f"Pending manual payments: {pending_manual}\n"
        f"Affiliation shares: {shared} (referrals joined: {referrals})\n"
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
    if p.refund_pending:
        return await m.answer(
            "This payment already has a refund with an uncertain result. Do not retry it automatically; "
            "reconcile the transaction with Telegram first."
        )
    # Serialize refunds for the same account before checking for an in-doubt operation.
    await session.scalar(select(User.id).where(User.id == p.user_id).with_for_update())
    other_pending = await session.scalar(
        select(Payment.id).where(
            Payment.user_id == p.user_id, Payment.refund_pending.is_(True)
        ).limit(1)
    )
    if other_pending:
        return await m.answer(
            "Another refund for this user needs reconciliation first. Resolve it before starting another refund."
        )
    if not p.entitlement_tracked and not allow_legacy:
        return await m.answer(
            "This older/unmatched payment has no Premium entitlement record, so no refund was issued. "
            "Review the account manually first. Explicit override: /refund CHARGE_ID ALLOW_LEGACY "
            "(Premium expiry will remain unchanged)."
        )

    # Persist an in-doubt marker before calling Telegram. If the process dies or the
    # remote call times out, admins can reconcile the charge without blindly retrying.
    p.refund_pending = True
    await session.commit()
    try:
        await bot.refund_star_payment(user_id=p.user_id, telegram_payment_charge_id=p.charge_id)
    except Exception:
        # The API result may be ambiguous; keep refund_pending set and require manual review.
        await m.answer(
            f"Refund result for payment #{p.id} could not be confirmed. It is marked for reconciliation; "
            "do not retry until the Telegram transaction is checked."
        )
        return

    p.refunded = True
    p.refund_pending = False
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
                    Payment.refund_pending.is_(False),
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
    await session.commit()
    await m.answer(f"Refunded {p.amount} Stars to {p.user_id}.{note}")


@admin.message(Command("refundpending"))
async def pending_refunds_cmd(m: Message, session: AsyncSession):
    rows = (await session.scalars(
        select(Payment)
        .where(Payment.refund_pending.is_(True))
        .order_by(Payment.created_at.asc())
        .limit(25)
    )).all()
    if not rows:
        await m.answer("No Stars refunds need reconciliation.")
        return
    lines = [
        f"Payment #{p.id} · user {p.user_id} · {p.amount} Stars · charge {p.charge_id}"
        for p in rows
    ]
    await m.answer(
        "<b>Refunds requiring reconciliation</b>\n"
        + "\n".join(lines)
        + "\n\nCheck each charge in Telegram before using /refundresolve CHARGE_ID REFUNDED or NOT_REFUNDED."
    )


@admin.message(Command("refundresolve"))
async def resolve_pending_refund_cmd(m: Message, command: CommandObject, session: AsyncSession):
    parts = (command.args or "").split()
    if len(parts) != 2 or parts[1].upper() not in {"REFUNDED", "NOT_REFUNDED"}:
        await m.answer("Usage: /refundresolve CHARGE_ID REFUNDED|NOT_REFUNDED — verify the transaction with Telegram first.")
        return
    charge, outcome = parts[0], parts[1].upper()
    payment = await session.scalar(
        select(Payment).where(Payment.charge_id == charge).with_for_update()
    )
    if not payment or not payment.refund_pending or payment.refunded:
        await m.answer("No unresolved refund exists for that charge.")
        return
    if outcome == "NOT_REFUNDED":
        payment.refund_pending = False
        session.add(AdminAudit(
            admin_id=m.from_user.id,
            action="stars_refund_reconciled",
            target_user_id=payment.user_id,
            details=f"payment_id={payment.id}; outcome=not_refunded",
        ))
        await session.commit()
        await m.answer("Marked as not refunded. You may retry /refund after confirming the charge state.")
        return

    payment.refunded = True
    payment.refund_pending = False
    user = await session.get(User, payment.user_id)
    note = " Premium expiry left unchanged."
    if user and payment.entitlement_tracked and payment.entitlement_start and payment.entitlement_end and user.premium_until:
        now = utcnow()
        remaining = refundable_unused_duration(
            payment.entitlement_start, payment.entitlement_end, user.premium_until, now
        )
        if remaining:
            user.premium_until = max(now, user.premium_until - remaining)
            future_payments = (await session.scalars(
                select(Payment)
                .where(
                    Payment.user_id == payment.user_id,
                    Payment.id != payment.id,
                    Payment.refunded.is_(False),
                    Payment.refund_pending.is_(False),
                    Payment.entitlement_tracked.is_(True),
                    Payment.entitlement_start >= payment.entitlement_end,
                )
                .with_for_update()
            )).all()
            for future_payment in future_payments:
                future_payment.entitlement_start -= remaining
                future_payment.entitlement_end -= remaining
        note = f" Premium expiry adjusted by {remaining.days}d {remaining.seconds // 3600}h."
    session.add(AdminAudit(
        admin_id=m.from_user.id,
        action="stars_refund_reconciled",
        target_user_id=payment.user_id,
        details=f"payment_id={payment.id}; outcome=refunded",
    ))
    await session.commit()
    await m.answer(f"Marked payment #{payment.id} as refunded after manual verification.{note}")
