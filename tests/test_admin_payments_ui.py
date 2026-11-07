import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.admin_tools import confirm_manual_payment_decision, router


def test_manual_payment_confirmation_route_is_distinct_from_decision_request():
    names = [handler.callback.__name__ for handler in router.callback_query.handlers]
    assert names.index("request_manual_payment_decision") < names.index("confirm_manual_payment_decision")
    request_handler = next(h for h in router.callback_query.handlers if h.callback.__name__ == "request_manual_payment_decision")
    from types import SimpleNamespace
    magic = request_handler.filters[0].magic
    assert magic.resolve(SimpleNamespace(data="admin:manualpay:verify:7")) is True
    assert magic.resolve(SimpleNamespace(data="admin:manualpay:confirm:verify:7")) is False


def test_confirmed_manual_payment_verification_grants_premium_and_audits():
    async def scenario():
        payment = SimpleNamespace(
            id=7, user_id=42, status="pending", premium_days=30,
            method="baridimob", amount=Decimal("2500"), currency="DZD",
            verified_by=None, verified_at=None,
        )
        user = SimpleNamespace(id=42, premium_until=None)
        session = SimpleNamespace(
            scalar=AsyncMock(side_effect=[payment, user]),
            add=MagicMock(),
            commit=AsyncMock(),
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        callback = SimpleNamespace(
            data="admin:manualpay:confirm:verify:7",
            from_user=SimpleNamespace(id=99),
            message=None,
            answer=AsyncMock(),
        )

        await confirm_manual_payment_decision(callback, session, bot)

        assert payment.status == "verified"
        assert payment.verified_by == 99
        assert payment.verified_at is not None
        assert user.premium_until is not None
        assert (user.premium_until - datetime.now(timezone.utc)).days in {29, 30}
        assert session.commit.await_count == 1
        assert bot.send_message.await_count == 1
        callback.answer.assert_awaited_once_with("Payment decision recorded.")

    asyncio.run(scenario())


def test_confirmed_manual_payment_rejection_does_not_grant_premium():
    async def scenario():
        payment = SimpleNamespace(
            id=8, user_id=43, status="pending", premium_days=90,
            method="cash", amount=Decimal("1000"), currency="DZD",
            verified_by=None, verified_at=None,
        )
        session = SimpleNamespace(
            scalar=AsyncMock(return_value=payment),
            add=MagicMock(),
            commit=AsyncMock(),
        )
        bot = SimpleNamespace(send_message=AsyncMock())
        callback = SimpleNamespace(
            data="admin:manualpay:confirm:reject:8",
            from_user=SimpleNamespace(id=99),
            message=None,
            answer=AsyncMock(),
        )

        await confirm_manual_payment_decision(callback, session, bot)

        assert payment.status == "rejected"
        assert payment.verified_by == 99
        assert session.commit.await_count == 1
        bot.send_message.assert_not_awaited()

    asyncio.run(scenario())
