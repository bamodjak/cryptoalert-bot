from app.handlers import nav_keyboard, persistent_menu_keyboard


def test_persistent_menu_is_persistent_and_uses_plain_language_actions():
    keyboard = persistent_menu_keyboard()
    assert keyboard.is_persistent is True
    assert keyboard.resize_keyboard is True
    assert keyboard.input_field_placeholder == "Orbsat · Dashboard, markets and alerts"
    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        ["🏠 Dashboard", "📊 Markets"],
        ["🔔 Alerts", "⭐ Premium"],
    ]
    assert [[button.style for button in row] for row in keyboard.keyboard] == [
        ["primary", "primary"],
        [None, "success"],
    ]


def test_inline_navigation_is_compact_and_keeps_admin_private():
    public = nav_keyboard(user_id=-1, show_back=False)
    labels = [button.text for row in public.inline_keyboard for button in row]
    callbacks = {button.callback_data for row in public.inline_keyboard for button in row}
    assert {"create:open", "dash:market", "dash:movers", "live:open", "prices:open", "wl:open", "advisor:open", "dash:premium", "nav:share", "nav:help", "lang:choose", "settings:open"} <= callbacks
    assert "🛡 Admin Panel" not in labels
    assert all("Admin" not in label for label in labels)
    assert [len(row) for row in public.inline_keyboard] == [1, 2, 2, 2, 2, 2, 2, 1]
    assert public.inline_keyboard[0][0].callback_data == "create:open"

    owner = nav_keyboard(user_id=next(iter(__import__("app.config", fromlist=["settings"]).settings.admin_id_set), -1), show_back=False)
    owner_labels = [button.text for row in owner.inline_keyboard for button in row]
    if owner.inline_keyboard:
        assert ("🛡 Admin Panel" in owner_labels) == bool(__import__("app.config", fromlist=["settings"]).settings.admin_id_set)


def test_persistent_menu_labels_do_not_expose_command_syntax():
    keyboard = persistent_menu_keyboard()
    assert all(not button.text.startswith("/") for row in keyboard.keyboard for button in row)


def test_persistent_menu_is_localized_for_arabic_users():
    keyboard = persistent_menu_keyboard("ar")
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert labels == ["🏠 الرئيسية", "📊 الأسواق", "🔔 التنبيهات", "⭐ بريميوم"]


def test_telegram_command_menu_exposes_every_public_command():
    from app.main import COMMANDS

    commands = {command.command for command in COMMANDS}
    assert {
        "start", "create", "alert", "price", "prices", "market", "movers",
        "live", "alerts", "history", "watchlist", "signals", "gas",
        "gasalert", "del", "cancel", "premium", "help", "language",
        "settings", "terms", "paysupport",
    } <= commands

def test_persistent_markets_action_uses_market_overview(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.handlers as handlers
    from app.i18n import tr

    async def scenario():
        user = SimpleNamespace(id=123, language="en")
        monkeypatch.setattr(handlers, "get_user", AsyncMock(return_value=user))
        monkeypatch.setattr(
            handlers,
            "market_snapshot_text",
            lambda snapshot, language: f"snapshot:{snapshot['market_cap_usd']}:{language}",
        )
        prices = SimpleNamespace(
            market_overview=AsyncMock(return_value={"market_cap_usd": 123})
        )
        message = SimpleNamespace(
            text=tr("en", "quick_markets"),
            from_user=SimpleNamespace(id=123),
            answer=AsyncMock(),
        )

        await handlers.persistent_menu_action(message, None, prices, None)

        prices.market_overview.assert_awaited_once()
        assert message.answer.await_args.args[0] == "snapshot:123:en"

    asyncio.run(scenario())



def test_help_navigation_opens_help_content_instead_of_home(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.handlers as handlers
    from app.i18n import tr

    async def scenario():
        user = SimpleNamespace(id=123, language="en")
        monkeypatch.setattr(handlers, "get_user", AsyncMock(return_value=user))
        message = SimpleNamespace(edit_text=AsyncMock())
        callback = SimpleNamespace(
            data="nav:help",
            from_user=SimpleNamespace(id=123),
            message=message,
            answer=AsyncMock(),
        )
        await handlers.cb_home_or_back(callback, None)
        assert message.edit_text.await_args.args[0] == tr("en", "help_text")
        callback.answer.assert_awaited_once()

    asyncio.run(scenario())



def test_home_navigation_acknowledges_callback_before_database_lookup(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import app.handlers as handlers

    async def scenario():
        events = []
        user = SimpleNamespace(id=123, language="en")

        async def get_user_first(*args, **kwargs):
            events.append("get_user")
            return user

        async def answer_first():
            events.append("answer")

        async def edit_first(*args, **kwargs):
            events.append("edit")

        monkeypatch.setattr(handlers, "get_user", get_user_first)
        message = SimpleNamespace(edit_text=edit_first)
        callback = SimpleNamespace(
            data="nav:home",
            from_user=SimpleNamespace(id=123),
            message=message,
            answer=answer_first,
        )

        await handlers.cb_home_or_back(callback, None)
        assert events[0] == "answer"
        assert events.index("answer") < events.index("get_user")
        assert "edit" in events

    asyncio.run(scenario())


def test_home_navigation_sends_fresh_dashboard_if_submenu_message_cannot_be_edited(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramBadRequest

    import app.handlers as handlers
    from app.i18n import welcome_text

    async def scenario():
        user = SimpleNamespace(id=123, language="en")
        monkeypatch.setattr(handlers, "get_user", AsyncMock(return_value=user))
        message = SimpleNamespace(
            edit_text=AsyncMock(side_effect=TelegramBadRequest(method=object(), message="message can't be edited")),
            answer=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="nav:home",
            from_user=SimpleNamespace(id=123),
            message=message,
            answer=AsyncMock(),
        )

        await handlers.cb_home_or_back(callback, None)

        message.answer.assert_awaited_once()
        assert message.answer.await_args.args[0] == welcome_text("en")
        assert message.answer.await_args.kwargs["reply_markup"] is not None
        message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        callback.answer.assert_awaited_once()

    asyncio.run(scenario())
