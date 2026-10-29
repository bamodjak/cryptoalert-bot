from app.worker import _alert_message


def test_price_alert_message_uses_arabic_preference():
    message = _alert_message("price", "BTC", 70000.0, 71000.0, 12, ">", "ar")
    assert "سعر BTC" in message
    assert "أعلى من" in message
    assert "alert #" not in message


def test_price_alert_message_defaults_to_english():
    message = _alert_message("price", "ETH", 2500.0, 2400.0, 7, "<", "en")
    assert "ETH is" in message
    assert "below your" in message
    assert "alert #7" in message


def test_gas_alert_message_is_localized():
    message = _alert_message("gas", "GAS", 0.5, 1.0, 4, "<", "ar")
    assert "رسوم الشبكة" in message
    assert "التنبيه رقم 4" in message


def test_callback_trace_redacts_dynamic_identifiers():
    from app.middlewares import safe_callback_action

    assert safe_callback_action("dash:premium") == "dash:premium"
    assert safe_callback_action("create:coin:btc") == "create:coin"
    assert safe_callback_action("alertdel:123456") == "alertdel:delete"
    assert safe_callback_action("admin:grant:123456:30") == "admin:grant"
    assert safe_callback_action(None) == "unknown"


def test_callback_trace_preserves_safe_static_subactions():
    from app.middlewares import safe_callback_action

    assert safe_callback_action("admin:broadcast:confirm") == "admin:broadcast:confirm"
    assert safe_callback_action("admin:broadcast:cancel") == "admin:broadcast:cancel"
    assert safe_callback_action("um:export:users") == "um:export:users"
    assert safe_callback_action("um:list:free:12") == "um:list"


def test_alert_check_lock_skips_when_another_instance_owns_it(monkeypatch):
    import asyncio
    from app import worker

    class FakeLock:
        async def acquire(self, blocking=False):
            return False

    class FakeRedis:
        def lock(self, *args, **kwargs):
            return FakeLock()

    async def unexpected_check(*args, **kwargs):
        raise AssertionError("check_once must not run without the distributed lock")

    monkeypatch.setattr(worker, "check_once", unexpected_check)
    assert asyncio.run(worker._run_check_with_lock(None, None, FakeRedis())) is False


def test_alert_check_lock_runs_and_releases(monkeypatch):
    import asyncio
    from app import worker

    calls = []

    class FakeLock:
        owned = False

        async def acquire(self, blocking=False):
            self.owned = True
            return True

        async def extend(self, additional_time, replace_ttl=False):
            return self.owned

        async def release(self):
            self.owned = False

    class FakeRedis:
        def __init__(self):
            self.lock_instance = FakeLock()

        def lock(self, *args, **kwargs):
            return self.lock_instance

    async def fake_check(*args, **kwargs):
        calls.append("checked")

    monkeypatch.setattr(worker, "check_once", fake_check)
    assert asyncio.run(worker._run_check_with_lock(None, None, FakeRedis())) is True
    assert calls == ["checked"]
