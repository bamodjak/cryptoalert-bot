from app.handlers import crypto_prices_keyboard, crypto_prices_text


def _coins():
    return [
        {
            "rank": index,
            "id": f"coin-{index}",
            "symbol": f"C{index}",
            "name": f"Coin {index}",
            "price_usd": 100.0 + index,
            "change_24h": index / 10,
        }
        for index in range(1, 34)
    ]


def test_crypto_prices_keyboard_offers_all_33_individual_coins_and_all_action():
    coins = _coins()
    keyboard = crypto_prices_keyboard(coins, "en")
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    coin_buttons = [button for button in buttons if button.callback_data.startswith("prices:coin:")]
    assert len(coin_buttons) == 33
    assert coin_buttons[0].callback_data == "prices:coin:1"
    assert coin_buttons[-1].callback_data == "prices:coin:33"
    assert any(button.callback_data == "prices:all" for button in buttons)


def test_crypto_prices_text_can_render_all_or_one_coin():
    coins = _coins()
    payload = {"coins": coins, "fetched_at": 1_900_000_000, "stale": False, "source": "coingecko"}
    all_text = crypto_prices_text(payload, "en")
    one_text = crypto_prices_text(payload, "en", selected=coins[0])
    assert all_text.count("Coin ") == 33
    assert "C33" in all_text
    assert "C1" in one_text
    assert "C2" not in one_text
