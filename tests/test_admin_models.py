from app.db import AdminAudit, Giveaway, GiveawayEntry, GiveawayWinner, ManualPayment, Payment, PremiumGrant, User
from app.admin_tools import public_router, router


def test_admin_models_registered():
    assert "premium_grants" in User.metadata.tables
    assert "manual_payments" in User.metadata.tables
    assert "giveaways" in User.metadata.tables
    assert "giveaway_entries" in User.metadata.tables
    assert "giveaway_winners" in User.metadata.tables
    assert "admin_audit" in User.metadata.tables


def test_suspension_field_is_non_nullable():
    assert User.__table__.c.suspended.nullable is False


def test_manual_payments_default_to_pending():
    assert ManualPayment.__table__.c.status.default.arg == "pending"


def test_giveaway_entry_is_unique_per_user():
    constraints = [
        constraint for constraint in GiveawayEntry.__table__.constraints
        if getattr(constraint, "name", None) == "uq_giveaway_entry_user"
    ]
    assert len(constraints) == 1


def test_public_giveaway_router_is_separate_from_admin_router():
    public_commands = [handler for handler in public_router.message.handlers]
    admin_commands = [handler for handler in router.message.handlers]
    assert public_commands
    assert all(handler.callback.__name__ == "join_giveaway_cmd" for handler in public_commands)
    assert all(handler.callback.__name__ != "join_giveaway_cmd" for handler in admin_commands)



def test_manual_payment_amount_uses_numeric_storage():
    from sqlalchemy import Numeric

    amount_type = ManualPayment.__table__.c.amount.type
    assert isinstance(amount_type, Numeric)
    assert amount_type.precision == 18
    assert amount_type.scale == 2


def test_owner_has_premium_without_expiry(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "owner_id", 987654321)
    owner = User(id=987654321, premium_until=None)
    assert owner.is_premium is True


def test_regular_user_without_expiry_is_not_premium(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "owner_id", 987654321)
    user = User(id=987654322, premium_until=None)
    assert user.is_premium is False



def test_alerts_have_entitlement_pause_state():
    from app.db import Alert

    column = Alert.__table__.c.entitlement_paused
    assert column.nullable is False
    assert column.default.arg is False


def test_stars_payment_has_entitlement_tracking_fields():
    columns = Payment.__table__.c
    assert "premium_days" in columns
    assert "entitlement_start" in columns
    assert "entitlement_end" in columns
    assert columns.entitlement_tracked.default.arg is False



def test_payment_model_tracks_uncertain_refunds():
    from app.db import Payment

    column = Payment.__table__.c.refund_pending
    assert column.nullable is False
    assert column.default.arg is False


def test_refund_calculation_only_removes_unused_interval():
    from datetime import datetime, timedelta, timezone
    from app.entitlements import refundable_unused_duration

    now = datetime(2026, 1, 20, tzinfo=timezone.utc)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    expiry = datetime(2026, 3, 2, tzinfo=timezone.utc)
    assert refundable_unused_duration(start, end, expiry, now) == timedelta(days=11)


def test_refund_calculation_is_zero_for_expired_entitlement():
    from datetime import datetime, timedelta, timezone
    from app.entitlements import refundable_unused_duration

    now = datetime(2026, 2, 5, tzinfo=timezone.utc)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 31, tzinfo=timezone.utc)
    expiry = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert refundable_unused_duration(start, end, expiry, now) == timedelta(0)



def test_specific_admin_callbacks_are_registered_before_generic_admin_router():
    from aiogram import Dispatcher
    from app.main import register_routers

    dp = Dispatcher()
    register_routers(dp)
    names = [router.name for router in dp.sub_routers]
    assert names.index("admin_tools") < names.index("admin")



def test_giveaway_winners_are_unique_per_giveaway():
    constraints = [
        constraint for constraint in GiveawayWinner.__table__.constraints
        if getattr(constraint, "name", None) == "uq_giveaway_winner_user"
    ]
    assert len(constraints) == 1
