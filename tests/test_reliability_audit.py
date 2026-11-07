import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import IntegrityError


def test_worker_persists_each_successful_alert_delivery(monkeypatch):
    import app.worker as worker

    alert = SimpleNamespace(
        id=501, user_id=77, kind="price", coin_id="bitcoin",
        symbol="BTC", target=10.0, op=">", entitlement_paused=False,
    )

    class FakeSession:
        def __init__(self):
            self.commits = 0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def execute(self, statement):
            return SimpleNamespace(all=lambda: [(alert, "en", None, 77)])
        async def commit(self):
            self.commits += 1

    sessions = []
    def session_factory():
        session = FakeSession()
        sessions.append(session)
        return session

    async def scenario():
        monkeypatch.setattr(worker, "Session", session_factory)
        monkeypatch.setattr(worker.settings, "owner_id", 1, raising=False)
        monkeypatch.setattr(worker.settings, "free_alert_limit", 30, raising=False)
        monkeypatch.setattr(worker.settings, "premium_alert_limit", 500, raising=False)
        bot = SimpleNamespace(send_message=AsyncMock())
        prices = SimpleNamespace(prices=AsyncMock(return_value={"bitcoin": 20.0}))
        await worker.check_once(bot, prices)
        bot.send_message.assert_awaited_once()
        assert len(sessions) == 2
        assert all(session.commits == 1 for session in sessions)

    asyncio.run(scenario())


def test_worker_does_not_start_new_sends_after_lease_loss(monkeypatch):
    import app.worker as worker

    alert = SimpleNamespace(
        id=502, user_id=77, kind="price", coin_id="bitcoin",
        symbol="BTC", target=10.0, op=">", entitlement_paused=False,
    )

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def execute(self, statement):
            return SimpleNamespace(all=lambda: [(alert, "en", None, 77)])
        async def commit(self):
            pass

    async def scenario():
        monkeypatch.setattr(worker, "Session", FakeSession)
        monkeypatch.setattr(worker.settings, "owner_id", 1, raising=False)
        monkeypatch.setattr(worker.settings, "free_alert_limit", 30, raising=False)
        monkeypatch.setattr(worker.settings, "premium_alert_limit", 500, raising=False)
        bot = SimpleNamespace(send_message=AsyncMock())
        prices = SimpleNamespace(prices=AsyncMock(return_value={"bitcoin": 20.0}))
        lost = asyncio.Event()
        lost.set()
        await worker.check_once(bot, prices, lease_lost=lost)
        bot.send_message.assert_not_awaited()

    asyncio.run(scenario())


def test_manual_payment_duplicate_reference_race_is_reported_cleanly(monkeypatch):
    from app.admin_tools import manualpay_cmd

    class FakeSession:
        def __init__(self):
            self.rollback = AsyncMock()
            self.flush = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate")))
            self.get = AsyncMock(return_value=SimpleNamespace(id=77))
            self.scalar = AsyncMock(return_value=None)
            self.add = MagicMock()

    async def scenario():
        session = FakeSession()
        message = SimpleNamespace(from_user=SimpleNamespace(id=1), answer=AsyncMock())
        command = SimpleNamespace(args="77 bank 100 DZD 30 REF-123")
        await manualpay_cmd(message, command, session)
        session.rollback.assert_awaited_once()
        assert "already recorded" in message.answer.await_args.args[0].lower()

    asyncio.run(scenario())


def test_live_dashboard_stale_message_errors_are_classified():
    from app.live_dashboard import is_stale_dashboard_message_error

    assert is_stale_dashboard_message_error(Exception("Bad Request: message to edit not found"))
    assert is_stale_dashboard_message_error(Exception("Bad Request: chat not found"))
    assert not is_stale_dashboard_message_error(Exception("Bad Request: message is not modified"))
    assert not is_stale_dashboard_message_error(Exception("Bad Request: can't parse entities"))
