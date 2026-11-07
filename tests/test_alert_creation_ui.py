import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.alert_handlers import AlertCustomInput, cb_create_alert
from app.prices import Coin


def test_custom_alert_input_has_inline_cancel_action():
    async def scenario():
        user = SimpleNamespace(id=42, language="en", username="tester", first_name="Tester", last_name=None)
        session = SimpleNamespace(get=AsyncMock(return_value=user), flush=AsyncMock())
        message = SimpleNamespace(message_id=77, edit_text=AsyncMock())
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=42, username="tester", first_name="Tester", last_name=None),
            message=message,
            data="create:custompct:btc:up",
            answer=AsyncMock(),
        )
        state = SimpleNamespace(
            clear=AsyncMock(),
            set_state=AsyncMock(),
            update_data=AsyncMock(),
        )
        redis = SimpleNamespace(set=AsyncMock(return_value=True))
        prices = SimpleNamespace(resolve=AsyncMock(return_value=Coin("bitcoin", "btc", "Bitcoin")))

        await cb_create_alert(callback, session, prices, state, redis)

        state.set_state.assert_awaited_once_with(AlertCustomInput.waiting)
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        assert callbacks == ["create:cancel"]

    asyncio.run(scenario())
