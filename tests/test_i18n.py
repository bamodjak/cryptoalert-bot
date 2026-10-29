from app.db import User
from app.i18n import TEXT, tr, welcome_text


def test_user_language_defaults_to_english():
    assert User.__table__.c.language.default.arg == "en"
    assert User.__table__.c.language.nullable is False


def test_arabic_translations_are_selected():
    assert tr("ar", "home") == "🏠 الرئيسية"
    assert "مرحباً بك" in welcome_text("ar")


def test_unknown_language_falls_back_to_english():
    assert tr("fr", "home") == tr("en", "home")



def test_arabic_translation_keys_cover_english_interface():
    assert set(TEXT["en"]) <= set(TEXT["ar"]) | {"en"}


def test_target_price_example_uses_dynamic_current_price_in_supported_languages():
    assert "72500" not in TEXT["en"]["target_prompt"]
    assert "72500" not in TEXT["ar"]["target_prompt"]
    assert "$80,000" in tr("en", "target_prompt", current_price="$80,000")
    assert "$80,000" in tr("ar", "target_prompt", current_price="$80,000")
    assert "{current_price}" not in tr("en", "target_prompt_no_price")
    assert "{current_price}" not in tr("ar", "target_prompt_no_price")
