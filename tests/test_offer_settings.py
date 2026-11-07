import asyncio
from unittest.mock import AsyncMock

import pytest

from app.entitlements import premium_payment_matches_offer
from app.offer import get_offer, set_offer_value
from app.handlers import premium_member_status, premium_copy


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    async def set(self, key, value):
        self.values[key] = str(value)



def test_offer_defaults_and_admin_updates_are_validated():
    async def scenario():
        redis = FakeRedis()
        default_price, default_days = await get_offer(redis)
        assert default_price > 0 and default_days > 0
        assert await set_offer_value(redis, "price", 1800) == (1800, default_days)
        assert await set_offer_value(redis, "days", 45) == (1800, 45)
        with pytest.raises(ValueError):
            await set_offer_value(redis, "price", 0)
        with pytest.raises(ValueError):
            await set_offer_value(redis, "days", 4000)

    asyncio.run(scenario())


def test_invoice_keeps_original_quoted_price_and_duration_after_offer_change():
    assert premium_payment_matches_offer("premium:30:1500", "XTR", 1500, 45, 1800)
    assert not premium_payment_matches_offer("premium:30:1500", "XTR", 1800, 45, 1800)
    assert not premium_payment_matches_offer("premium:30:1500", "USD", 1500, 45, 1800)
    assert not premium_payment_matches_offer("premium:0:1500", "XTR", 1500, 45, 1800)


def test_premium_membership_copy_explains_paid_benefits():
    text = premium_member_status("en", "2026-12-01")
    assert "PREMIUM MEMBER" in text
    assert "Premium quantitative signals" in text
    assert "Live Market dashboard" in text
    assert "Ethereum gas alerts" in text
    arabic = premium_member_status("ar", "2026-12-01")
    assert "عضو بريميوم" in arabic


def test_premium_offer_copy_accepts_runtime_values():
    text = premium_copy("en", 1800, 45)
    assert "1800" in text and "45" in text
