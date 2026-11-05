import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery
from app import handlers, main
from app.handlers import cmd_start, router as user_router
from app.i18n import welcome_text
from app.middlewares import CallbackTraceMiddleware, MessageTraceMiddleware, safe_message_action


def test_safe_message_action_logs_only_command_name():
    assert safe_message_action("/start@Orbsat16bot invite_SECRET") == "/start"
    assert safe_message_action("/price BTC") == "/price"
    assert safe_message_action("hello this is private") == "message"
    assert safe_message_action(None) == "message"


def test_message_trace_records_success_without_message_content_or_user_id(caplog):
    event = SimpleNamespace(
        text="/start private-referral-payload",
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=987654321),
    )

    async def handler(received_event, data):
        return "handled"

    async def run():
        return await MessageTraceMiddleware()(handler, event, {})

    with caplog.at_level(logging.INFO, logger="app.middlewares"):
        result = asyncio.run(run())

    assert result == "handled"
    assert "telegram_message received command=/start chat_type=private" in caplog.text
    assert "telegram_message completed command=/start chat_type=private" in caplog.text
    assert "private-referral-payload" not in caplog.text
    assert "987654321" not in caplog.text


def test_message_trace_records_handler_failure_and_reraises(caplog):
    event = SimpleNamespace(
        text="/start",
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=987654321),
        answer=AsyncMock(),
    )

    async def handler(received_event, data):
        raise RuntimeError("database unavailable")

    async def run():
        return await MessageTraceMiddleware()(handler, event, {})

    with caplog.at_level(logging.INFO, logger="app.middlewares"):
        with pytest.raises(RuntimeError, match="database unavailable"):
            asyncio.run(run())

    assert "telegram_message failed command=/start chat_type=private" in caplog.text
    event.answer.assert_awaited_once()
    assert "Please try /start again" in event.answer.await_args.args[0]
    assert "987654321" not in caplog.text


def test_start_command_handler_is_registered_on_the_user_router_used_by_main():
    assert main.router is user_router
    assert any(handler.callback is cmd_start for handler in user_router.message.handlers)



def test_start_handler_sends_welcome_and_home_keyboard(monkeypatch):
    user = SimpleNamespace(id=12345, language="en")
    get_user = AsyncMock(return_value=user)
    monkeypatch.setattr(handlers, "get_user", get_user)

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=12345),
        answer=AsyncMock(),
    )
    command = SimpleNamespace(args=None)

    asyncio.run(cmd_start(message, command, object()))

    get_user.assert_awaited_once()
    message.answer.assert_awaited_once()
    args, kwargs = message.answer.await_args
    assert args[0] == welcome_text("en")
    assert kwargs["reply_markup"] is not None



def test_expired_callback_gets_a_fresh_menu_hint(caplog):
    event = SimpleNamespace(
        id="expired-callback",
        data="dash:market",
        message=SimpleNamespace(answer=AsyncMock()),
    )
    error = TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id="expired-callback"),
        message="query is too old and response timeout expired",
    )

    async def handler(received_event, data):
        raise error

    async def run():
        return await CallbackTraceMiddleware()(handler, event, {})

    with caplog.at_level(logging.INFO, logger="app.middlewares"):
        result = asyncio.run(run())

    assert result is None
    assert "telegram_callback expired" in caplog.text
    event.message.answer.assert_awaited_once()
    assert "/start" in event.message.answer.await_args.args[0]
