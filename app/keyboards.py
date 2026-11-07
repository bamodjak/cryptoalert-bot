"""Shared Telegram keyboard builders. Keep presentation separate from handlers."""
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

from .config import settings
from .db import Alert, utcnow
from .i18n import tr


def nav_keyboard(user_id: int, show_back: bool = True, lang: str = "en") -> InlineKeyboardMarkup:
    """Centered, single-column navigation; admin controls are owner-only."""
    rows = [
        [InlineKeyboardButton(text=tr(lang, "home_create"), callback_data="create:open")],
        [InlineKeyboardButton(text=tr(lang, "market"), callback_data="dash:market")],
        [InlineKeyboardButton(text=tr(lang, "movers"), callback_data="dash:movers")],
        [InlineKeyboardButton(text=tr(lang, "live"), callback_data="live:open")],
        [InlineKeyboardButton(text=tr(lang, "crypto_prices"), callback_data="prices:open")],
        [InlineKeyboardButton(text=tr(lang, "gas"), callback_data="dash:gas")],
        [InlineKeyboardButton(text=tr(lang, "watchlist"), callback_data="wl:open")],
        [InlineKeyboardButton(text=tr(lang, "advisor"), callback_data="advisor:open")],
        [InlineKeyboardButton(text=tr(lang, "my_alerts"), callback_data="dash:alerts")],
        [InlineKeyboardButton(text=tr(lang, "premium"), callback_data="dash:premium")],
        [InlineKeyboardButton(text=tr(lang, "referrals"), callback_data="nav:share")],
        [InlineKeyboardButton(text=tr(lang, "help"), callback_data="nav:help")],
        [InlineKeyboardButton(text=tr(lang, "language"), callback_data="lang:choose")],
        [InlineKeyboardButton(text=tr(lang, "settings"), callback_data="settings:open")],
    ]
    if user_id in settings.admin_id_set:
        rows.append([InlineKeyboardButton(text="🛡 Admin Panel", callback_data="admin:panel")])
    if show_back:
        rows.append([InlineKeyboardButton(text=tr(lang, "home"), callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def persistent_menu_keyboard(language: str = "en", show_back: bool = False) -> ReplyKeyboardMarkup:
    """Localized quick actions; show a persistent Back key only inside a subflow."""
    rows = [
            [
                KeyboardButton(text=tr(language, "quick_dashboard"), style="primary"),
                KeyboardButton(text=tr(language, "quick_markets"), style="primary"),
            ],
            [
                KeyboardButton(text=tr(language, "quick_alerts")),
                KeyboardButton(text=tr(language, "quick_premium"), style="success"),
            ],
        ]
    if show_back:
        rows.append([KeyboardButton(text=tr(language, "back"), style="primary")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Orbsat · Dashboard, markets and alerts" if language != "ar" else "Orbsat · السوق والتنبيهات",
    )


def alerts_keyboard(alerts: list[Alert], user_id: int, lang: str = "en") -> InlineKeyboardMarkup:
    rows = []
    now = utcnow()
    for alert in alerts[:20]:
        control = (
            InlineKeyboardButton(text=tr(lang, "alert_resume"), callback_data=f"alertresume:{alert.id}")
            if alert.snoozed_until and alert.snoozed_until > now
            else InlineKeyboardButton(text=tr(lang, "alert_snooze_1h"), callback_data=f"alertsnooze:{alert.id}")
        )
        rows.append([
            InlineKeyboardButton(text=tr(lang, "alert_edit"), callback_data=f"alertedit:{alert.id}"),
            control,
            InlineKeyboardButton(text=f"✖ #{alert.id}", callback_data=f"alertdel:{alert.id}"),
        ])
    rows.append([InlineKeyboardButton(text=tr(lang, "alert_history"), callback_data="alerts:history")])
    rows.append([
        InlineKeyboardButton(text=tr(lang, "home_create"), callback_data="create:open"),
        InlineKeyboardButton(text=tr(lang, "home"), callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def edit_message_if_changed(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Ignore Telegram no-op edits; propagate all real failures."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
