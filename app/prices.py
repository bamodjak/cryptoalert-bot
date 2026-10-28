import asyncio
import json
import logging
import re
from dataclasses import dataclass

import aiohttp
from redis.asyncio import Redis

from . import metrics
from .config import settings

log = logging.getLogger(__name__)

KNOWN = {
    "btc": ("bitcoin", "Bitcoin"), "eth": ("ethereum", "Ethereum"), "sol": ("solana", "Solana"),
    "bnb": ("binancecoin", "BNB"), "xrp": ("ripple", "XRP"), "ada": ("cardano", "Cardano"),
    "doge": ("dogecoin", "Dogecoin"), "ton": ("the-open-network", "Toncoin"), "trx": ("tron", "TRON"),
    "avax": ("avalanche-2", "Avalanche"), "dot": ("polkadot", "Polkadot"), "link": ("chainlink", "Chainlink"),
    "ltc": ("litecoin", "Litecoin"), "usdt": ("tether", "Tether"), "usdc": ("usd-coin", "USDC"),
}


class PriceError(Exception):
    pass


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
        for attempt in range(3):
            try:
                async with self.http.get(self.base + path, params=params, headers=self.headers) as r:
                    if r.status == 429:
                        await asyncio.sleep(5 * 2**attempt)
                        continue
                    r.raise_for_status()
                    return await r.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = e
                await asyncio.sleep(2**attempt)
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

    async def prices(self, ids: list[str]) -> dict[str, float]:
        ids = sorted(set(ids))
        if not ids:
            return {}
        cached = await self.redis.mget([f"cg:px:{i}" for i in ids])
        out = {i: float(v) for i, v in zip(ids, cached) if v is not None}
        missing = [i for i in ids if i not in out]
        for n in range(0, len(missing), 100):
            chunk = missing[n:n + 100]
            data = await self._get("/simple/price", {"ids": ",".join(chunk), "vs_currencies": "usd"})
            pipe = self.redis.pipeline()
            for cid, v in data.items():
                if "usd" in v:
                    out[cid] = float(v["usd"])
                    pipe.set(f"cg:px:{cid}", v["usd"], ex=max(10, settings.check_interval - 5))
            await pipe.execute()
        return out

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
