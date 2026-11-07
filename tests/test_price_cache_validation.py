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
