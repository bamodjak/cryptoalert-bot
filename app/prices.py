import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass

import aiohttp
from redis.asyncio import Redis

from . import metrics
from .config import settings
from .assets import CORE_ASSETS

log = logging.getLogger(__name__)


# Compatibility mappings derived from the canonical asset registry.
PAPRIKA_IDS = {asset.coin_id: asset.paprika_id for asset in CORE_ASSETS}
KNOWN = {
    asset.symbol: (asset.coin_id, asset.name) for asset in CORE_ASSETS
}

class PriceError(Exception):
    pass


def _valid_global_snapshot(payload: object) -> bool:
    """Reject incomplete or non-finite market snapshots before presenting cached data."""
    if not isinstance(payload, dict):
        return False
    try:
        market_cap = float(payload["market_cap_usd"])
        volume = float(payload["volume_24h_usd"])
        change = float(payload["market_cap_change_24h"])
        dominance = float(payload["btc_dominance"])
        active = int(payload["active_cryptocurrencies"])
        fetched_at = int(payload["fetched_at"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return (
        all(math.isfinite(value) for value in (market_cap, volume, change, dominance))
        and market_cap > 0 and volume >= 0
        and 0 <= dominance <= 100 and active >= 0 and fetched_at > 0
    )


@dataclass(frozen=True)
class Coin:
    id: str
    symbol: str
    name: str


class PriceService:
    def __init__(self, redis: Redis, http: aiohttp.ClientSession):
        self.redis = redis
        self.http = http
        self.base = "https://api.coingecko.com/api/v3"
        self.headers = {"accept": "application/json"}
        if settings.coingecko_api_key:
            self.headers["x-cg-demo-api-key"] = settings.coingecko_api_key

    async def _get(self, path: str, params: dict) -> dict:
        last: Exception | None = None
        for attempt in range(2):
            try:
                async with self.http.get(self.base + path, params=params, headers=self.headers) as r:
                    if r.status == 429:
                        # Open a short circuit and fail over immediately; long sleeps
                        # here made a button tap wait up to 35 seconds.
                        await self.redis.set("cg:rate_limited", "1", ex=30)
                        metrics.PRICE_ERRORS.inc()
                        raise PriceError(f"CoinGecko rate-limited {path}")
                    r.raise_for_status()
                    return await r.json()
            except PriceError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = e
                if attempt == 0:
                    await asyncio.sleep(0.5)
        metrics.PRICE_ERRORS.inc()
        raise PriceError(f"CoinGecko {path} failed: {last}")

    async def resolve(self, symbol: str) -> Coin | None:
        s = symbol.lower().strip().lstrip("$")
        if not re.fullmatch(r"[a-z0-9\-]{1,30}", s):
            return None
        if s in KNOWN:
            cid, name = KNOWN[s]
            return Coin(cid, s, name)
        key = f"cg:sym:{s}"
        cached = await self.redis.get(key)
        if cached is not None:
            if cached == "":
                return None
            cid, name = json.loads(cached)
            return Coin(cid, s, name)
        data = await self._get("/search", {"query": s})
        for c in data.get("coins", []):  # already sorted by market-cap rank
            if c.get("symbol", "").lower() == s:
                await self.redis.set(key, json.dumps([c["id"], c["name"]]), ex=86400)
                return Coin(c["id"], s, c["name"])
        await self.redis.set(key, "", ex=3600)
        return None


    async def _paprika_price(self, coin_id: str) -> float | None:
        """Fetch one supported coin from CoinPaprika as a rate-limit fallback."""
        ticker_id = PAPRIKA_IDS.get(coin_id)
        if not ticker_id:
            return None
        try:
            async with self.http.get(
                f"https://api.coinpaprika.com/v1/tickers/{ticker_id}",
                params={"quotes": "USD"},
                headers={"accept": "application/json"},
            ) as response:
                response.raise_for_status()
                payload = await response.json()
            value = payload.get("quotes", {}).get("USD", {}).get("price")
            return float(value) if value is not None else None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            log.warning("CoinPaprika fallback failed for %s: %s", coin_id, type(exc).__name__)
            return None

    async def prices(self, ids: list[str], *, allow_stale: bool = False) -> dict[str, float]:
        ids = sorted(set(ids))
        if not ids:
            return {}
        cached = await self.redis.mget([f"cg:px:{i}" for i in ids])
        out = {i: float(v) for i, v in zip(ids, cached) if v is not None}
        missing = [i for i in ids if i not in out]
        if not missing:
            return out

        # Keep a separate stale copy for brief provider outages. It is only used
        # after fresh-cache misses and is refreshed whenever a new quote arrives.
        stale = {}
        if allow_stale:
            stale_values = await self.redis.mget([f"cg:px:stale:{i}" for i in missing])
            stale = {i: float(v) for i, v in zip(missing, stale_values) if v is not None}
        try:
            rate_limited = bool(await self.redis.exists("cg:rate_limited"))
        except Exception:
            rate_limited = False

        data = {}
        if not rate_limited:
            for n in range(0, len(missing), 100):
                chunk = missing[n:n + 100]
                try:
                    data.update(await self._get(
                        "/simple/price",
                        {"ids": ",".join(chunk), "vs_currencies": "usd"},
                    ))
                except PriceError:
                    # A failed CoinGecko request should not block fallback quotes.
                    break

        unresolved = [cid for cid in missing if cid not in data or "usd" not in data.get(cid, {})]
        if unresolved:
            # One cached bulk request avoids a burst of per-asset calls against
            # CoinPaprika's shared public rate limit.
            rows = await self._paprika_tickers()
            ticker_by_id = {
                str(row.get("id")): row
                for row in rows or []
                if isinstance(row, dict) and row.get("id")
            }
            for cid in unresolved:
                ticker = ticker_by_id.get(PAPRIKA_IDS.get(cid, ""))
                value = (
                    ticker.get("quotes", {}).get("USD", {}).get("price")
                    if ticker else None
                )
                try:
                    price = float(value)
                    if math.isfinite(price) and price > 0:
                        data[cid] = {"usd": price}
                        continue
                except (TypeError, ValueError):
                    pass
                if allow_stale and cid in stale:
                    data[cid] = {"usd": stale[cid]}

        pipe = self.redis.pipeline()
        for cid, value in data.items():
            if "usd" not in value:
                continue
            price = float(value["usd"])
            if not math.isfinite(price) or price <= 0:
                continue
            out[cid] = price
            pipe.set(f"cg:px:{cid}", price, ex=max(10, settings.check_interval - 5))
            pipe.set(f"cg:px:stale:{cid}", price, ex=600)
        await pipe.execute()
        # If a fresh quote was unavailable, use the stale quote as a last resort.
        if allow_stale:
            for cid in missing:
                if cid not in out and cid in stale:
                    out[cid] = stale[cid]
        return out


    async def _paprika_json(self, path: str, params: dict | None = None):
        """Fetch public fallback market data from CoinPaprika (no API key required)."""
        if self.http is None:
            return None
        try:
            async with self.http.get(
                "https://api.coinpaprika.com/v1" + path,
                params=params or {},
                headers={"accept": "application/json"},
            ) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            log.warning("CoinPaprika market fallback failed path=%s status=%s error=%s", path, getattr(exc, "status", "-"), type(exc).__name__)
            return None

    async def _paprika_tickers(self) -> list[dict] | None:
        """Return a shared, short-lived CoinPaprika ticker snapshot."""
        cache_key = "paprika:tickers:usd"
        cached = await self.redis.get(cache_key)
        if cached is not None:
            try:
                rows = json.loads(cached)
                if isinstance(rows, list):
                    return rows
                log.warning("discarding malformed cached CoinPaprika tickers")
            except (TypeError, ValueError):
                log.warning("discarding malformed cached CoinPaprika tickers")

        rows = await self._paprika_json("/tickers", {"quotes": "USD"})
        if isinstance(rows, list):
            await self.redis.set(cache_key, json.dumps(rows, separators=(",", ":")), ex=60)
            return rows
        return None

    async def market_overview(self) -> dict:
        """Return global metrics from CoinGecko, falling back to CoinPaprika and bounded stale data."""
        cache_key, stale_key = "cg:global", "cg:global:stale"
        cached = await self.redis.get(cache_key)
        if cached is not None:
            try:
                candidate = json.loads(cached)
                if _valid_global_snapshot(candidate):
                    return candidate
                log.warning("discarding incomplete cached global market data")
            except (TypeError, ValueError):
                log.warning("discarding malformed cached global market data")

        stale_payload = None
        stale = await self.redis.get(stale_key)
        if stale is not None:
            try:
                candidate = json.loads(stale)
                if _valid_global_snapshot(candidate):
                    stale_payload = candidate
                else:
                    log.warning("discarding incomplete stale global market data")
            except (TypeError, ValueError):
                log.warning("discarding malformed stale global market data")

        payload = None
        try:
            if await self.redis.exists("cg:rate_limited"):
                raise PriceError("CoinGecko is temporarily rate-limited")
            response = await self._get("/global", {})
            data = response.get("data") or {}
            payload = {
                "market_cap_usd": float(data["total_market_cap"]["usd"]),
                "volume_24h_usd": float(data["total_volume"]["usd"]),
                "market_cap_change_24h": float(data["market_cap_change_percentage_24h_usd"]),
                "btc_dominance": float(data["market_cap_percentage"]["btc"]),
                "active_cryptocurrencies": int(data["active_cryptocurrencies"]),
                "fetched_at": int(data.get("updated_at") or time.time()),
                "stale": False,
                "source": "coingecko",
            }
        except (PriceError, KeyError, TypeError, ValueError) as exc:
            log.warning("CoinGecko global market data unavailable; trying CoinPaprika (%s)", type(exc).__name__)
            data = await self._paprika_json("/global")
            if isinstance(data, dict):
                try:
                    payload = {
                        "market_cap_usd": float(data.get("market_cap_usd", data.get("market_cap"))),
                        "volume_24h_usd": float(data.get("volume_24h_usd", data.get("volume_24h"))),
                        "market_cap_change_24h": float(data.get("market_cap_change_24h", data.get("market_cap_change_24h_percentage", 0))),
                        "btc_dominance": float(data.get("bitcoin_dominance_percentage", data.get("btc_dominance_percentage", data.get("bitcoin_dominance", 0)))),
                        "active_cryptocurrencies": int(data.get("cryptocurrencies_number", data.get("active_cryptocurrencies", 0))),
                        "fetched_at": int(data.get("last_updated", time.time())),
                        "stale": False,
                        "source": "coinpaprika",
                    }
                    if payload["market_cap_usd"] <= 0 or payload["volume_24h_usd"] < 0:
                        raise ValueError("invalid CoinPaprika global metrics")
                except (TypeError, ValueError):
                    payload = None

        if payload is None:
            if stale_payload is not None:
                stale_payload["stale"] = True
                return stale_payload
            raise PriceError("Global market data is unavailable")

        encoded = json.dumps(payload, separators=(",", ":"))
        await self.redis.set(cache_key, encoded, ex=120 if payload["source"] == "coingecko" else 300)
        await self.redis.set(stale_key, encoded, ex=3600)
        return payload

    async def market_movers(self) -> dict:
        """Return 24h movers from the top 100 assets, with independent provider fallback."""
        cache_key, stale_key = "cg:movers", "cg:movers:stale"
        cached = await self.redis.get(cache_key)
        if cached is not None:
            try:
                payload = json.loads(cached)
                if (
                    isinstance(payload, dict)
                    and isinstance(payload.get("gainers"), list) and len(payload["gainers"]) >= 5
                    and isinstance(payload.get("losers"), list) and len(payload["losers"]) >= 5
                    and isinstance(payload.get("fetched_at"), int)
                ):
                    return payload
                raise ValueError("invalid cached market movers structure")
            except (TypeError, ValueError):
                log.warning("discarding malformed cached market movers")

        stale_payload = None
        stale = await self.redis.get(stale_key)
        if stale is not None:
            try:
                candidate = json.loads(stale)
                if (
                    isinstance(candidate, dict)
                    and isinstance(candidate.get("gainers"), list) and len(candidate["gainers"]) >= 5
                    and isinstance(candidate.get("losers"), list) and len(candidate["losers"]) >= 5
                    and isinstance(candidate.get("fetched_at"), int)
                ):
                    stale_payload = candidate
                else:
                    raise ValueError("invalid stale market movers structure")
            except (TypeError, ValueError):
                log.warning("discarding malformed stale market movers")

        rows = None
        source = "coingecko"
        try:
            if await self.redis.exists("cg:rate_limited"):
                raise PriceError("CoinGecko is temporarily rate-limited")
            rows = await self._get(
                "/coins/markets",
                {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 100, "page": 1, "sparkline": "false", "price_change_percentage": "24h"},
            )
        except PriceError as exc:
            log.warning("CoinGecko movers unavailable; trying CoinPaprika (%s)", type(exc).__name__)
            source = "coinpaprika"
            rows = await self._paprika_tickers()

        candidates = []
        if isinstance(rows, list):
            for row in rows:
                try:
                    if source == "coinpaprika":
                        quote = row.get("quotes", {}).get("USD", {})
                        change = float(quote["percent_change_24h"])
                        price = float(quote["price"])
                        market_cap = float(quote["market_cap"])
                        rank = int(row.get("rank") or 0)
                    else:
                        change = float(row["price_change_percentage_24h"])
                        price = float(row["current_price"])
                        market_cap = float(row["market_cap"])
                        rank = len(candidates) + 1
                    symbol = str(row["symbol"]).strip().upper()
                    name = str(row["name"]).strip()
                    if not symbol or not name or not all(math.isfinite(v) for v in (price, market_cap, change)) or price <= 0 or market_cap < 10_000_000 or not (-100 <= change <= 10000):
                        continue
                    if source == "coinpaprika" and rank > 100:
                        continue
                    candidates.append({"id": str(row.get("id", "")), "symbol": symbol[:12], "name": name[:48], "price_usd": price, "change_24h": change, "market_cap_usd": market_cap})
                except (KeyError, TypeError, ValueError):
                    continue

        if len(candidates) < 5:
            if stale_payload is not None:
                stale_payload["stale"] = True
                return stale_payload
            raise PriceError("Market movers are temporarily unavailable")

        payload = {
            "gainers": sorted(candidates, key=lambda item: item["change_24h"], reverse=True)[:5],
            "losers": sorted(candidates, key=lambda item: item["change_24h"])[:5],
            "fetched_at": int(time.time()),
            "stale": False,
            "source": source,
            "universe": "top_100_by_market_cap",
        }
        encoded = json.dumps(payload, separators=(",", ":"))
        await self.redis.set(cache_key, encoded, ex=180 if source == "coingecko" else 300)
        await self.redis.set(stale_key, encoded, ex=1800)
        return payload


    async def top_coins(self) -> dict:
        """Return the current top 33 crypto assets with short-lived and stale caches."""
        cache_key, stale_key = "cg:top33", "cg:top33:stale"
        cached = await self.redis.get(cache_key)
        if cached is not None:
            try:
                payload = json.loads(cached)
                if isinstance(payload, dict) and len(payload.get("coins", [])) == 33:
                    return payload
            except (TypeError, ValueError):
                log.warning("discarding malformed cached top-33 prices")

        stale_payload = None
        stale = await self.redis.get(stale_key)
        if stale is not None:
            try:
                candidate = json.loads(stale)
                if isinstance(candidate, dict) and candidate.get("coins"):
                    stale_payload = candidate
            except (TypeError, ValueError):
                log.warning("discarding malformed stale top-33 prices")

        rows = None
        source = "coingecko"
        try:
            if await self.redis.exists("cg:rate_limited"):
                raise PriceError("CoinGecko is temporarily rate-limited")
            rows = await self._get(
                "/coins/markets",
                {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 33,
                 "page": 1, "sparkline": "false", "price_change_percentage": "24h"},
            )
        except PriceError as exc:
            log.warning("CoinGecko top-33 prices unavailable; trying CoinPaprika (%s)", type(exc).__name__)
            source = "coinpaprika"
            rows = await self._paprika_tickers()

        coins = []
        if isinstance(rows, list):
            for row in rows:
                try:
                    if source == "coinpaprika":
                        quote = row.get("quotes", {}).get("USD", {})
                        rank = int(row.get("rank") or 0)
                        price = float(quote["price"])
                        change = float(quote.get("percent_change_24h") or 0)
                        market_cap = float(quote.get("market_cap") or 0)
                        volume_24h = float(quote.get("volume_24h") or 0)
                        coin_id = str(row["id"])
                    else:
                        rank = int(row.get("market_cap_rank") or (len(coins) + 1))
                        price = float(row["current_price"])
                        change = float(row.get("price_change_percentage_24h") or 0)
                        market_cap = float(row.get("market_cap") or 0)
                        volume_24h = float(row.get("total_volume") or 0)
                        coin_id = str(row["id"])
                    symbol = str(row["symbol"]).strip().upper()
                    name = str(row["name"]).strip()
                    if source == "coinpaprika" and rank > 33:
                        continue
                    if not symbol or not name or not math.isfinite(price) or price <= 0 or not math.isfinite(change):
                        continue
                    coins.append({
                        "rank": rank, "id": coin_id, "symbol": symbol[:12],
                        "name": name[:48], "price_usd": price, "change_24h": change,
                        "market_cap_usd": market_cap if market_cap > 0 else None,
                        "volume_24h_usd": volume_24h if volume_24h > 0 else None,
                    })
                except (KeyError, TypeError, ValueError):
                    continue
        coins.sort(key=lambda item: item["rank"])
        coins = coins[:33]
        if len(coins) < 33:
            if stale_payload is not None:
                stale_payload["stale"] = True
                return stale_payload
            raise PriceError("Top 33 crypto prices are temporarily unavailable")

        payload = {"coins": coins, "fetched_at": int(time.time()), "stale": False, "source": source}
        encoded = json.dumps(payload, separators=(",", ":"))
        await self.redis.set(cache_key, encoded, ex=120 if source == "coingecko" else 300)
        await self.redis.set(stale_key, encoded, ex=1800)
        return payload

    async def gas_gwei(self) -> float:
        cached = await self.redis.get("gas:gwei")
        if cached is not None:
            return float(cached)
        payload = {"jsonrpc": "2.0", "method": "eth_gasPrice", "params": [], "id": 1}
        try:
            async with self.http.post(settings.eth_rpc_url, json=payload) as r:
                r.raise_for_status()
                data = await r.json()
            gwei = int(data["result"], 16) / 1e9
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, ValueError) as e:
            metrics.PRICE_ERRORS.inc()
            raise PriceError(f"gas RPC failed: {e}") from e
        await self.redis.set("gas:gwei", gwei, ex=25)
        return gwei
