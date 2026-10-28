"""Administrative workflows for grants, manual payments, users and giveaways.

All mutations are restricted to ADMIN_IDS/OWNER_ID and recorded in admin_audit.
Manual payment records do not activate Premium until an admin verifies them.
"""
import random
import html
import asyncio
import re
from datetime import timedelta
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import (
    AdminAudit, Giveaway, GiveawayEntry, GiveawayWinner, ManualPayment, Payment, PremiumGrant, User, utcnow
)

router = Router(name="admin_tools")
public_router = Router(name="giveaway_public")
router.message.filter(F.from_user.id.in_(settings.admin_id_set))


async def audit(session: AsyncSession, admin_id: int, action: str, target: int | None = None, details: str | None = None) -> None:
    session.add(AdminAudit(admin_id=admin_id, action=action, target_user_id=target, details=details))


def premium_base(user: User):
    return user.premium_until if user.premium_until and user.premium_until > utcnow() else utcnow()


@router.message(Command("users"))
async def users_cmd(m: Message, command: CommandObject, session: AsyncSession):
    """List recent users, or search by exact Telegram ID / username fragment."""
    query = (command.args or "").strip()
    stmt = select(User).order_by(User.created_at.desc()).limit(20)
    if query.isdigit():
        stmt = select(User).where(User.id == int(query)).limit(20)
    elif query:
        stmt = select(User).where(User.username.ilike(f"%{query.lstrip('@')}%")).order_by(User.created_at.desc()).limit(20)
    rows = (await session.scalars(stmt)).all()
    if not rows:
        await m.answer("No users found.")
        return
    lines = []
    for u in rows:
        until = "free (owner)" if u.id == settings.owner_id else (u.premium_until.strftime("%Y-%m-%d UTC") if u.premium_until else "not set")
        lines.append(f"{u.id} @{u.username or 'no-username'} · Premium until: {until} · suspended={u.suspended}")
    await m.answer("<b>Users</b>\n" + "\n".join(lines))


@router.message(Command("grant"))
async def grant_cmd(m: Message, command: CommandObject, session: AsyncSession):
    """Usage: /grant USER_ID DAYS [reason]"""
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await m.answer("Usage: /grant USER_ID DAYS [reason]")
        return
    user_id, days = int(parts[0]), int(parts[1])
    if days < 1 or days > 3650:
        await m.answer("Days must be between 1 and 3650.")
        return
    user = await session.get(User, user_id)
    if not user:
        await m.answer("User not found. They must start the bot first.")
        return
    reason = parts[2] if len(parts) > 2 else "admin grant"
    user.premium_until = premium_base(user) + timedelta(days=days)
    session.add(PremiumGrant(user_id=user_id, granted_by=m.from_user.id, days=days, reason=reason[:256]))
    await audit(session, m.from_user.id, "premium_grant", user_id, f"days={days}; reason={reason[:200]}")
    await m.answer(f"Granted {days} Premium days to {user_id}; expiry {user.premium_until:%Y-%m-%d} UTC.")


async def set_suspension(m: Message, session: AsyncSession, suspended: bool, args: str):
    parts = args.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await m.answer("Usage: /suspend USER_ID [reason] or /unsuspend USER_ID")
        return
    user_id = int(parts[0])
    if user_id in settings.admin_id_set:
        await m.answer("Administrator accounts cannot be suspended with this command.")
        return
    user = await session.get(User, user_id)
    if not user:
        await m.answer("User not found.")
        return
    user.suspended = suspended
    reason = parts[1][:200] if len(parts) > 1 else ""
    await audit(session, m.from_user.id, "suspend" if suspended else "unsuspend", user_id, reason)
    await m.answer(f"User {user_id} {'suspended' if suspended else 'restored'}.")


@router.message(Command("suspend"))
async def suspend_cmd(m: Message, command: CommandObject, session: AsyncSession):
    await set_suspension(m, session, True, command.args or "")


@router.message(Command("unsuspend"))
async def unsuspend_cmd(m: Message, command: CommandObject, session: AsyncSession):
    await set_suspension(m, session, False, command.args or "")


@router.message(Command("manualpay"))
async def manualpay_cmd(m: Message, command: CommandObject, session: AsyncSession):
    """Usage: /manualpay USER_ID METHOD AMOUNT CURRENCY DAYS [reference]"""
    parts = (command.args or "").split()
    if len(parts) < 5 or not parts[0].isdigit() or not parts[4].isdigit():
        await m.answer("Usage: /manualpay USER_ID cash|cash_person|stars|baridimob|ccp|bank AMOUNT CURRENCY DAYS [reference]")
        return
    user_id, method, amount, currency, days = int(parts[0]), parts[1].lower(), parts[2], parts[3].upper(), int(parts[4])
    allowed = {"cash", "cash_person", "stars", "baridimob", "ccp", "bank"}
    if method not in allowed or days < 1 or days > 3650:
        await m.answer("Unsupported method or invalid Premium duration.")
        return
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", amount):
        await m.answer("Amount must be a positive number with at most two decimal places.")
        return
    amount_value = Decimal(amount)
    if amount_value <= 0 or amount_value > Decimal("9999999999999999.99"):
        await m.answer("Amount must be positive and fit the payment field.")
        return
    if not currency.isalnum() or len(currency) > 12:
        await m.answer("Currency must be a short code such as DZD, EUR, or XTR.")
        return
    if method == "stars" and currency != "XTR":
        await m.answer("For Telegram Stars, use currency XTR.")
        return
    if method == "stars" and amount_value != amount_value.to_integral_value():
        await m.answer("Telegram Stars amounts must be whole numbers.")
        return
    if method != "stars" and currency == "XTR":
        await m.answer("Use method 'stars' for Telegram Stars payments.")
        return
    user = await session.get(User, user_id)
    if not user:
        await m.answer("User not found. They must start the bot first.")
        return
    reference = parts[5] if len(parts) > 5 else None
    if reference and len(reference) > 128:
        await m.answer("Reference must be 128 characters or fewer.")
        return
    if reference and await session.scalar(select(ManualPayment.id).where(ManualPayment.reference == reference)):
        await m.answer("That payment reference is already recorded.")
        return
    payment = ManualPayment(
        user_id=user_id, recorded_by=m.from_user.id, method=method, amount=amount_value,
        currency=currency, premium_days=days, reference=reference, status="pending"
    )
    session.add(payment)
    await session.flush()
    await audit(session, m.from_user.id, "manual_payment_pending", user_id, f"payment_id={payment.id}; method={method}; amount={amount} {currency}")
    await m.answer(f"Manual payment #{payment.id} saved as PENDING. It grants no Premium until verified with /verify {payment.id}.")


@router.message(Command("pendingpay"))
async def pending_payments_cmd(m: Message, session: AsyncSession):
    rows = (await session.scalars(
        select(ManualPayment)
        .where(ManualPayment.status == "pending")
        .order_by(ManualPayment.created_at.asc())
        .limit(25)
    )).all()
    if not rows:
        await m.answer("No pending manual payments.")
        return
    lines = [
        f"#{p.id} · user {p.user_id} · {p.method} · {p.amount} {p.currency} · {p.premium_days}d · ref {html.escape((p.reference or '-')[:40])}"
        for p in rows
    ]
    await m.answer("<b>Pending manual payments</b>\n" + "\n".join(lines))


@router.message(Command("verify"))
async def verify_payment_cmd(m: Message, command: CommandObject, session: AsyncSession, bot: Bot):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer("Usage: /verify PAYMENT_ID")
        return
    payment = await session.scalar(select(ManualPayment).where(ManualPayment.id == int(arg)).with_for_update())
    if not payment or payment.status != "pending":
        await m.answer("Pending payment not found.")
        return
    user = await session.get(User, payment.user_id)
    if not user:
        await m.answer("User record not found; payment left pending.")
        return
    payment.status = "verified"
    payment.verified_by = m.from_user.id
    payment.verified_at = utcnow()
    user.premium_until = premium_base(user) + timedelta(days=payment.premium_days)
    session.add(PremiumGrant(user_id=user.id, granted_by=m.from_user.id, days=payment.premium_days, reason=f"manual payment #{payment.id}"))
    await audit(session, m.from_user.id, "manual_payment_verified", user.id, f"payment_id={payment.id}; method={payment.method}; amount={payment.amount} {payment.currency}")
    await session.commit()
    await m.answer(f"Verified payment #{payment.id}; granted {payment.premium_days} Premium days to {user.id}.")
    try:
        await bot.send_message(user.id, f"Your manual payment was verified. Premium is active until {user.premium_until:%Y-%m-%d} UTC.")
    except Exception:
        pass


@router.message(Command("rejectpay"))
async def reject_payment_cmd(m: Message, command: CommandObject, session: AsyncSession):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer("Usage: /rejectpay PAYMENT_ID")
        return
    payment = await session.scalar(select(ManualPayment).where(ManualPayment.id == int(arg)).with_for_update())
    if not payment or payment.status != "pending":
        await m.answer("Pending payment not found.")
        return
    payment.status = "rejected"
    payment.verified_by = m.from_user.id
    payment.verified_at = utcnow()
    await audit(session, m.from_user.id, "manual_payment_rejected", payment.user_id, f"payment_id={payment.id}")
    await m.answer(f"Payment #{payment.id} rejected; no Premium granted.")


@router.message(Command("revenue"))
async def revenue_cmd(m: Message, session: AsyncSession):
    stars = await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.refunded.is_(False))) or 0
    pending = await session.scalar(select(func.count()).select_from(ManualPayment).where(ManualPayment.status == "pending")) or 0
    verified = await session.scalar(select(func.count()).select_from(ManualPayment).where(ManualPayment.status == "verified")) or 0
    totals = (await session.execute(
        select(ManualPayment.currency, func.sum(ManualPayment.amount))
        .where(ManualPayment.status == "verified")
        .group_by(ManualPayment.currency)
        .order_by(ManualPayment.currency)
    )).all()
    amounts = "\n".join(f"Verified {currency}: {amount}" for currency, amount in totals) or "Verified manual amounts: none"
    await m.answer(
        f"<b>Revenue overview</b>\nUnrefunded Telegram Stars: {stars} ⭐\n"
        f"Manual payments verified: {verified}\nManual payments pending: {pending}\n{amounts}"
    )


@router.message(Command("giveaway"))
async def giveaway_cmd(m: Message, command: CommandObject, session: AsyncSession):
    """Usage: /giveaway TITLE | DAYS | WINNERS"""
    parts = [x.strip() for x in (command.args or "").split("|")]
    if len(parts) != 3 or not parts[0] or not parts[1].isdigit() or not parts[2].isdigit():
        await m.answer("Usage: /giveaway Title | PREMIUM_DAYS | WINNERS")
        return
    days, winners = int(parts[1]), int(parts[2])
    if not 1 <= days <= 3650 or not 1 <= winners <= 100:
        await m.answer("Days must be 1–3650 and winners 1–100.")
        return
    g = Giveaway(created_by=m.from_user.id, title=parts[0][:200], premium_days=days, winner_count=winners)
    session.add(g)
    await session.flush()
    await audit(session, m.from_user.id, "giveaway_created", None, f"giveaway_id={g.id}; days={days}; winners={winners}")
    await m.answer(f"Giveaway #{g.id} created. Users can join with /join {g.id}; draw winners with /draw {g.id}.")


@public_router.message(Command("join"))
async def join_giveaway_cmd(m: Message, command: CommandObject, session: AsyncSession):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer("Usage: /join GIVEAWAY_ID")
        return
    giveaway = await session.scalar(select(Giveaway).where(Giveaway.id == int(arg)).with_for_update())
    if not giveaway or giveaway.status != "open":
        await m.answer("That giveaway is not open.")
        return
    user = await session.get(User, m.from_user.id)
    if not user:
        await m.answer("Use /start first.")
        return
    existing = await session.scalar(select(GiveawayEntry.id).where(GiveawayEntry.giveaway_id == giveaway.id, GiveawayEntry.user_id == user.id))
    if existing:
        await m.answer("You're already entered.")
        return
    session.add(GiveawayEntry(giveaway_id=giveaway.id, user_id=user.id))
    await m.answer(f"Entered giveaway #{giveaway.id}: {html.escape(giveaway.title)}")


@router.message(Command("draw"))
async def draw_giveaway_cmd(m: Message, command: CommandObject, session: AsyncSession, bot: Bot):
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer("Usage: /draw GIVEAWAY_ID")
        return
    giveaway = await session.scalar(select(Giveaway).where(Giveaway.id == int(arg)).with_for_update())
    if not giveaway or giveaway.status != "open":
        await m.answer("Open giveaway not found.")
        return
    entries = list((await session.scalars(
        select(GiveawayEntry)
        .join(User, User.id == GiveawayEntry.user_id)
        .where(GiveawayEntry.giveaway_id == giveaway.id, User.suspended.is_(False))
    )).all())
    if not entries:
        await m.answer("No entries yet; giveaway remains open.")
        return
    winners = random.SystemRandom().sample(entries, min(giveaway.winner_count, len(entries)))
    giveaway.status = "drawn"
    giveaway.drawn_at = utcnow()
    winner_ids = []
    notifications = []
    for entry in winners:
        user = await session.get(User, entry.user_id)
        if not user:
            continue
        user.premium_until = premium_base(user) + timedelta(days=giveaway.premium_days)
        winner_ids.append(user.id)
        notifications.append((user.id, html.escape(giveaway.title), giveaway.premium_days))
        session.add(GiveawayWinner(giveaway_id=giveaway.id, user_id=user.id, premium_days=giveaway.premium_days))
        session.add(PremiumGrant(user_id=user.id, granted_by=m.from_user.id, days=giveaway.premium_days, reason=f"giveaway #{giveaway.id}"))
    await audit(session, m.from_user.id, "giveaway_drawn", None, f"giveaway_id={giveaway.id}; winners={winner_ids}")
    await session.commit()
    for user_id, title, days in notifications:
        try:
            await bot.send_message(user_id, f"You won giveaway: {title}! {days} Premium days have been granted.")
        except Exception:
            pass
    await m.answer(f"Giveaway #{giveaway.id} drawn. Winners: {', '.join(map(str, winner_ids)) or 'none'}")


@router.message(Command("broadcast"))
async def broadcast_cmd(m: Message, command: CommandObject, redis: Redis):
    text = (command.args or "").strip()
    if not text:
        await m.answer("Usage: /broadcast your announcement text\nThis creates a preview; confirm it with the button before sending.")
        return
    announcement = text[:600]
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Send broadcast", callback_data="admin:broadcast:confirm"),
        InlineKeyboardButton(text="Cancel", callback_data="admin:broadcast:cancel"),
    ]])
    preview_message = await m.answer(
        f"<b>Broadcast preview</b>\n\n{html.escape(announcement, quote=True)}\n\n"
        "Confirm to send to all registered users. Preview is limited to 600 characters.",
        reply_markup=markup,
    )
    payload_key = f"admin:broadcast:payload:{preview_message.chat.id}:{preview_message.message_id}"
    await redis.set(payload_key, announcement, ex=3600)


@router.callback_query(F.data == "admin:broadcast:cancel")
async def cancel_broadcast(cb: CallbackQuery, redis: Redis):
    if cb.from_user.id not in settings.admin_id_set:
        await cb.answer("Not authorized.", show_alert=True)
        return
    if cb.message:
        payload_key = f"admin:broadcast:payload:{cb.message.chat.id}:{cb.message.message_id}"
        await redis.delete(payload_key)
        await cb.message.edit_text("Broadcast cancelled.")
    await cb.answer()


@router.callback_query(F.data == "admin:broadcast:confirm")
async def confirm_broadcast(cb: CallbackQuery, session: AsyncSession, bot: Bot, redis: Redis):
    if cb.from_user.id not in settings.admin_id_set:
        await cb.answer("Not authorized.", show_alert=True)
        return
    if not cb.message or not cb.message.text:
        await cb.answer("Broadcast preview unavailable.", show_alert=True)
        return
    payload_key = f"admin:broadcast:payload:{cb.message.chat.id}:{cb.message.message_id}"
    announcement = await redis.get(payload_key)
    if not announcement:
        await cb.answer("Broadcast preview expired or already processed.", show_alert=True)
        return
    lock_key = f"admin:broadcast:lock:{cb.message.chat.id}:{cb.message.message_id}"
    claimed = await redis.set(lock_key, str(cb.from_user.id), nx=True, ex=3600)
    if not claimed:
        await cb.answer("This broadcast has already been processed.", show_alert=True)
        return
    global_lock_key = "admin:broadcast:global"
    global_claimed = await redis.set(global_lock_key, lock_key, nx=True, ex=3600)
    if not global_claimed:
        await redis.delete(lock_key)
        await cb.answer("Another broadcast is already running.", show_alert=True)
        return
    await cb.answer("Broadcast started.")
    ids = list((await session.scalars(select(User.id).where(User.suspended.is_(False)))).all())
    await session.commit()  # release the DB connection before network sends
    sent = 0
    failed = 0
    for uid in ids:
        try:
            await bot.send_message(uid, announcement, parse_mode=None)
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                await bot.send_message(uid, announcement, parse_mode=None)
                sent += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)
    await audit(session, cb.from_user.id, "broadcast_sent", None, f"sent={sent}; failed={failed}")
    await redis.delete(payload_key)
    if await redis.get(global_lock_key) == lock_key:
        await redis.delete(global_lock_key)
    await cb.message.edit_text(f"Broadcast completed. Delivered: {sent}; failed: {failed}.")
