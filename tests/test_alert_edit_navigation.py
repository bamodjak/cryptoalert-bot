import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import app.alert_handlers as handlers


def test_alert_edit_prompt_has_inline_cancel(monkeypatch):
    async def scenario():
        user = SimpleNamespace(id=42, language="en")
        alert = SimpleNamespace(id=7, symbol="BTC")
        monkeypatch.setattr(handlers, "get_user", AsyncMock(return_value=user))
        state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())
        message = SimpleNamespace(answer=AsyncMock())
        callback = SimpleNamespace(
            data="alertedit:7", from_user=SimpleNamespace(id=42),
            message=message, answer=AsyncMock(),
        )
        session = SimpleNamespace(scalar=AsyncMock(return_value=alert))

        await handlers.cb_edit_alert(callback, session, state)

        markup = message.answer.await_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == "alertedit:cancel"
        state.set_state.assert_awaited_once_with(handlers.AlertEditInput.waiting)
        state.update_data.assert_awaited_once_with(alert_id=7)

    asyncio.run(scenario())


def test_alert_edit_cancel_clears_state_and_returns_navigation(monkeypatch):
    async def scenario():
        user = SimpleNamespace(id=42, language="en")
        monkeypatch.setattr(handlers, "get_user", AsyncMock(return_value=user))
        state = SimpleNamespace(clear=AsyncMock())
        message = SimpleNamespace(edit_text=AsyncMock())
        callback = SimpleNamespace(
            data="alertedit:cancel", from_user=SimpleNamespace(id=42),
            message=message, answer=AsyncMock(),
        )

        await handlers.cb_edit_alert(callback, SimpleNamespace(), state)

        state.clear.assert_awaited_once()
        message.edit_text.assert_awaited_once()
        assert message.edit_text.await_args.kwargs["reply_markup"] is not None
        callback.answer.assert_awaited_once()

    asyncio.run(scenario())
