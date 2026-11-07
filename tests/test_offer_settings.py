import asyncio
import re
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


def test_admin_offer_value_is_saved_and_audited():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    import app.handlers as handlers

    class Redis:
        def __init__(self):
            self.values = {"admin:offer:input:77": "price"}
        async def get(self, key):
            return self.values.get(key)
        async def set(self, key, value):
            self.values[key] = str(value)
        async def delete(self, key):
            self.values.pop(key, None)
        async def mget(self, keys):
            return [self.values.get(key) for key in keys]

    async def scenario():
        redis = Redis()
        session = SimpleNamespace(add=MagicMock(), commit=AsyncMock())
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=77), text="2200", answer=AsyncMock(),
        )
        await handlers.save_offer_edit(message, redis, session)
        assert await handlers.get_offer(redis) == (2200, handlers.settings.premium_days)
        session.add.assert_called_once()
        assert session.add.call_args.args[0].action == "premium_offer_update"
        assert session.add.call_args.args[0].details == "price=2200"
        session.commit.assert_awaited_once()
        assert "2200 Stars" in message.answer.await_args.args[0]

    asyncio.run(scenario())


def test_admin_offer_input_filter_matches_numeric_messages():
    from app.handlers import OFFER_VALUE_PATTERN

    # This is the router's actual message filter: it must accept values such as
    # 150 and 100000, rather than requiring a literal backslash before 'd'.
    assert re.fullmatch(OFFER_VALUE_PATTERN, "150")
    assert re.fullmatch(OFFER_VALUE_PATTERN, "100000")
    assert not re.fullmatch(OFFER_VALUE_PATTERN, "150x")
    assert not re.fullmatch(OFFER_VALUE_PATTERN, "")


def test_premium_copy_renders_free_and_paid_allowances_without_missing_placeholders():
    from app.config import settings

    text = premium_copy("en", 1500, 30)
    assert f"{settings.free_alert_limit}" in text
    assert f"{settings.premium_alert_limit}" in text
    assert "Telegram Stars" in text
    assert "no automatic renewal" in text.lower()


def test_premium_limit_keyboard_has_direct_plan_and_home_actions():
    from app.alert_handlers import premium_limit_keyboard

    keyboard = premium_limit_keyboard("en")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["dash:premium", "nav:home"]
