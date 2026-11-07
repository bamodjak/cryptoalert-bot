from app.admin_management import _grant_user_button
from app.db import User


def test_user_list_has_direct_premium_grant_button():
    button = _grant_user_button(User(id=8231611086, first_name="Bachir"))

    assert button.text == "🎁 Grant Premium · Bachir"
    assert button.callback_data == "um:grantone:8231611086"


def test_premium_grant_button_label_is_compact():
    button = _grant_user_button(User(id=123456789, first_name="A" * 120))

    assert len(button.text) <= 60
    assert button.callback_data == "um:grantone:123456789"
