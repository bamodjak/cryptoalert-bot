"""Shared asset detail presentation and contextual Telegram actions."""
import html
import time

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .assets import get_asset
from .i18n import tr
from .parsing import fmt_compact_usd, fmt_usd


def asset_detail_text(coin: dict, language: str, payload: dict, advanced: bool = False) -> str:
    """Render a consistent, escaped asset summary from normalized market data."""
    rank = coin.get("rank")
    rank_text = f"#{rank}" if rank else tr(language, "asset_rank_unavailable")
    change = coin.get("change_24h")
    change_text = f"{change:+.2f}%" if change is not None else tr(language, "asset_change_unavailable")
    fetched_at = payload.get("fetched_at")
    if fetched_at:
        age_seconds = max(0, int(time.time()) - int(fetched_at))
        freshness = tr(language, "market_just_now") if age_seconds < 60 else tr(
            language, "market_minutes_ago", minutes=max(1, age_seconds // 60)
        )
    else:
        freshness = tr(language, "asset_freshness_unknown")
    if payload.get("stale"):
        freshness += " · " + tr(language, "market_cached")
    body = tr(
        language,
        "asset_detail",
        name=html.escape(str(coin.get("name", "Unknown"))),
        symbol=html.escape(str(coin.get("symbol", "")).upper()),
        rank=rank_text,
        price=fmt_usd(float(coin["price_usd"])) if coin.get("price_usd") is not None else tr(language, "asset_price_unavailable"),
        change=change_text,
        source=html.escape(str(payload.get("source", "market data"))),
        freshness=freshness,
    )
    if advanced:
        market_cap = coin.get("market_cap_usd")
        volume = coin.get("volume_24h_usd")
        if market_cap:
            body += "\n" + tr(language, "asset_market_cap", value=fmt_compact_usd(float(market_cap)))
        if volume:
            body += "\n" + tr(language, "asset_volume_24h", value=fmt_compact_usd(float(volume)))
    return body


def asset_detail_keyboard(symbol: str, language: str, back_callback: str) -> InlineKeyboardMarkup:
    """Keep the same contextual actions available wherever an asset is opened."""
    rows = [
        [InlineKeyboardButton(text=tr(language, "asset_create_alert"), callback_data=f"create:coin:{symbol.lower()}")],
        [InlineKeyboardButton(text=tr(language, "asset_add_watchlist"), callback_data=f"wl:add:{symbol.lower()}")],
    ]
    asset = get_asset(symbol)
    if asset and asset.advisor_enabled:
        rows.append([InlineKeyboardButton(text=tr(language, "asset_analysis"), callback_data="advisor:open")])
    rows.append([InlineKeyboardButton(text=tr(language, "back"), callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
