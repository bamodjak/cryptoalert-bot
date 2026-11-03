import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.admin_management import _edit_text_if_changed


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=None, message=message)


def test_edit_text_if_changed_ignores_identical_telegram_message():
    edit_text = AsyncMock(side_effect=_bad_request("message is not modified"))
    message = SimpleNamespace(edit_text=edit_text)

    asyncio.run(_edit_text_if_changed(message, "same content", None))

    edit_text.assert_awaited_once_with("same content", reply_markup=None)


def test_edit_text_if_changed_propagates_other_telegram_bad_requests():
    edit_text = AsyncMock(side_effect=_bad_request("message to edit not found"))
    message = SimpleNamespace(edit_text=edit_text)

    with pytest.raises(TelegramBadRequest, match="message to edit not found"):
        asyncio.run(_edit_text_if_changed(message, "new content", None))


def test_edit_text_if_changed_keeps_normal_edits_unchanged():
    edit_text = AsyncMock()
    message = SimpleNamespace(edit_text=edit_text)
    markup = object()

    asyncio.run(_edit_text_if_changed(message, "updated content", markup))

    edit_text.assert_awaited_once_with("updated content", reply_markup=markup)
