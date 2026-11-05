from datetime import timedelta
from types import SimpleNamespace

from app.db import Alert
from app.handlers import alerts_keyboard
from app.db import utcnow


def test_alert_snooze_is_nullable_and_keyboard_exposes_pause_action():
    assert Alert.__table__.c.snoozed_until.nullable is True
    alert = SimpleNamespace(id=12, symbol="BTC", snoozed_until=None)
    keyboard = alerts_keyboard([alert], user_id=1, lang="en")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "alertedit:12" in callbacks
    assert "alertsnooze:12" in callbacks


def test_snoozed_alert_keyboard_exposes_resume_action():
    alert = SimpleNamespace(id=13, symbol="ETH", snoozed_until=utcnow() + timedelta(hours=1))
    keyboard = alerts_keyboard([alert], user_id=1, lang="en")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "alertresume:13" in callbacks
    assert "alertsnooze:13" not in callbacks
