from app.admin_management import _access_button, _grant_user_button
from app.db import User


def test_user_list_has_direct_premium_grant_button():
    button = _grant_user_button(User(id=8231611086, first_name="Bachir"))

    assert button.text == "🎁 Grant Premium · Bachir"
    assert button.callback_data == "um:grantone:8231611086"


def test_premium_grant_button_label_is_compact():
    button = _grant_user_button(User(id=123456789, first_name="A" * 120))

    assert len(button.text) <= 60
    assert button.callback_data == "um:grantone:123456789"



def test_admin_user_search_matches_username_fragments_case_insensitively():
    from app.admin_management import _user_search_statement

    sql = str(_user_search_statement("@Bach").compile(compile_kwargs={"literal_binds": True}))

    assert "lower(users.username) LIKE lower('%Bach%')" in sql


def test_admin_user_search_uses_exact_numeric_telegram_id():
    from app.admin_management import _user_search_statement

    sql = str(_user_search_statement("8231611086").compile(compile_kwargs={"literal_binds": True}))

    assert "users.id = 8231611086" in sql
    assert "WHERE users.id = 8231611086" in sql
    assert "WHERE lower(users.username)" not in sql



def test_user_access_button_switches_between_suspend_and_restore():
    active = _access_button(User(id=101, suspended=False))
    suspended = _access_button(User(id=102, suspended=True))

    assert active.text == "🔒 Suspend access"
    assert active.callback_data == "um:access:101:suspend"
    assert suspended.text == "🔓 Restore access"
    assert suspended.callback_data == "um:access:102:restore"


def test_access_changes_require_a_separate_confirmation_callback():
    from app.admin_management import router

    callbacks = [handler.callback.__name__ for handler in router.callback_query.handlers]
    assert "request_access_change" in callbacks
    assert "confirm_access_change" in callbacks



def test_suspend_request_requires_confirmation_and_preserves_account():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.admin_management import request_access_change

    user = SimpleNamespace(id=101, suspended=False)
    confirmation_message = SimpleNamespace(answer=AsyncMock())
    callback = SimpleNamespace(
        data="um:access:101:suspend",
        from_user=SimpleNamespace(id=1),
        message=confirmation_message,
        answer=AsyncMock(),
    )
    session = SimpleNamespace(get=AsyncMock(return_value=user))

    asyncio.run(request_access_change(callback, session))

    confirmation_message.answer.assert_awaited_once()
    markup = confirmation_message.answer.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert any(button.callback_data == "um:accessconfirm:101:suspend" for button in buttons)
    assert user.suspended is False


def test_suspend_confirmation_updates_access_and_audits_action():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from app.admin_management import confirm_access_change

    user = SimpleNamespace(id=101, suspended=False)
    message = SimpleNamespace(edit_text=AsyncMock())
    callback = SimpleNamespace(
        data="um:accessconfirm:101:suspend",
        from_user=SimpleNamespace(id=1),
        message=message,
        answer=AsyncMock(),
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=user),
        add=Mock(),
        commit=AsyncMock(),
    )

    asyncio.run(confirm_access_change(callback, session))

    assert user.suspended is True
    session.add.assert_called_once()
    session.commit.assert_awaited_once()
    assert "suspended" in message.edit_text.await_args.args[0].lower()


def test_empty_admin_user_search_never_matches_everyone():
    from app.admin_management import _user_search_statement

    sql = str(_user_search_statement(" @ ").compile(compile_kwargs={"literal_binds": True}))
    assert "users.id = -1" in sql
    assert "LIKE lower('%%')" not in sql
