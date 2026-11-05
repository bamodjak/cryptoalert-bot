"""User-facing preferences screens and settings actions."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from .i18n import tr
from .users import get_user

router = Router(name="preferences")

SUPPORTED_TIMEZONES = {
    "UTC": "UTC",
    "Africa/Algiers": "Algiers",
    "Europe/Paris": "Paris",
    "America/New_York": "New York",
    "Asia/Dubai": "Dubai",
}


def settings_text(user) -> str:
    advanced = tr(user.language, "settings_enabled") if user.advanced_mode else tr(user.language, "settings_disabled")
    return tr(user.language, "settings_title", timezone=user.timezone, advanced=advanced)


def settings_keyboard(user) -> InlineKeyboardMarkup:
    rows = []
    timezone_buttons = [
        InlineKeyboardButton(
            text=(f"✓ {label}" if user.timezone == zone else label),
            callback_data=f"settings:tz:{zone}",
        )
        for zone, label in SUPPORTED_TIMEZONES.items()
    ]
    rows.extend([timezone_buttons[:2], timezone_buttons[2:4], timezone_buttons[4:]])
    rows.append([InlineKeyboardButton(
        text=tr(user.language, "settings_advanced_toggle"),
        callback_data="settings:advanced",
    )])
    rows.append([
        InlineKeyboardButton(text=tr(user.language, "language"), callback_data="lang:choose"),
        InlineKeyboardButton(text=tr(user.language, "home"), callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_settings(target, user):
    if target.message:
        await target.message.edit_text(settings_text(user), reply_markup=settings_keyboard(user))
    else:
        await target.answer(settings_text(user), reply_markup=settings_keyboard(user))


@router.message(Command("settings"))
async def cmd_settings(message: Message, session: AsyncSession):
    user = await get_user(session, message.from_user)
    await message.answer(settings_text(user), reply_markup=settings_keyboard(user))


@router.callback_query(F.data == "settings:open")
async def cb_settings(callback: CallbackQuery, session: AsyncSession):
    user = await get_user(session, callback.from_user)
    if callback.message:
        await callback.message.edit_text(settings_text(user), reply_markup=settings_keyboard(user))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:tz:"))
async def cb_settings_timezone(callback: CallbackQuery, session: AsyncSession):
    user = await get_user(session, callback.from_user)
    zone = (callback.data or "").split(":", 2)[-1]
    if zone not in SUPPORTED_TIMEZONES:
        await callback.answer(tr(user.language, "settings_invalid_timezone"), show_alert=True)
        return
    user.timezone = zone
    await session.flush()
    if callback.message:
        await callback.message.edit_text(settings_text(user), reply_markup=settings_keyboard(user))
    await callback.answer(tr(user.language, "settings_saved"))


@router.callback_query(F.data == "settings:advanced")
async def cb_settings_advanced(callback: CallbackQuery, session: AsyncSession):
    user = await get_user(session, callback.from_user)
    user.advanced_mode = not user.advanced_mode
    await session.flush()
    if callback.message:
        await callback.message.edit_text(settings_text(user), reply_markup=settings_keyboard(user))
    await callback.answer(tr(user.language, "settings_saved"))
