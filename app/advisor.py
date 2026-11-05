"""Premium quantitative market advisor.

Uses exchange OHLCV history, multi-horizon trend/momentum, RSI, ATR and
realized volatility. This is a transparent rule ensemble, not a trained
predictive model and never places orders.
"""
import asyncio
import json
import logging
import math
import time

import aiohttp

from .assets import CORE_ASSETS
from redis.asyncio import Redis

log = logging.getLogger(__name__)

UNIVERSE = tuple(
    (asset.symbol.upper(), asset.binance_pair.upper())
    for asset in CORE_ASSETS
    if asset.advisor_enabled and asset.binance_pair
)
CACHE_KEY = "advisor:premium:latest"
CACHE_SECONDS = 300


class AdvisorUnavailable(Exception):
    pass


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("not enough observations")
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _rsi(values: list[float], period: int = 14) -> float:
    changes = [b - a for a, b in zip(values[-period - 1:], values[-period:])]
    gains = sum(max(x, 0) for x in changes) / period
    losses = sum(max(-x, 0) for x in changes) / period
    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))


def _analyse_klines(symbol: str, rows: list[list]) -> dict:
    candles = [
        (float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        for row in rows
    ]
    closes = [c[3] for c in candles]
    highs = [c[1] for c in candles]
    lows = [c[2] for c in candles]
    volumes = [c[4] for c in candles]
    if len(closes) < 60 or any(not math.isfinite(x) or x <= 0 for x in closes):
        raise ValueError("insufficient or invalid OHLCV history")

    price = closes[-1]
    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    rsi = _rsi(closes)
    momentum_24h = price / closes[-7] - 1
    momentum_7d = price / closes[-43] - 1
    true_ranges = []
    for i in range(1, len(candles)):
        true_ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr = sum(true_ranges[-14:]) / 14
    daily_returns = [
        closes[i] / closes[i - 6] - 1
        for i in range(max(6, len(closes) - 42), len(closes))
    ]
    realized_vol = math.sqrt(sum(x * x for x in daily_returns) / max(1, len(daily_returns)))
    volume_base = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / volume_base if volume_base > 0 else 1.0

    # A volatility-aware score: trend alignment, 24h/7d momentum and RSI.
    score = 0
    score += 2 if price > ema20 > ema50 else 1 if price > ema20 else -2 if price < ema20 < ema50 else -1
    score += 1 if momentum_24h > 0.012 else -1 if momentum_24h < -0.012 else 0
    score += 1 if momentum_7d > 0.04 else -1 if momentum_7d < -0.04 else 0
    score += 1 if 52 <= rsi <= 68 else -1 if rsi >= 76 or rsi <= 30 else 0
    score += 1 if volume_ratio >= 1.25 and momentum_24h > 0 else 0
    regime = "high volatility" if realized_vol >= 0.12 else "moderate volatility" if realized_vol >= 0.06 else "calm"
    if realized_vol >= 0.20:
        score = max(-1, min(1, score))
    if score >= 3:
        stance = "BULLISH SETUP"
    elif score <= -3:
        stance = "BEARISH / AVOID LONGS"
    else:
        stance = "NEUTRAL · WAIT"
    conviction = min(85, 45 + abs(score) * 8)
    # Risk levels are ATR-based reference levels, not guaranteed stops/targets.
    direction = 1 if score >= 3 else -1 if score <= -3 else 0
    invalidation = price - direction * 1.5 * atr if direction else None
    objective = price + direction * 2.5 * atr if direction else None
    return {
        "symbol": symbol, "price": price, "stance": stance, "score": score,
        "conviction": conviction, "rsi": rsi, "momentum_24h": momentum_24h,
        "momentum_7d": momentum_7d, "atr_pct": atr / price,
        "volatility": regime, "volume_ratio": volume_ratio,
        "invalidation": invalidation, "objective": objective,
        "timestamp": int(time.time()),
    }


async def _fetch_one(http: aiohttp.ClientSession, symbol: str) -> dict | None:
    try:
        async with http.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "4h", "limit": 120},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as response:
            response.raise_for_status()
            rows = await response.json()
        return _analyse_klines(symbol.removesuffix("USDT"), rows)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as exc:
        log.warning("Advisor history unavailable for %s: %s", symbol, type(exc).__name__)
        return None


async def build_advisory(http: aiohttp.ClientSession, redis: Redis) -> list[dict]:
    cached = await redis.get(CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except (ValueError, TypeError):
            pass
    rows = await asyncio.gather(*(_fetch_one(http, pair) for _, pair in UNIVERSE))
    results = [row for row in rows if row]
    if not results:
        raise AdvisorUnavailable("No reliable market history is available")
    results.sort(key=lambda row: (row["score"], row["conviction"]), reverse=True)
    await redis.set(CACHE_KEY, json.dumps(results, separators=(",", ":")), ex=CACHE_SECONDS)
    return results


def format_advisory(rows: list[dict], language: str = "en") -> str:
    arabic = language == "ar"
    title = "🧠 <b>ORBSAT · PREMIUM QUANT DESK</b>" if not arabic else "🧠 <b>ORBSAT · مكتب التحليل الكمي بريميوم</b>"
    lines = [title, "4H OHLCV · multi-horizon trend · RSI · ATR · volume · volatility regime", ""]
    for row in rows:
        stance = row["stance"]
        if arabic:
            stance = {"BULLISH SETUP": "إشارة صعود", "BEARISH / AVOID LONGS": "ضغط هبوطي / تجنب الشراء", "NEUTRAL · WAIT": "محايد · انتظار"}.get(stance, stance)
        line = (
            f"<b>{row['symbol']}</b> · {stance} · model score {row['score']:+d}/6\n"
            f"Price ${row['price']:,.6g} · RSI {row['rsi']:.0f} · 24h {row['momentum_24h']:+.1%} · 7d {row['momentum_7d']:+.1%}\n"
            f"Regime: {row['volatility']} · ATR {row['atr_pct']:.1%} · volume {row['volume_ratio']:.2f}×"
        )
        if row["invalidation"] is not None:
            line += f"\nReference invalidation ${row['invalidation']:,.6g} · 2.5 ATR objective ${row['objective']:,.6g}"
        lines.append(line)
    lines.extend([
        "",
        "Method: transparent, volatility-aware rule ensemble over 4h exchange candles. Scores are heuristic ranks, NOT probabilities.",
        "Risk controls: high-volatility regime suppresses conviction; neutral signals do not force a trade. Levels are research references, not execution instructions.",
        "Data source: Binance public market data · cached 5 min.",
        "<i>Research and educational market analysis only. Not individualized financial advice. No return is guaranteed; verify fees, slippage and liquidity.</i>",
    ])
    return "\n\n".join(lines)
