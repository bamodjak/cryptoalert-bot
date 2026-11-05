from datetime import datetime, timezone
from types import SimpleNamespace

from app.handlers import alert_history_text, alerts_keyboard


def test_alert_history_renders_triggered_events_in_english_and_arabic():
    row = SimpleNamespace(
        id=4, symbol="BTC", kind="price", op=">", target=70000.0,
        triggered_at=datetime(2026, 11, 5, 8, 30, tzinfo=timezone.utc),
    )
    english = alert_history_text([row], "en")
    arabic = alert_history_text([row], "ar")
    assert "Recent triggered alerts" in english
    assert "BTC" in english and "2026-11-05 08:30" in english
    assert "أحدث التنبيهات" in arabic


def test_alert_history_empty_state_is_clear():
    assert "No triggered alerts" in alert_history_text([], "en")


def test_alerts_keyboard_exposes_history_entry():
    keyboard = alerts_keyboard([], user_id=1, lang="en")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "alerts:history" in callbacks


def test_alert_history_converts_timestamps_to_selected_timezone():
    row = SimpleNamespace(
        id=5, symbol="ETH", kind="price", op="<", target=2500.0,
        triggered_at=datetime(2026, 11, 5, 8, 30, tzinfo=timezone.utc),
    )
    rendered = alert_history_text([row], "en", "America/New_York")
    assert "2026-11-05 03:30" in rendered
    assert "America/New_York" in rendered
