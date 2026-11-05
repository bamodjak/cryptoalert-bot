import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from app.handlers import cmd_movers, market_movers_text, router as user_router
from app.prices import PriceError, PriceService


class FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})

    async def get(self, key):
        return self.values.get(key)

    async def exists(self, key):
        return int(key in self.values)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        return True


def market_rows():
    return [
        {
            "id": f"coin-{index}",
            "symbol": f"c{index}",
            "name": f"Coin {index}",
            "current_price": 1.0 + index,
            "price_change_percentage_24h": float(index - 5),
            "market_cap": 100_000_000 + index,
        }
        for index in range(12)
    ] + [
        {
            "id": "tiny",
            "symbol": "tiny",
            "name": "Tiny Cap",
            "current_price": 1,
            "price_change_percentage_24h": 900,
            "market_cap": 1_000_000,
        },
        {
            "id": "bad",
            "symbol": "bad",
            "name": "Bad Data",
            "current_price": None,
            "price_change_percentage_24h": 800,
            "market_cap": 100_000_000,
        },
    ]


def test_market_movers_selects_top_five_gainers_and_losers_and_caches():
    async def scenario():
        redis = FakeRedis()
        service = PriceService(redis, http=None)
        service._get = AsyncMock(return_value=market_rows())

        result = await service.market_movers()

        assert len(result["gainers"]) == 5
        assert len(result["losers"]) == 5
        assert result["gainers"][0]["symbol"] == "C11"
        assert result["gainers"][0]["change_24h"] == 6
        assert result["losers"][0]["symbol"] == "C0"
        assert result["losers"][0]["change_24h"] == -5
        assert all(row["id"] != "tiny" for row in result["gainers"] + result["losers"])
        assert result["stale"] is False
        assert result["universe"] == "top_100_by_market_cap"
        assert "cg:movers" in redis.values
        assert "cg:movers:stale" in redis.values
        service._get.assert_awaited_once_with(
            "/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 100,
                "page": 1,
                "sparkline": "false",
                "price_change_percentage": "24h",
            },
        )

    asyncio.run(scenario())


def test_market_movers_uses_fresh_cache_without_provider_call():
    async def scenario():
        cached = {
            "gainers": [{"name": "A", "symbol": "A", "price_usd": 1, "change_24h": 2}],
            "losers": [{"name": "B", "symbol": "B", "price_usd": 2, "change_24h": -2}],
            "fetched_at": int(time.time()),
            "stale": False,
            "universe": "top_100_by_market_cap",
        }
        redis = FakeRedis({"cg:movers": json.dumps(cached)})
        service = PriceService(redis, http=None)
        service._get = AsyncMock()

        assert await service.market_movers() == cached
        service._get.assert_not_awaited()

    asyncio.run(scenario())


def test_market_movers_uses_stale_data_when_rate_limited():
    async def scenario():
        stale = {
            "gainers": [{"name": "A", "symbol": "A", "price_usd": 1, "change_24h": 2}],
            "losers": [{"name": "B", "symbol": "B", "price_usd": 2, "change_24h": -2}],
            "fetched_at": int(time.time()) - 600,
            "stale": False,
            "universe": "top_100_by_market_cap",
        }
        redis = FakeRedis({
            "cg:rate_limited": "1",
            "cg:movers:stale": json.dumps(stale),
        })
        service = PriceService(redis, http=None)
        service._get = AsyncMock()

        result = await service.market_movers()

        assert result["stale"] is True
        assert result["gainers"] == stale["gainers"]
        service._get.assert_not_awaited()

    asyncio.run(scenario())


def test_market_movers_fails_cleanly_without_fresh_or_stale_data():
    async def scenario():
        redis = FakeRedis({"cg:rate_limited": "1"})
        service = PriceService(redis, http=None)
        service._get = AsyncMock()

        with pytest.raises(PriceError):
            await service.market_movers()

    asyncio.run(scenario())


def test_market_movers_text_is_localized_and_escapes_provider_names():
    snapshot = {
        "gainers": [{
            "name": "<b>Injected</b>",
            "symbol": "UP",
            "price_usd": 2.5,
            "change_24h": 12.345,
        }],
        "losers": [{
            "name": "Down",
            "symbol": "DN",
            "price_usd": 0.5,
            "change_24h": -8.5,
        }],
        "fetched_at": int(time.time()) - 120,
        "stale": True,
    }

    english = market_movers_text(snapshot, "en")
    arabic = market_movers_text(snapshot, "ar")

    assert "24-hour market movers" in english
    assert "&lt;b&gt;Injected&lt;/b&gt;" in english
    assert "+12.35%" in english
    assert "cached data" in english
    assert "أكثر تحركات السوق" in arabic
    assert "بيانات مخزنة" in arabic


def test_market_movers_command_is_registered_on_the_user_router():
    assert any(handler.callback is cmd_movers for handler in user_router.message.handlers)


def test_market_movers_falls_back_to_coinpaprika_tickers():
    async def scenario():
        redis = FakeRedis({"cg:rate_limited": "1"})
        service = PriceService(redis, http=None)
        service._paprika_json = AsyncMock(return_value=[
            {
                "id": f"coin-{index}", "name": f"Coin {index}", "symbol": f"C{index}",
                "rank": index + 1,
                "quotes": {"USD": {
                    "price": 1.0 + index, "market_cap": 100_000_000 + index,
                    "percent_change_24h": float(index - 5),
                }},
            }
            for index in range(12)
        ])
        result = await service.market_movers()
        assert result["source"] == "coinpaprika"
        assert len(result["gainers"]) == 5
        assert len(result["losers"]) == 5
        assert result["gainers"][0]["symbol"] == "C11"
        assert result["losers"][0]["symbol"] == "C0"
        assert "cg:movers" in redis.values
        service._paprika_json.assert_awaited_once_with("/tickers", {"quotes": "USD", "limit": 100})
    asyncio.run(scenario())
