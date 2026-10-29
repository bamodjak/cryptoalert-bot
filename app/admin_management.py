"""Button-first admin user and Premium management."""
import csv
import html
import io
import json
import secrets
from datetime import timedelta
from urllib.parse import quote

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import AdminAudit, Payment, PremiumGrant, PremiumInvite, User, utcnow

router = Router(name="admin_management")
router.callback_query.filter(F.from_user.id.in_(settings.admin_id_set), F.message.chat.type == "private")
router.message.filter(F.from_user.id.in_(settings.admin_id_set))
router.message.filter(F.chat.type == "private")

PAGE_SIZE = 8
FLOW_TTL = 900


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _back():
    return [InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="admin:panel")]


async def _premium_kind(session: AsyncSession, u: User) -> str:
    now = utcnow()
    if u.id == settings.owner_id:
        return "Owner · complimentary"
    if not u.premium_until or u.premium_until <= now:
        return "Free"
    paid_end = await session.scalar(select(func.max(Payment.entitlement_end)).where(
        Payment.user_id == u.id, Payment.refunded.is_(False),
        Payment.entitlement_tracked.is_(True),
    ))
    last_paid = await session.scalar(select(func.max(Payment.created_at)).where(
        Payment.user_id == u.id, Payment.refunded.is_(False),
    ))
    last_grant = await session.scalar(select(func.max(PremiumGrant.created_at)).where(
        PremiumGrant.user_id == u.id,
    ))
    if last_grant and (not last_paid or last_grant > last_paid):
        return "Free Premium grant"
    if paid_end and paid_end > now:
        return "Paid Premium"
    if last_grant:
        return "Free Premium grant"
    return "Premium · source unclear"


def _user_name(u: User) -> str:
    name = " ".join(x for x in (u.first_name, u.last_name) if x).strip()
    if name:
        return html.escape(name[:80])
    return "Name not captured yet"


async def _user_card(session: AsyncSession, u: User) -> str:
    kind = await _premium_kind(session, u)
    days = max(0, (u.premium_until - utcnow()).days) if u.premium_until and u.premium_until > utcnow() else 0
    expiry = f"{u.premium_until:%Y-%m-%d} UTC · {days} day(s) left" if days else "No active Premium"
    username = f"@{html.escape(u.username)}" if u.username else "no username"
    return (
        f"👤 <b>{_user_name(u)}</b>\n{username}\n"
        f"🆔 <code>{u.id}</code>\n⭐ {html.escape(kind)}\n"
        f"📅 {expiry}\n"
        f"🔒 {'Suspended' if u.suspended else 'Active account'}"
    )


async def _show_users(target, session: AsyncSession, page: int = 0, mode: str = "all"):
    total_stmt = select(func.count()).select_from(User)
    stmt = select(User).order_by(User.created_at.desc(), User.id.desc())
    now = utcnow()
    if mode == "premium":
        stmt = stmt.where((User.premium_until > now) | (User.id == settings.owner_id))
        total_stmt = total_stmt.where((User.premium_until > now) | (User.id == settings.owner_id))
    elif mode == "free":
        stmt = stmt.where((User.premium_until.is_(None)) | (User.premium_until <= now))
        total_stmt = total_stmt.where((User.premium_until.is_(None)) | (User.premium_until <= now))
    total = await session.scalar(total_stmt) or 0
    page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    users = list((await session.scalars(stmt.offset(page * PAGE_SIZE).limit(PAGE_SIZE))).all())
    cards = [await _user_card(session, u) for u in users]
    title = {"all": "All registered users", "premium": "Premium users", "free": "Free users"}[mode]
    text = f"<b>👥 {title}</b>\nTotal: {total} · Page {page + 1}/{page_count}\n\n" + ("\n\n".join(cards) if cards else "No users on this page.")
    rows = []
    if page > 0 or page + 1 < page_count:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ Previous", callback_data=f"um:list:{mode}:{page-1}"))
        if page + 1 < page_count:
            nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"um:list:{mode}:{page+1}"))
        rows.append(nav)
    rows += [
        [InlineKeyboardButton(text="🔎 Find by ID / username", callback_data="um:find")],
        [InlineKeyboardButton(text="🎁 Grant Premium to user(s)", callback_data="um:grant")],
        [InlineKeyboardButton(text="📤 Export registered users (CSV)", callback_data="um:export:users")],
        [InlineKeyboardButton(text="🔗 Export invitation links (CSV)", callback_data="um:export:invites")],
        [InlineKeyboardButton(text="➕ Add days to ALL users", callback_data="um:all")],
        [
            InlineKeyboardButton(text="👥 All", callback_data="um:list:all:0"),
            InlineKeyboardButton(text="⭐ Premium", callback_data="um:list:premium:0"),
            InlineKeyboardButton(text="🆓 Free", callback_data="um:list:free:0"),
        ],
        _back(),
    ]
    markup = _kb(rows)
    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _set_flow(redis: Redis, admin_id: int, state: dict):
    await redis.set(f"adminflow:{admin_id}", json.dumps(state), ex=FLOW_TTL)



def _safe_csv_cell(value: object) -> object:
    """Neutralize spreadsheet formulas in untrusted text exported to CSV."""
    if not isinstance(value, str):
        return value
    stripped = value.lstrip(" \t\r\n")
    if value[:1] in {"=", "+", "-", "@", "\t", "\r", "\n"} or stripped[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def _csv_file(filename: str, headers: list[str], rows: list[list[object]]) -> BufferedInputFile:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows([[_safe_csv_cell(value) for value in row] for row in rows])
    return BufferedInputFile(stream.getvalue().encode("utf-8-sig"), filename=filename)


@router.callback_query(F.data == "um:export:users")
async def export_users(cb: CallbackQuery, session: AsyncSession):
    users = list((await session.scalars(select(User).order_by(User.created_at.asc(), User.id.asc()))).all())
    rows = []
    for u in users:
        premium_kind = await _premium_kind(session, u)
        now = utcnow()
        days = max(0, (u.premium_until - now).days) if u.premium_until and u.premium_until > now else 0
        paid_stars = await session.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.user_id == u.id, Payment.refunded.is_(False)
        )) or 0
        grant_days = await session.scalar(select(func.coalesce(func.sum(PremiumGrant.days), 0)).where(
            PremiumGrant.user_id == u.id
        )) or 0
        rows.append([
            u.id, u.username or "", u.first_name or "", u.last_name or "",
            u.created_at.isoformat() if u.created_at else "", premium_kind,
            u.premium_until.isoformat() if u.premium_until else "", days,
            "yes" if u.suspended else "no", "yes" if u.blocked else "no",
            paid_stars, grant_days, u.referred_by or "", u.referrals_count or 0,
            u.affiliation_shares or 0,
        ])
    document = _csv_file(
        "orbsat_registered_users.csv",
        ["telegram_id", "username", "first_name", "last_name", "registered_at_utc",
         "premium_status", "premium_until_utc", "premium_days_left", "suspended",
         "blocked", "non_refunded_stars_total", "premium_grant_days_total",
         "referred_by_id", "referrals_joined", "referral_shares"],
        rows,
    )
    if cb.message:
        await cb.message.answer_document(document, caption=f"Registered users export · {len(rows)} account(s)")
    await cb.answer("Export ready.")


@router.callback_query(F.data == "um:export:invites")
async def export_invites(cb: CallbackQuery, session: AsyncSession):
    users = list((await session.scalars(
        select(User).where(User.premium_until > utcnow()).order_by(User.id.asc())
    )).all())
    rows = []
    for u in users:
        invite = await session.scalar(
            select(PremiumInvite).where(PremiumInvite.recipient_id == u.id)
            .order_by(PremiumInvite.created_at.desc(), PremiumInvite.id.desc()).limit(1)
        )
        if invite is None:
            invite = PremiumInvite(
                token=secrets.token_urlsafe(18), recipient_id=u.id, created_by=cb.from_user.id
            )
            session.add(invite)
            await session.flush()
        kind = await _premium_kind(session, u)
        days = max(0, (u.premium_until - utcnow()).days) if u.premium_until else 0
        link = f"https://t.me/{settings.bot_username}?start=invite_{invite.token}"
        rows.append([
            u.id, u.username or "", " ".join(x for x in (u.first_name, u.last_name) if x),
            kind, u.premium_until.isoformat() if u.premium_until else "", days, link,
            invite.recipient_started_at.isoformat() if invite.recipient_started_at else "",
            invite.clicked_at.isoformat() if invite.clicked_at else "",
            invite.clicked_by or "",
            "Started by intended recipient" if invite.recipient_started_at else "Invitation not yet started by intended recipient",
        ])
    await session.commit()
    document = _csv_file(
        "orbsat_premium_invite_tracking.csv",
        ["telegram_id", "username", "display_name", "premium_status", "premium_until_utc",
         "premium_days_left", "personalized_bot_link", "recipient_started_at_utc",
         "first_click_at_utc", "first_clicker_telegram_id", "invite_status"],
        rows,
    )
    if cb.message:
        await cb.message.answer_document(
            document,
            caption=f"Personalized Premium invitations · {len(rows)} row(s). Links are tied to the intended Telegram ID; a different account cannot claim that recipient's access.",
        )
    await cb.answer("Invitation export ready.")

@router.callback_query(F.data == "admin:users")
async def open_users(cb: CallbackQuery, session: AsyncSession):
    await cb.answer()
    await _show_users(cb, session)


@router.callback_query(F.data.startswith("um:list:"))
async def page_users(cb: CallbackQuery, session: AsyncSession):
    try:
        _, _, mode, page = (cb.data or "").split(":")
        page_num = int(page)
        if mode not in {"all", "premium", "free"}:
            raise ValueError
    except ValueError:
        await cb.answer("Invalid page.", show_alert=True)
        return
    await cb.answer()
    await _show_users(cb, session, page_num, mode)


@router.callback_query(F.data == "um:find")
async def start_find(cb: CallbackQuery, redis: Redis):
    await _set_flow(redis, cb.from_user.id, {"state": "find"})
    await cb.answer()
    if cb.message:
        await cb.message.answer("Send the user's Telegram ID or @username as a normal message.")


@router.callback_query(F.data.in_({"um:grant", "um:all"}))
async def start_grant(cb: CallbackQuery, redis: Redis):
    all_users = cb.data == "um:all"
    await _set_flow(redis, cb.from_user.id, {"state": "grant_days", "target": "all" if all_users else "ids"})
    await cb.answer()
    if cb.message:
        if all_users:
            await cb.message.answer("You chose ALL registered users. Send the number of days to add (1–3650). A confirmation will appear before anything changes.")
        else:
            await cb.message.answer("Send Telegram IDs, one per line. You can paste multiple IDs. Only registered users will be changed. Send /cancel to stop.")


@router.message(F.text)
async def handle_admin_text(m: Message, session: AsyncSession, redis: Redis):
    raw = (m.text or "").strip()
    key = f"adminflow:{m.from_user.id}"
    state_raw = await redis.get(key)
    if not state_raw:
        raise SkipHandler
    if raw.lower() in {"/cancel", "cancel"}:
        await redis.delete(key)
        await m.answer("Cancelled. No changes were made.", reply_markup=_kb([_back()]))
        return
    try:
        state = json.loads(state_raw)
    except (ValueError, TypeError):
        await redis.delete(key)
        await m.answer("That admin action expired. Open Users & Premium and try again.")
        return

    if state.get("state") == "find":
        await redis.delete(key)
        q = raw.lstrip("@")
        stmt = select(User).where(User.id == int(q)) if q.isdigit() else select(User).where(User.username.ilike(q)).limit(10)
        users = list((await session.scalars(stmt.limit(10))).all())
        if not users:
            await m.answer("No registered user found. They must have pressed Start at least once.")
            return
        rows = []
        cards = []
        for u in users:
            cards.append(await _user_card(session, u))
            rows.append([InlineKeyboardButton(text=f"Grant Premium · {u.id}", callback_data=f"um:grantone:{u.id}")])
        rows.append(_back())
        await m.answer("\n\n".join(cards), reply_markup=_kb(rows))
        return

    if state.get("state") == "grant_days":
        target = state.get("target")
        # The first message for a multi-user grant is a pasted list of IDs.
        if target == "ids" and not state.get("ids"):
            parsed = []
            invalid = []
            for line in raw.splitlines():
                token = line.strip().lstrip("@")
                if not token:
                    continue
                if token.isdigit() and int(token) > 0:
                    parsed.append(int(token))
                else:
                    invalid.append(token)
            if not parsed:
                await m.answer("I couldn't find numeric Telegram IDs. Send IDs one per line, or /cancel.")
                return
            await _set_flow(redis, m.from_user.id, {"state": "grant_days", "target": "ids", "ids": sorted(set(parsed)), "invalid": invalid})
            await m.answer(f"Received {len(set(parsed))} unique ID(s). Now send how many Premium days to add (1–3650).")
            return
        if not raw.isdigit() or not 1 <= int(raw) <= 3650:
            await m.answer("Enter a whole number from 1 to 3650 days, or /cancel.")
            return
        days = int(raw)
        if target == "all":
            ids = list((await session.scalars(select(User.id).order_by(User.id))).all())
        else:
            ids = state.get("ids", [])
        existing = list((await session.scalars(select(User).where(User.id.in_(ids)))).all()) if ids else []
        found_ids = {u.id for u in existing}
        missing = sorted(set(ids) - found_ids)
        payload = {
            "ids": sorted(set(ids)),
            "existing_ids": sorted(found_ids),
            "days": days,
            "missing": missing,
            "invalid": state.get("invalid", []),
        }
        await _set_flow(redis, m.from_user.id, {"state": "confirm_grant", "payload": payload})
        await m.answer(
            f"<b>Confirm Premium grant</b>\nExisting accounts: {len(found_ids)}\n"
            f"New account records to create: {len(missing)}\n"
            f"Premium days to add per ID: {days}\nInvalid lines: {len(payload['invalid'])}\n\n"
            "Unknown IDs will be added to the database and granted Premium now. "
            "They can use the bot after they press Start. Confirm below to apply.",
            reply_markup=_kb([[InlineKeyboardButton(text="✅ Confirm", callback_data="um:confirm")],
                              [InlineKeyboardButton(text="❌ Cancel", callback_data="um:cancel")]]),
        )
        return

    await m.answer("This admin action is waiting for a button. Use /cancel to stop.")


@router.callback_query(F.data.startswith("um:grantone:"))
async def grant_one(cb: CallbackQuery, redis: Redis):
    try:
        uid = int((cb.data or "").rsplit(":", 1)[1])
    except ValueError:
        await cb.answer("Invalid user.", show_alert=True)
        return
    await _set_flow(redis, cb.from_user.id, {"state": "grant_days", "target": "ids", "ids": [uid]})
    await cb.answer()
    if cb.message:
        await cb.message.answer(f"Send the number of Premium days to add for user {uid} (1–3650).")


@router.callback_query(F.data == "um:confirm")
async def confirm_grant(cb: CallbackQuery, session: AsyncSession, redis: Redis):
    key = f"adminflow:{cb.from_user.id}"
    raw = await redis.get(key)
    if not raw:
        await cb.answer("Confirmation expired. Start again.", show_alert=True)
        return
    state = json.loads(raw)
    if state.get("state") != "confirm_grant":
        await cb.answer("No pending change.", show_alert=True)
        return
    payload = state["payload"]
    ids, days = sorted(set(payload["ids"])), int(payload["days"])
    if not ids:
        await redis.delete(key)
        await cb.answer("No valid Telegram IDs were supplied.", show_alert=True)
        return
    users = list((await session.scalars(select(User).where(User.id.in_(ids)).with_for_update())).all())
    existing_ids = {u.id for u in users}
    new_ids = [uid for uid in ids if uid not in existing_ids]
    for uid in new_ids:
        # A Telegram user ID is sufficient to prepare an account and entitlement.
        # Telegram may not let the bot DM this user until they press Start.
        users.append(User(id=uid, username=None, first_name=None, last_name=None))
    if new_ids:
        session.add_all([u for u in users if u.id in set(new_ids)])
        await session.flush()
    for u in users:
        if u.id in settings.admin_id_set and u.id != settings.owner_id:
            continue
        base = u.premium_until if u.premium_until and u.premium_until > utcnow() else utcnow()
        u.premium_until = base + timedelta(days=days)
        session.add(PremiumGrant(user_id=u.id, granted_by=cb.from_user.id, days=days, reason="admin panel bulk grant"))
        session.add(AdminAudit(admin_id=cb.from_user.id, action="premium_grant", target_user_id=u.id, details=f"days={days}; source=admin_panel"))
    # Only IDs that have never been registered need an invitation to discover the bot.
    invites = []
    for uid in new_ids:
        if uid in settings.admin_id_set and uid != settings.owner_id:
            continue
        invite = PremiumInvite(token=secrets.token_urlsafe(18), recipient_id=uid, created_by=cb.from_user.id)
        session.add(invite)
        invites.append(invite)
    await session.commit()
    await redis.delete(key)
    updated = len([u for u in users if u.id not in settings.admin_id_set or u.id == settings.owner_id])
    missing = payload.get("missing", [])
    result = f"✅ Premium added for {updated} account(s): {days} day(s) each."
    if new_ids:
        result += f"\nCreated {len(new_ids)} new account record(s) from Telegram IDs."
    if missing:
        result += f"\nNew IDs created: {len(missing)}."
    if payload.get("invalid"):
        result += f"\nIgnored non-numeric lines: {len(payload['invalid'])}."
    if cb.message:
        await cb.message.edit_text(result, reply_markup=_kb([
            [InlineKeyboardButton(text="👥 Back to Users", callback_data="admin:users")],
            _back(),
        ]))
        for invite in invites:
            link = f"https://t.me/{settings.bot_username}?start=invite_{invite.token}"
            invite_text = (
                "🎁 You've been gifted Premium on Orbsat Crypto Alerts!\n\n"
                f"Your access is reserved for Telegram ID {invite.recipient_id}. "
                f"Open this link and tap Start to use your Premium access:\n{link}\n\n"
                f"Premium duration added: {days} day(s)."
            )
            share_url = "https://t.me/share/url?url=" + quote(link, safe="") + "&text=" + quote(
                "You've been gifted Premium on Orbsat Crypto Alerts! Open the link and tap Start: " + link,
                safe="",
            )
            await cb.message.answer(
                f"<b>Invitation for Telegram ID {invite.recipient_id}</b>\n\n"
                f"<code>{link}</code>\n\n{invite_text}",
                reply_markup=_kb([[InlineKeyboardButton(text="📨 Share invitation", url=share_url)]]),
            )
    await cb.answer("Completed.")


@router.callback_query(F.data == "um:cancel")
async def cancel_grant(cb: CallbackQuery, redis: Redis):
    await redis.delete(f"adminflow:{cb.from_user.id}")
    if cb.message:
        await cb.message.edit_text("Cancelled. No Premium days were changed.", reply_markup=_kb([
            [InlineKeyboardButton(text="👥 Back to Users", callback_data="admin:users")], _back()
        ]))
    await cb.answer()
