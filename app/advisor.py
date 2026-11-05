"""Premium quantitative market advisor.

Uses exchange OHLCV history, multi-horizon trend/momentum, RSI, ATR and
realized volatility. This is a transparent rule ensemble, not a trained
predictive model and never places orders.
"""
import asyncio
import json
import logging
import math
import os
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
COINGECKO_IDS = {asset.symbol.upper(): asset.coin_id for asset in CORE_ASSETS}


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



def _score_signal(
    price: float, ema20: float, ema50: float, momentum_24h: float,
    momentum_7d: float, rsi: float, volume_ratio: float, realized_vol: float,
) -> tuple[int, list[str]]:
    """Return the transparent rule score and stable explanation keys."""
    score = 0
    reasons = []
    if price > ema20 > ema50:
        score += 2
        reasons.append("trend_bullish")
    elif price > ema20:
        score += 1
        reasons.append("trend_above_ema20")
    elif price < ema20 < ema50:
        score -= 2
        reasons.append("trend_bearish")
    else:
        score -= 1
        reasons.append("trend_below_ema20")
    if momentum_24h > 0.012:
        score += 1
        reasons.append("momentum_24h_positive")
    elif momentum_24h < -0.012:
        score -= 1
        reasons.append("momentum_24h_negative")
    else:
        reasons.append("momentum_24h_neutral")
    if momentum_7d > 0.04:
        score += 1
        reasons.append("momentum_7d_positive")
    elif momentum_7d < -0.04:
        score -= 1
        reasons.append("momentum_7d_negative")
    else:
        reasons.append("momentum_7d_neutral")
    if 52 <= rsi <= 68:
        score += 1
        reasons.append("rsi_supportive")
    elif rsi >= 76 or rsi <= 30:
        score -= 1
        reasons.append("rsi_extreme")
    else:
        reasons.append("rsi_neutral")
    if volume_ratio >= 1.25 and momentum_24h > 0:
        score += 1
        reasons.append("volume_confirmation")
    else:
        reasons.append("volume_no_confirmation")
    if realized_vol >= 0.20:
        score = max(-1, min(1, score))
        reasons.append("volatility_high")
    elif realized_vol >= 0.12:
        reasons.append("volatility_elevated")
    elif realized_vol >= 0.06:
        reasons.append("volatility_moderate")
    else:
        reasons.append("volatility_calm")
    return score, reasons


def _historical_evaluation(rows: list[list], horizon_candles: int = 6) -> dict | None:
    """Evaluate prior closed-candle signals against the next 24h; fees/slippage excluded."""
    closed_rows = rows[:-1]  # Exclude the currently forming Binance candle.
    candles = [
        (float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        for row in closed_rows
    ]
    closes = [candle[3] for candle in candles]
    volumes = [candle[4] for candle in candles]
    if len(closes) < 70:
        return None
    outcomes = []
    for index in range(60, len(closes) - horizon_candles):
        history = closes[:index + 1]
        price = closes[index]
        momentum_24h = price / closes[index - 6] - 1
        momentum_7d = price / closes[index - 42] - 1
        rsi = _rsi(history)
        ema20, ema50 = _ema(history, 20), _ema(history, 50)
        volume_base = sum(volumes[index - 20:index]) / 20
        volume_ratio = volumes[index] / volume_base if volume_base > 0 else 1.0
        daily_returns = [
            closes[j] / closes[j - 6] - 1
            for j in range(max(6, index - 42), index + 1)
        ]
        realized_vol = math.sqrt(sum(value * value for value in daily_returns) / max(1, len(daily_returns)))
        score, _ = _score_signal(
            price, ema20, ema50, momentum_24h, momentum_7d, rsi, volume_ratio, realized_vol,
        )
        direction = 1 if score >= 3 else -1 if score <= -3 else 0
        if not direction:
            continue
        forward_return = closes[index + horizon_candles] / price - 1
        outcomes.append(forward_return * direction)
    if not outcomes:
        return None
    return {
        "samples": len(outcomes),
        "win_rate": sum(value > 0 for value in outcomes) / len(outcomes),
        "average_return": sum(outcomes) / len(outcomes),
        "horizon_hours": horizon_candles * 4,
    }


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

    score, reasons = _score_signal(price, ema20, ema50, momentum_24h, momentum_7d, rsi, volume_ratio, realized_vol)
    regime = "high volatility" if realized_vol >= 0.12 else "moderate volatility" if realized_vol >= 0.06 else "calm"
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
        "reasons": reasons,
        "history": _historical_evaluation(rows),
        "timestamp": int(time.time()),
    }


async def _fetch_one(http: aiohttp.ClientSession, symbol: str) -> dict | None:
    """Fetch candles from Binance's market-data-only host, then regional REST mirrors.

    Render regions can receive HTTP 451/403 from the general Binance API. The
    documented data-api host serves the same public klines without authentication.
    """
    bases = (
        "https://data-api.binance.vision",
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api-gcp.binance.com",
    )
    last_error: Exception | None = None
    for base in bases:
        try:
            async with http.get(
                f"{base}/api/v3/klines",
                params={"symbol": symbol, "interval": "4h", "limit": 120},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as response:
                response.raise_for_status()
                rows = await response.json()
            return _analyse_klines(symbol.removesuffix("USDT"), rows)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as exc:
            last_error = exc
            status = getattr(exc, "status", None)
            log.warning(
                "Advisor candle provider failed symbol=%s host=%s status=%s error=%s",
                symbol, base.split("//", 1)[-1], status or "-", type(exc).__name__,
            )
    # Binance can return HTTP 418 from cloud-hosted regions. Fall back to
    # CoinGecko's public 30-day OHLC endpoint, which provides 4h candles on
    # the free/demo tier. OHLC has no volume field, so volume confirmation is
    # deliberately neutral rather than fabricated.
    asset_symbol = symbol.removesuffix("USDT").upper()
    coin_id = COINGECKO_IDS.get(asset_symbol)
    if coin_id:
        try:
            headers = {"accept": "application/json"}
            if os.getenv("COINGECKO_API_KEY"):
                headers["x-cg-demo-api-key"] = os.environ["COINGECKO_API_KEY"]
            async with http.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc",
                params={"vs_currency": "usd", "days": "30"},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                response.raise_for_status()
                candles = await response.json()
            # CoinGecko OHLC rows are [timestamp, open, high, low, close].
            rows = [list(row) + [0.0] for row in candles if isinstance(row, (list, tuple)) and len(row) >= 5]
            result = _analyse_klines(asset_symbol, rows)
            result["source"] = "coingecko"
            log.info("Advisor history loaded via CoinGecko fallback symbol=%s candles=%d", asset_symbol, len(rows))
            return result
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as exc:
            log.warning("Advisor CoinGecko OHLC fallback failed symbol=%s status=%s error=%s",
                        asset_symbol, getattr(exc, "status", "-"), type(exc).__name__)

    log.error("Advisor history unavailable for %s after Binance and CoinGecko attempts: %s",
              symbol, type(last_error).__name__ if last_error else "unknown")
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
    reason_labels = {
        "trend_bullish": ("Price above aligned EMA20/EMA50", "السعر أعلى من متوسطي EMA20 وEMA50 المتوافقين"),
        "trend_above_ema20": ("Price above EMA20", "السعر أعلى من EMA20"),
        "trend_bearish": ("Price below aligned EMA20/EMA50", "السعر أسفل من متوسطي EMA20 وEMA50 المتوافقين"),
        "trend_below_ema20": ("Price below EMA20", "السعر أسفل من EMA20"),
        "momentum_24h_positive": ("Positive 24h momentum", "زخم إيجابي خلال 24 ساعة"),
        "momentum_24h_negative": ("Negative 24h momentum", "زخم سلبي خلال 24 ساعة"),
        "momentum_24h_neutral": ("Flat 24h momentum", "زخم شبه مستقر خلال 24 ساعة"),
        "momentum_7d_positive": ("Positive 7d momentum", "زخم إيجابي خلال 7 أيام"),
        "momentum_7d_negative": ("Negative 7d momentum", "زخم سلبي خلال 7 أيام"),
        "momentum_7d_neutral": ("Flat 7d momentum", "زخم شبه مستقر خلال 7 أيام"),
        "rsi_supportive": ("RSI supports trend", "مؤشر RSI يدعم الاتجاه"),
        "rsi_extreme": ("RSI at an extreme", "مؤشر RSI عند مستوى متطرف"),
        "rsi_neutral": ("RSI is neutral", "مؤشر RSI محايد"),
        "volume_confirmation": ("Above-average volume confirms upside", "حجم تداول أعلى من المتوسط يدعم الصعود"),
        "volume_no_confirmation": ("No bullish volume confirmation", "لا يوجد تأكيد صعودي من حجم التداول"),
        "volatility_high": ("Very high volatility caps the score", "التقلب الشديد يحد من درجة الإشارة"),
        "volatility_elevated": ("Elevated volatility", "تقلب مرتفع"),
        "volatility_moderate": ("Moderate volatility", "تقلب متوسط"),
        "volatility_calm": ("Calm volatility", "تقلب منخفض"),
    }
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
        reasons = [reason_labels[key][1 if arabic else 0] for key in row.get("reasons", []) if key in reason_labels][:3]
        if reasons:
            line += ("\nالأسباب: " if arabic else "\nWhy: ") + "؛ ".join(reasons) if arabic else "\nWhy: " + "; ".join(reasons)
        history = row.get("history")
        if history:
            win_rate = history["win_rate"] * 100
            average_return = history["average_return"] * 100
            if arabic:
                line += f"\nتقييم تاريخي {history['horizon_hours']}س: {history['samples']} إشارة · نجاح {win_rate:.0f}% · متوسط العائد الاتجاهي {average_return:+.2f}%"
            else:
                line += f"\nHistorical {history['horizon_hours']}h: {history['samples']} signals · {win_rate:.0f}% wins · avg directional return {average_return:+.2f}%"
        elif arabic:
            line += "\nالتقييم التاريخي: عينة إشارات غير كافية"
        else:
            line += "\nHistorical evaluation: insufficient signal sample"
        lines.append(line)
    lines.extend([
        "",
        "Method: transparent, volatility-aware rule ensemble over 4h exchange candles. Scores are heuristic ranks, NOT probabilities.",
        "Historical evaluation is an in-sample directional check on prior closed candles; it is not a forecast and excludes fees, slippage and execution constraints.",
        "Risk controls: high-volatility regime suppresses conviction; neutral signals do not force a trade. Levels are research references, not execution instructions.",
        "Data source: Binance public market data; CoinGecko OHLC fallback where needed · cached 5 min.",
        "<i>Research and educational market analysis only. Not individualized financial advice. No return is guaranteed; verify fees, slippage and liquidity.</i>",
    ])
    return "\n\n".join(lines)
