"""Shared asset detail presentation and contextual Telegram actions."""
import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .i18n import tr
from .parsing import fmt_usd


def asset_detail_text(coin: dict, language: str, payload: dict) -> str:
    """Render a consistent, escaped asset summary from normalized market data."""
    rank = coin.get("rank")
    rank_text = f"#{rank}" if rank else tr(language, "asset_rank_unavailable")
    change = coin.get("change_24h")
    change_text = f"{change:+.2f}%" if change is not None else tr(language, "asset_change_unavailable")
    stale = tr(language, "market_cached") if payload.get("stale") else tr(language, "market_just_now")
    return tr(
        language,
        "asset_detail",
        name=html.escape(str(coin.get("name", "Unknown"))),
        symbol=html.escape(str(coin.get("symbol", "")).upper()),
        rank=rank_text,
        price=fmt_usd(float(coin["price_usd"])) if coin.get("price_usd") is not None else tr(language, "asset_price_unavailable"),
        change=change_text,
        source=html.escape(str(payload.get("source", "market data"))),
        freshness=stale,
    )


def asset_detail_keyboard(symbol: str, language: str, back_callback: str) -> InlineKeyboardMarkup:
    """Keep the same contextual actions available wherever an asset is opened."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "asset_create_alert"), callback_data=f"create:coin:{symbol.lower()}")],
        [InlineKeyboardButton(text=tr(language, "asset_add_watchlist"), callback_data=f"wl:add:{symbol.lower()}")],
        [InlineKeyboardButton(text=tr(language, "asset_analysis"), callback_data="advisor:open")],
        [InlineKeyboardButton(text=tr(language, "back"), callback_data=back_callback)],
    ])
