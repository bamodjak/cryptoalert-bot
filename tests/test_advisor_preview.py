from app.i18n import tr
from app.market_handlers import advisor_preview_keyboard


def test_free_premium_research_preview_has_direct_upgrade_and_home_actions():
    keyboard = advisor_preview_keyboard("en")
    assert [[button.callback_data for button in row] for row in keyboard.inline_keyboard] == [
        ["dash:premium"],
        ["nav:home"],
    ]
    assert keyboard.inline_keyboard[0][0].text == tr("en", "advisor_preview_cta")
    assert "Compare 8 major assets" in tr("en", "advisor_premium_only")
    assert "No trade is placed" in tr("en", "advisor_premium_only")


def test_free_premium_research_preview_is_localized():
    keyboard = advisor_preview_keyboard("ar")
    assert keyboard.inline_keyboard[0][0].text == tr("ar", "advisor_preview_cta")
    assert "8 أصول" in tr("ar", "advisor_premium_only")
