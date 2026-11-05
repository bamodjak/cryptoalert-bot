from app.watchlists import MAX_WATCHLIST_ITEMS, SUPPORTED, asset_picker_keyboard, watchlist_keyboard


def test_watchlist_asset_catalog_is_unique_and_bounded():
    symbols = [symbol for symbol, _ in SUPPORTED]
    assert len(symbols) == len(set(symbols))
    assert len(symbols) >= MAX_WATCHLIST_ITEMS
    assert {"btc", "eth", "sol", "usdt", "usdc"} <= set(symbols)


def test_asset_picker_uses_callback_data_and_back_action():
    keyboard = asset_picker_keyboard()
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "wl:add:btc" in callbacks
    assert "wl:add:eth" in callbacks
    assert "wl:open" in callbacks


def test_watchlist_keyboard_exposes_add_and_dashboard_actions():
    keyboard = watchlist_keyboard([])
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["wl:add", "nav:home"]
