from app.handlers import persistent_menu_keyboard


def test_persistent_menu_is_persistent_and_contains_core_actions():
    keyboard = persistent_menu_keyboard()
    assert keyboard.is_persistent is True
    assert keyboard.resize_keyboard is True
    assert keyboard.input_field_placeholder == "Orbsat · Markets, alerts and Premium"

    rows = keyboard.keyboard
    assert [[button.text for button in row] for row in rows] == [
        ["/market", "/movers"],
        ["/price btc", "/alerts"],
        ["/premium", "/help"],
    ]
    assert [[button.style for button in row] for row in rows] == [
        ["primary", "primary"],
        ["primary", "primary"],
        ["success", None],
    ]


def test_persistent_menu_uses_existing_command_routes():
    keyboard = persistent_menu_keyboard()
    labels = {button.text for row in keyboard.keyboard for button in row}
    assert labels <= {"/market", "/movers", "/price btc", "/alerts", "/premium", "/help"}
