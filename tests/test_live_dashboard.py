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


def test_dashboard_keyboard_has_no_inert_refresh_button():
    from app.live_dashboard import dashboard_keyboard

    markup = dashboard_keyboard("en")
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == ["live:close"]


def test_market_trade_cache_does_not_depend_on_redis():
    from app import live_dashboard

    live_dashboard.LIVE_QUOTES.clear()
    assert live_dashboard.record_trade("BTCUSDT", 65000.0, received_at=123.0) is True
    assert live_dashboard.LIVE_QUOTES["bitcoin"] == (65000.0, 123.0)
    assert live_dashboard.record_trade("USDCUSDT", 1.0001, received_at=124.0) is True
    assert live_dashboard.LIVE_QUOTES["usd-coin"] == (1.0001, 124.0)
    assert live_dashboard.LIVE_QUOTES["tether"] == (1.0, 124.0)


def test_market_trade_cache_rejects_unknown_pairs_and_invalid_prices():
    from app import live_dashboard

    live_dashboard.LIVE_QUOTES.clear()
    assert live_dashboard.record_trade("UNKNOWNUSDT", 1.0, received_at=123.0) is False
    assert live_dashboard.record_trade("BTCUSDT", 0.0, received_at=123.0) is False
    assert live_dashboard.LIVE_QUOTES == {}


def test_live_runtime_elects_one_owner_for_stream_and_dashboard(monkeypatch):
    import asyncio
    from app import live_dashboard

    class FakeLock:
        def __init__(self):
            self.acquired = False
            self.released = False
        async def acquire(self, blocking=False):
            self.acquired = True
            return True
        async def extend(self, ttl, replace_ttl=False):
            return True
        async def release(self):
            self.released = True

    class FakeRedis:
        def __init__(self):
            self.lock_instance = FakeLock()
        def lock(self, name, timeout, blocking):
            assert name == live_dashboard.LIVE_RUNTIME_LOCK_KEY
            assert timeout == live_dashboard.LIVE_RUNTIME_LOCK_TTL
            assert blocking is False
            return self.lock_instance

    async def run():
        started = []
        async def fake_worker(*args):
            started.append(True)
            await stop.wait()
        monkeypatch.setattr(live_dashboard, "live_market_stream", fake_worker)
        monkeypatch.setattr(live_dashboard, "live_dashboard_loop", fake_worker)
        redis = FakeRedis()
        stop = asyncio.Event()
        task = asyncio.create_task(live_dashboard.live_runtime_loop(object(), redis, object(), stop))
        for _ in range(100):
            if len(started) == 2:
                break
            await asyncio.sleep(0.001)
        assert len(started) == 2
        assert live_dashboard.metrics.LIVE_RUNTIME_ACTIVE._value.get() == 1
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        assert redis.lock_instance.acquired is True
        assert redis.lock_instance.released is True
        assert live_dashboard.metrics.LIVE_RUNTIME_ACTIVE._value.get() == 0
    asyncio.run(run())
