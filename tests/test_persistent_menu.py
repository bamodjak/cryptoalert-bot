from app.handlers import persistent_menu_keyboard


def test_persistent_menu_is_persistent_and_contains_core_actions():
    keyboard = persistent_menu_keyboard()
    assert keyboard.is_persistent is True
    assert keyboard.resize_keyboard is True
    labels = [button.text for row in keyboard.keyboard for button in row]
    assert labels == [
        "/start", "/market", "/movers",
        "/price btc", "/alerts", "/premium",
        "/help",
    ]
