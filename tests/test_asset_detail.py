from app.asset_detail import asset_detail_keyboard, asset_detail_text


def test_asset_detail_is_localized_and_escapes_provider_supplied_names():
    coin = {
        "name": "<b>Injected</b>",
        "symbol": "btc",
        "rank": 1,
        "price_usd": 65000.0,
        "change_24h": 2.5,
    }
    payload = {"fetched_at": 1_900_000_000, "source": "coingecko", "stale": False}
    english = asset_detail_text(coin, "en", payload)
    arabic = asset_detail_text(coin, "ar", payload)
    assert "&lt;b&gt;Injected&lt;/b&gt;" in english
    assert "BTC" in english and "$65,000" in english
    assert "ترتيب السوق" in arabic


def test_asset_detail_keyboard_exposes_real_cross_feature_actions():
    keyboard = asset_detail_keyboard("btc", "en", "prices:open")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["create:coin:btc", "wl:add:btc", "advisor:open", "prices:open"]
