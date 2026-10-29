from app.live_dashboard import COINS, PAIR_TO_COIN, format_dashboard


def test_live_dashboard_formats_stream_prices_and_age():
    text = format_dashboard({"bitcoin": (80000.0, 1000.0)}, "en", now=1002.0)
    assert "ORBSAT" in text
    assert "BTC" in text
    assert "$80,000" in text
    assert "2s" in text
    assert "ETH" in text
    assert "waiting for trade" in text


def test_live_dashboard_supports_english_and_arabic_copy():
    assert "PREMIUM LIVE MARKET" in format_dashboard({}, "en", now=1000.0)
    assert "سوق بريميوم المباشر" in format_dashboard({}, "ar", now=1000.0)


def test_stream_pairs_map_to_supported_coins_only():
    assert PAIR_TO_COIN["BTCUSDT"][0] == "bitcoin"
    assert PAIR_TO_COIN["ETHUSDT"][0] == "ethereum"
    assert all(pair for _coin_id, _symbol, _name, pair in COINS if _symbol != "USDT")
