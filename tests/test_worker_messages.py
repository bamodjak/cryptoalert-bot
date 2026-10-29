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
