import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler

from app.handlers import save_offer_edit


def test_admin_text_handler_does_not_consume_unrelated_numeric_input():
    async def run():
        redis = AsyncMock()
        redis.get.return_value = None
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123456),
            text="75700",
        )

        with pytest.raises(SkipHandler):
            await save_offer_edit(message, redis, session=None)

        redis.get.assert_awaited_once_with("admin:offer:input:123456")

    asyncio.run(run())
