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
from .db import Alert, Payment, User, utcnow
from .middlewares import KILL_KEY
from .parsing import fmt_gwei, fmt_usd, parse_alert, parse_number, parse_referral
from .prices import PriceError, PriceService

router = Router(name="user")
admin = Router(name="admin")
admin.message.filter(F.from_user.id.in_(settings.admin_id_set))

HELP = (
    "<b>Orbsat Labs Shop — Crypto &amp; Gas Alert Bot</b>\n\n"
    "/price btc — current price\n"
    "/alert btc &gt; 70000 — ping me when BTC goes above $70,000\n"
    "/alert eth below 2.5k — ping me when ETH drops below $2,500\n"
    "/gas — Ethereum gas now\n"
    "/gasalert 0.04 — ping me when gas drops below 0.04 gwei (Premium)\n"
    "/alerts — your active alerts\n"
    "/del 12 — delete alert #12\n"
    "/premium — more alerts + gas alerts\n"
    "/terms · /paysupport\n\n"
    f"Free: {settings.free_alert_limit} alerts. Premium ({settings.premium_price_stars} ⭐ / {settings.premium_days}d): "
    f"{settings.premium_alert_limit} alerts + gas alerts.\n"
    "<i>Information only, not financial advice.</i>"
)


def nav_keyboard(user_id: int, show_back: bool = True) -> InlineKeyboardMarkup:
    ref_link = f"https://t.me/{settings.bot_username}?start=ref_{user_id}"
    share_text = "Real-time Crypto Price & Ethereum Gas Alerts on Telegram!"
    share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote(share_text)}"
    top_row = []
    if show_back:
        top_row.append(InlineKeyboardButton(text="⬅️ Back", callback_data="nav:back"))
    top_row.append(InlineKeyboardButton(text="🏠 Home", callback_data="nav:home"))
    top_row.append(InlineKeyboardButton(text="🔗 Share Affiliation Link", callback_data="nav:share"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            top_row,
            [InlineKeyboardButton(text="📤 Forward Referral Link", url=share_url)],
        ]
    )


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
        "Your alerts:\n" + "\n".join(lines) + "\n\nDelete with /del &lt;id&gt;",
        reply_markup=nav_keyboard(u.id),
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


# ---------- Payments (Telegram Stars) ----------

@router.message(Command("premium"))
async def cmd_premium(m: Message, session: AsyncSession):
    u = await get_user(session, m.from_user)
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
    if q.invoice_payload.startswith("premium:") and q.currency == "XTR":
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="This invoice is no longer valid.")


@router.message(F.successful_payment)
async def paid(m: Message, session: AsyncSession):
    sp = m.successful_payment
    days = int(sp.invoice_payload.split(":")[1])
    u = await get_user(session, m.from_user)
    base = u.premium_until if u.is_premium else utcnow()
    u.premium_until = base + timedelta(days=days)
    session.add(Payment(user_id=u.id, charge_id=sp.telegram_payment_charge_id, amount=sp.total_amount))
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
    premium = await session.scalar(select(func.count()).select_from(User).where(User.premium_until > utcnow())) or 0
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
    charge = (command.args or "").strip()
    p = await session.scalar(select(Payment).where(Payment.charge_id == charge)) if charge else None
    if not p or p.refunded:
        return await m.answer("Usage: /refund <charge_id> (unrefunded payment)")
    await bot.refund_star_payment(user_id=p.user_id, telegram_payment_charge_id=p.charge_id)
    p.refunded = True
    u = await session.get(User, p.user_id)
    if u:
        u.premium_until = utcnow()
    await m.answer(f"Refunded {p.amount} Stars to {p.user_id}.")
