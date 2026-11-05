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
    assert len(public.inline_keyboard) == 8
    assert "🛡 Admin Panel" not in labels
    assert all("Admin" not in label for label in labels)

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
