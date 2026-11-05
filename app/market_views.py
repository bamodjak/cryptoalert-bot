"""Provider-neutral formatting and keyboards for market screens."""
import html
import time

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .i18n import tr
from .parsing import fmt_compact_usd, fmt_usd

def crypto_prices_keyboard(coins: list[dict], language: str) -> InlineKeyboardMarkup:
    rows = []
    for index in range(0, len(coins), 2):
        pair = coins[index:index + 2]
        rows.append([
            InlineKeyboardButton(
                text=f"{coin['rank']}. {coin['symbol']}",
                callback_data=f"prices:coin:{coin['rank']}",
            )
            for coin in pair
        ])
    rows.append([InlineKeyboardButton(text=tr(language, "prices_all"), callback_data="prices:all")])
    rows.append([InlineKeyboardButton(text=tr(language, "back"), callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_prices_text(payload: dict, language: str, selected: dict | None = None) -> str:
    age_seconds = max(0, int(time.time()) - int(payload["fetched_at"]))
    age = tr(language, "market_just_now") if age_seconds < 60 else tr(
        language, "market_minutes_ago", minutes=max(1, age_seconds // 60)
    )
    if payload.get("stale"):
        age += " · " + tr(language, "market_cached")
    coins = [selected] if selected else payload["coins"]
    lines = [tr(language, "crypto_prices_title")]
    for coin in coins:
        lines.append(
            f"<b>{coin['rank']}. {coin['symbol']}</b> ({html.escape(coin['name'])}) · "
            f"{fmt_usd(coin['price_usd'])} · 24h {coin['change_24h']:+.2f}%"
        )
    lines.append(tr(language, "crypto_prices_updated", age=age, source=payload.get("source", "market data")))
    return "\n".join(lines)

def market_snapshot_text(snapshot: dict, language: str) -> str:
    age_seconds = max(0, int(time.time()) - int(snapshot["fetched_at"]))
    age = tr(language, "market_just_now") if age_seconds < 60 else tr(
        language, "market_minutes_ago", minutes=max(1, age_seconds // 60)
    )
    if snapshot.get("stale"):
        age += " · " + tr(language, "market_cached")
    return tr(
        language,
        "market_snapshot",
        market_cap=fmt_compact_usd(snapshot["market_cap_usd"]),
        volume=fmt_compact_usd(snapshot["volume_24h_usd"]),
        change=f"{snapshot['market_cap_change_24h']:+.2f}%",
        dominance=f"{snapshot['btc_dominance']:.1f}%",
        active=f"{snapshot['active_cryptocurrencies']:,}",
        age=age,
    )

def market_movers_text(snapshot: dict, language: str) -> str:
    age_seconds = max(0, int(time.time()) - int(snapshot["fetched_at"]))
    age = tr(language, "market_just_now") if age_seconds < 60 else tr(
        language, "market_minutes_ago", minutes=max(1, age_seconds // 60)
    )
    if snapshot.get("stale"):
        age += " · " + tr(language, "market_cached")

    def render(items):
        return "\n".join(
            f"<b>{index}.</b> {html.escape(item['name'])} ({html.escape(item['symbol'])}) "
            f"<b>{item['change_24h']:+.2f}%</b> · {fmt_usd(item['price_usd'])}"
            for index, item in enumerate(items, 1)
        )

    return tr(
        language,
        "market_movers_snapshot",
        gainers=render(snapshot["gainers"]),
        losers=render(snapshot["losers"]),
        age=age,
    )


def market_movers_keyboard(snapshot: dict, language: str) -> InlineKeyboardMarkup:
    rows = []
    for item in snapshot["gainers"] + snapshot["losers"]:
        label = f"{item['symbol'].upper()} {item['change_24h']:+.1f}%"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"asset:mover:{item['id']}")])
    rows.append([InlineKeyboardButton(text=tr(language, "home"), callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
