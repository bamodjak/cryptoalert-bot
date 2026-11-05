from app.keyboards import nav_keyboard


def test_inline_navigation_is_compact_and_keeps_admin_private():
    public = nav_keyboard(user_id=-1, show_back=False)
    labels = [button.text for row in public.inline_keyboard for button in row]
    assert len(public.inline_keyboard) == 8
    assert "Admin Panel" not in " ".join(labels)

    from app.config import settings

    if settings.admin_id_set:
        owner_id = next(iter(settings.admin_id_set))
        owner = nav_keyboard(user_id=owner_id, show_back=False)
        owner_labels = [button.text for row in owner.inline_keyboard for button in row]
        assert "Admin Panel" in " ".join(owner_labels)


def test_telegram_command_menu_exposes_every_public_command():
    from app.main import COMMANDS

    commands = {command.command for command in COMMANDS}
    assert {
        "start", "create", "alert", "price", "prices", "market", "movers",
        "live", "alerts", "history", "watchlist", "signals", "gas",
        "gasalert", "del", "cancel", "premium", "help", "language",
        "settings", "terms", "paysupport",
    } <= commands
