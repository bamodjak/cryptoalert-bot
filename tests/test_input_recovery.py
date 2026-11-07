import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock


def test_custom_watchlist_input_can_be_cancelled():
    from app.watchlists import cancel_custom_asset_input

    async def scenario():
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(answer=AsyncMock())
        await cancel_custom_asset_input(message, state)
        state.clear.assert_awaited_once()
        assert "cancelled" in message.answer.await_args.args[0].lower()

    asyncio.run(scenario())


def test_admin_offer_edit_invalid_input_has_recovery_and_cancel():
    from app.handlers import cancel_offer_edit, handle_invalid_offer_edit

    class Redis:
        def __init__(self):
            self.values = {"admin:offer:input:77": "price"}
        async def get(self, key):
            return self.values.get(key)
        async def delete(self, key):
            self.values.pop(key, None)

    async def scenario():
        redis = Redis()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=77), text="one-fifty",
            answer=AsyncMock(),
        )
        await handle_invalid_offer_edit(message, redis)
        assert "1–100000 Stars" in message.answer.await_args.args[0]
        assert await redis.get("admin:offer:input:77") == "price"

        await cancel_offer_edit(message, redis)
        assert await redis.get("admin:offer:input:77") is None
        assert "cancelled" in message.answer.await_args.args[0].lower()

    asyncio.run(scenario())
