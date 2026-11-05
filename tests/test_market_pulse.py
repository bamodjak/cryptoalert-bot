import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from app.handlers import cmd_market, market_snapshot_text, router as user_router
from app.market_handlers import market_router
from app.parsing import fmt_compact_usd
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


def sample_global_response():
    return {
        "data": {
            "total_market_cap": {"usd": 2_500_000_000_000},
            "total_volume": {"usd": 84_000_000_000},
            "market_cap_change_percentage_24h_usd": 1.25,
            "market_cap_percentage": {"btc": 54.6},
            "active_cryptocurrencies": 18_500,
        }
    }


def test_market_overview_fetches_and_caches_global_metrics():
    async def scenario():
        redis = FakeRedis()
        service = PriceService(redis, http=None)
        service._get = AsyncMock(return_value=sample_global_response())

        result = await service.market_overview()

        assert result["market_cap_usd"] == 2_500_000_000_000
        assert result["volume_24h_usd"] == 84_000_000_000
        assert result["market_cap_change_24h"] == 1.25
        assert result["btc_dominance"] == 54.6
        assert result["active_cryptocurrencies"] == 18_500
        assert result["stale"] is False
        assert "cg:global" in redis.values
        assert "cg:global:stale" in redis.values
        service._get.assert_awaited_once_with("/global", {})

    asyncio.run(scenario())


def test_market_overview_uses_fresh_cache_without_provider_call():
    async def scenario():
        cached = {
            "market_cap_usd": 2_000_000_000_000,
            "volume_24h_usd": 50_000_000_000,
            "market_cap_change_24h": -0.5,
            "btc_dominance": 55.0,
            "active_cryptocurrencies": 18_000,
            "fetched_at": int(time.time()),
            "stale": False,
        }
        redis = FakeRedis({"cg:global": json.dumps(cached)})
        service = PriceService(redis, http=None)
        service._get = AsyncMock()

        result = await service.market_overview()

        assert result == cached
        service._get.assert_not_awaited()

    asyncio.run(scenario())


def test_market_overview_falls_back_to_stale_data_during_rate_limit():
    async def scenario():
        stale = {
            "market_cap_usd": 2_000_000_000_000,
            "volume_24h_usd": 50_000_000_000,
            "market_cap_change_24h": -0.5,
            "btc_dominance": 55.0,
            "active_cryptocurrencies": 18_000,
            "fetched_at": int(time.time()) - 600,
            "stale": False,
        }
        redis = FakeRedis({
            "cg:rate_limited": "1",
            "cg:global:stale": json.dumps(stale),
        })
        service = PriceService(redis, http=None)
        service._get = AsyncMock()

        result = await service.market_overview()

        assert result["stale"] is True
        assert result["market_cap_usd"] == stale["market_cap_usd"]
        service._get.assert_not_awaited()

    asyncio.run(scenario())


def test_market_overview_raises_when_provider_fails_without_stale_data():
    async def scenario():
        redis = FakeRedis({"cg:rate_limited": "1"})
        service = PriceService(redis, http=None)
        service._get = AsyncMock()

        with pytest.raises(PriceError):
            await service.market_overview()

    asyncio.run(scenario())


def test_compact_usd_format_preserves_large_market_scales():
    assert fmt_compact_usd(1_500_000_000_000) == "$1.50T"
    assert fmt_compact_usd(84_000_000_000) == "$84.00B"
    assert fmt_compact_usd(2_500_000) == "$2.50M"
    assert fmt_compact_usd(950) == "$950"


def test_market_snapshot_is_localized_and_marks_stale_data():
    snapshot = {
        "market_cap_usd": 2_500_000_000_000,
        "volume_24h_usd": 84_000_000_000,
        "market_cap_change_24h": 1.25,
        "btc_dominance": 54.6,
        "active_cryptocurrencies": 18_500,
        "fetched_at": int(time.time()) - 180,
        "stale": True,
    }

    english = market_snapshot_text(snapshot, "en")
    arabic = market_snapshot_text(snapshot, "ar")

    assert "Global market pulse" in english
    assert "$2.50T" in english
    assert "+1.25%" in english
    assert "cached data" in english
    assert "نبض السوق العالمي" in arabic
    assert "بيانات مخزنة" in arabic


def test_market_command_is_registered_on_the_market_feature_router():
    assert market_router in user_router.sub_routers
    assert any(handler.callback is cmd_market for handler in market_router.message.handlers)


def test_market_overview_falls_back_to_coinpaprika_when_coingecko_is_rate_limited():
    async def scenario():
        redis = FakeRedis({"cg:rate_limited": "1"})
        service = PriceService(redis, http=None)
        service._paprika_json = AsyncMock(return_value={
            "market_cap_usd": 2_800_000_000_000,
            "volume_24h_usd": 120_000_000_000,
            "market_cap_change_24h": 2.4,
            "bitcoin_dominance_percentage": 57.2,
            "cryptocurrencies_number": 12_000,
            "last_updated": 1_793_858_724,
        })
        result = await service.market_overview()
        assert result["source"] == "coinpaprika"
        assert result["market_cap_usd"] == 2_800_000_000_000
        assert result["btc_dominance"] == 57.2
        assert result["active_cryptocurrencies"] == 12_000
        assert "cg:global" in redis.values
        service._paprika_json.assert_awaited_once_with("/global")
    asyncio.run(scenario())



def test_market_movers_coinpaprika_fallback_uses_supported_query_and_normalizes_rows():
    async def scenario():
        rows = []
        for rank in range(1, 11):
            rows.append({
                "id": f"coin-{rank}",
                "rank": rank,
                "symbol": f"C{rank}",
                "name": f"Coin {rank}",
                "quotes": {"USD": {
                    "price": 10.0 + rank,
                    "market_cap": 50_000_000 + rank,
                    "percent_change_24h": 12.0 - rank * 2,
                }},
            })
        redis = FakeRedis({"cg:rate_limited": "1"})
        service = PriceService(redis, http=None)
        service._paprika_json = AsyncMock(return_value=rows)

        result = await service.market_movers()

        assert result["source"] == "coinpaprika"
        assert len(result["gainers"]) == 5
        assert len(result["losers"]) == 5
        service._paprika_json.assert_awaited_once_with("/tickers", {"quotes": "USD"})

    asyncio.run(scenario())


def test_market_movers_rejects_empty_cached_payload_and_refetches():
    async def scenario():
        empty_cache = {
            "gainers": [], "losers": [], "fetched_at": int(time.time()),
        }
        rows = []
        for rank in range(1, 11):
            rows.append({
                "id": f"coin-{rank}", "rank": rank,
                "symbol": f"C{rank}", "name": f"Coin {rank}",
                "quotes": {"USD": {
                    "price": 10.0 + rank, "market_cap": 50_000_000,
                    "percent_change_24h": 12.0 - rank * 2,
                }},
            })
        redis = FakeRedis({
            "cg:movers": json.dumps(empty_cache),
            "cg:rate_limited": "1",
        })
        service = PriceService(redis, http=None)
        service._paprika_json = AsyncMock(return_value=rows)

        result = await service.market_movers()

        assert len(result["gainers"]) == 5
        assert len(result["losers"]) == 5
        service._paprika_json.assert_awaited_once_with("/tickers", {"quotes": "USD"})

    asyncio.run(scenario())



def test_market_overview_discards_incomplete_cache_and_uses_provider_fallback():
    async def scenario():
        redis = FakeRedis({
            "cg:global": json.dumps({"market_cap_usd": 1}),
            "cg:rate_limited": "1",
        })
        service = PriceService(redis, http=None)
        service._paprika_json = AsyncMock(return_value={
            "market_cap_usd": 2_800_000_000_000,
            "volume_24h_usd": 120_000_000_000,
            "market_cap_change_24h": 2.4,
            "bitcoin_dominance_percentage": 57.2,
            "cryptocurrencies_number": 12_000,
            "last_updated": 1_793_858_724,
        })

        result = await service.market_overview()

        assert result["market_cap_usd"] == 2_800_000_000_000
        assert result["source"] == "coinpaprika"
        service._paprika_json.assert_awaited_once_with("/global")

    asyncio.run(scenario())
