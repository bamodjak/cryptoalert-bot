import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.middlewares import MessageTraceMiddleware, safe_message_action


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
    )

    async def handler(received_event, data):
        raise RuntimeError("database unavailable")

    async def run():
        return await MessageTraceMiddleware()(handler, event, {})

    with caplog.at_level(logging.INFO, logger="app.middlewares"):
        with pytest.raises(RuntimeError, match="database unavailable"):
            asyncio.run(run())

    assert "telegram_message failed command=/start chat_type=private" in caplog.text
    assert "987654321" not in caplog.text
