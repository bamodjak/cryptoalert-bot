import asyncio
from unittest.mock import AsyncMock

from app.prices import PriceService


class FakePipeline:
    def set(self, *args, **kwargs):
        return self

    async def execute(self):
        return []


class FakeRedis:
    def __init__(self):
        self.calls = 0

    async def mget(self, keys):
        self.calls += 1
        if self.calls == 1:
            return ["nan", "not-a-number", "-1"]
        return ["inf", "0", None]

    async def exists(self, key):
        return 1

    async def get(self, key):
        return None

    def pipeline(self):
        return FakePipeline()



def test_prices_ignores_corrupt_fresh_and_stale_redis_values():
    async def scenario():
        redis = FakeRedis()
        service = PriceService(redis, http=None)
        result = await service.prices(["btc", "eth", "sol"], allow_stale=True)
        assert result == {}
        assert redis.calls == 2

    asyncio.run(scenario())



def test_resolve_discards_malformed_cached_symbol_lookup_and_refetches():
    async def scenario():
        class Redis:
            def __init__(self):
                self.value = "{broken"
                self.deleted = []
            async def get(self, key):
                return self.value
            async def delete(self, key):
                self.deleted.append(key)
                self.value = None
            async def set(self, key, value, **kwargs):
                self.value = value

        redis = Redis()
        service = PriceService(redis, http=None)
        service._get = AsyncMock(return_value={"coins": [{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"}]})
        coin = await service.resolve("xbt")
        assert coin is None
        assert redis.deleted == ["cg:sym:xbt"]

    asyncio.run(scenario())


def test_gas_gwei_discards_invalid_cached_value_and_validates_rpc_quote(monkeypatch):
    async def scenario():
        class Redis:
            def __init__(self):
                self.value = "nan"
                self.deleted = []
                self.saved = None
            async def get(self, key):
                return self.value
            async def delete(self, key):
                self.deleted.append(key)
                self.value = None
            async def set(self, key, value, **kwargs):
                self.saved = value

        class Response:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None
            def raise_for_status(self):
                return None
            async def json(self):
                return {"result": "0x3b9aca00"}

        class Http:
            def post(self, *args, **kwargs):
                return Response()

        redis = Redis()
        service = PriceService(redis, http=Http())
        result = await service.gas_gwei()
        assert result == 1.0
        assert redis.deleted == ["gas:gwei"]
        assert redis.saved == 1.0

    asyncio.run(scenario())
