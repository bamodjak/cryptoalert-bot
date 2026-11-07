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


def test_admin_panel_exposes_premium_offer_controls(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import app.handlers as handlers

    async def scenario():
        monkeypatch.setattr(handlers.settings, "admin_id_set", frozenset({1}), raising=False)
        monkeypatch.setattr(handlers, "get_offer", AsyncMock(return_value=(1800, 45)))
        redis = SimpleNamespace(set=AsyncMock())
        message = SimpleNamespace(edit_text=AsyncMock())
        callback = SimpleNamespace(
            data="admin:offer", from_user=SimpleNamespace(id=1),
            message=message, answer=AsyncMock(),
        )
        await handlers.cb_admin_panel(callback, None, redis)
        text = message.edit_text.await_args.args[0]
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        assert "1800 Stars" in text and "45 days" in text
        actions = [button.callback_data for row in markup.inline_keyboard for button in row]
        assert "admin:offer:set:price" in actions
        assert "admin:offer:set:days" in actions

    asyncio.run(scenario())


def test_admin_offer_price_action_starts_a_scoped_edit_prompt(monkeypatch):
    from types import SimpleNamespace
    import asyncio
    from unittest.mock import AsyncMock
    import app.handlers as handlers

    async def scenario():
        monkeypatch.setattr(handlers.settings, "admin_id_set", frozenset({77}), raising=False)
        redis = SimpleNamespace(set=AsyncMock())
        message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(
            data="admin:offer:set:price", from_user=SimpleNamespace(id=77),
            message=message, answer=AsyncMock(),
        )
        await handlers.cb_admin_panel(callback, None, redis)
        redis.set.assert_awaited_once_with("admin:offer:input:77", "price", ex=600)
        assert "Stars" in message.answer.await_args.args[0]

    asyncio.run(scenario())
