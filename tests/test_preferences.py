from types import SimpleNamespace

from app.preferences import SUPPORTED_TIMEZONES, settings_keyboard, settings_text


def test_settings_screen_exposes_timezone_language_and_advanced_mode():
    user = SimpleNamespace(language="en", timezone="Africa/Algiers", advanced_mode=False)
    text = settings_text(user)
    keyboard = settings_keyboard(user)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Africa/Algiers" in text
    assert "settings:tz:Africa/Algiers" in callbacks
    assert "settings:advanced" in callbacks
    assert "lang:choose" in callbacks
    assert set(SUPPORTED_TIMEZONES) >= {"UTC", "Africa/Algiers", "Europe/Paris"}


def test_settings_screen_is_available_in_arabic():
    user = SimpleNamespace(language="ar", timezone="UTC", advanced_mode=True)
    assert "الإعدادات الشخصية" in settings_text(user)
