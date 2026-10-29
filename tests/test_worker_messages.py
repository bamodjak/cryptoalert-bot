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


def test_claim_create_action_allows_only_first_action_for_prompt():
    import asyncio
    from app.handlers import claim_create_action

    class FakeRedis:
        def __init__(self):
            self.keys = set()

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.keys:
                return None
            self.keys.add(key)
            return "OK"

    redis = FakeRedis()
    assert asyncio.run(claim_create_action(redis, 42, 100, "set")) is True
    assert asyncio.run(claim_create_action(redis, 42, 100, "set")) is False
    assert asyncio.run(claim_create_action(redis, 42, 101, "set")) is True
    assert asyncio.run(claim_create_action(redis, 42, 100, "confirm")) is True


def test_csv_export_neutralizes_formula_like_user_text():
    from app.admin_management import _safe_csv_cell

    assert _safe_csv_cell("=HYPERLINK(\"https://example.invalid\")") == "'=HYPERLINK(\"https://example.invalid\")"
    assert _safe_csv_cell("  @SUM(A1:A2)") == "'  @SUM(A1:A2)"
    assert _safe_csv_cell("\t=1+1") == "'\t=1+1"
    assert _safe_csv_cell("normal username") == "normal username"
    assert _safe_csv_cell(123456) == 123456


def test_worker_enforces_free_and_premium_alert_entitlements():
    from app.worker import should_pause_alert

    assert should_pause_alert(False, "gas", 1, 30, 500) is True
    assert should_pause_alert(False, "price", 30, 30, 500) is False
    assert should_pause_alert(False, "price", 31, 30, 500) is True
    assert should_pause_alert(True, "gas", 500, 30, 500) is False
    assert should_pause_alert(True, "price", 500, 30, 500) is False
    assert should_pause_alert(True, "price", 501, 30, 500) is True
