import asyncio

from app import main


class FakeLock:
    def __init__(self, acquire_results=None, extend_result=True):
        self.acquire_results = list(acquire_results or [True])
        self.extend_result = extend_result
        self.acquire_calls = 0
        self.released = False

    async def acquire(self, blocking=False):
        self.acquire_calls += 1
        if self.acquire_results:
            return self.acquire_results.pop(0)
        return False

    async def extend(self, ttl, replace_ttl=False):
        return self.extend_result

    async def release(self):
        self.released = True


class FakeRedis:
    def __init__(self, lock):
        self._lock = lock

    def lock(self, name, timeout, blocking):
        assert name == main.POLLING_LOCK_KEY
        assert timeout == main.POLLING_LOCK_TTL_SECONDS
        assert blocking is False
        return self._lock


class FakeDispatcher:
    def __init__(self):
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.start_calls = 0

    def resolve_used_update_types(self):
        return ["message"]

    async def start_polling(self, bot, **kwargs):
        assert kwargs["handle_signals"] is False
        assert kwargs["polling_timeout"] == main.POLLING_TIMEOUT_SECONDS == 5
        assert kwargs["allowed_updates"] == ["message"]
        self.start_calls += 1
        self.started.set()
        await self.stopped.wait()

    async def stop_polling(self):
        self.stopped.set()


def test_polling_lease_waits_for_previous_deployment_and_releases():
    async def scenario():
        lock = FakeLock(acquire_results=[False, True])
        redis = FakeRedis(lock)
        dispatcher = FakeDispatcher()
        stop = asyncio.Event()
        task = asyncio.create_task(main.run_polling_with_lock(dispatcher, object(), redis, stop))

        await asyncio.wait_for(dispatcher.started.wait(), timeout=1)
        assert main.telegram_transport_ready is True
        stop.set()
        await asyncio.wait_for(task, timeout=1)

        assert lock.acquire_calls == 2
        assert dispatcher.start_calls == 1
        assert lock.released
        assert main.telegram_transport_ready is False

    original_retry = main.POLLING_LOCK_RETRY_SECONDS
    original_grace = main.POLLING_STARTUP_GRACE_SECONDS
    main.POLLING_LOCK_RETRY_SECONDS = 0.001
    main.POLLING_STARTUP_GRACE_SECONDS = 0
    try:
        asyncio.run(scenario())
    finally:
        main.POLLING_LOCK_RETRY_SECONDS = original_retry
        main.POLLING_STARTUP_GRACE_SECONDS = original_grace


def test_lost_polling_lease_stops_dispatcher_immediately():
    class LostLease:
        async def extend(self, ttl, replace_ttl=False):
            return False

        async def acquire(self, blocking=False):
            return False  # another instance owns the lease

    async def scenario():
        dispatcher = FakeDispatcher()
        stop = asyncio.Event()
        original_interval = main.POLLING_LOCK_RENEW_SECONDS
        main.POLLING_LOCK_RENEW_SECONDS = 0.001
        try:
            await main._renew_polling_lock(LostLease(), dispatcher, stop)
        finally:
            main.POLLING_LOCK_RENEW_SECONDS = original_interval

        assert stop.is_set()
        assert dispatcher.stopped.is_set()

    asyncio.run(scenario())


def _run_renewal(lock, rounds_until_stop):
    async def scenario():
        dispatcher = FakeDispatcher()
        stop = asyncio.Event()
        lock.on_round = lambda n: stop.set() if n >= rounds_until_stop else None
        original_interval = main.POLLING_LOCK_RENEW_SECONDS
        main.POLLING_LOCK_RENEW_SECONDS = 0.001
        try:
            await asyncio.wait_for(main._renew_polling_lock(lock, dispatcher, stop), timeout=2)
        finally:
            main.POLLING_LOCK_RENEW_SECONDS = original_interval
        return dispatcher

    return asyncio.run(scenario())


def test_transient_redis_errors_do_not_stop_polling():
    class FlakyLease:
        rounds = 0

        async def extend(self, ttl, replace_ttl=False):
            self.rounds += 1
            self.on_round(self.rounds)
            if self.rounds <= 3:
                raise ConnectionError("redis blip")
            return True

        async def acquire(self, blocking=False):
            raise AssertionError("should not re-acquire while extend works")

    dispatcher = _run_renewal(FlakyLease(), rounds_until_stop=6)
    assert not dispatcher.stopped.is_set()


def test_expired_lease_is_reacquired_instead_of_stopping():
    class ExpiredLease:
        rounds = 0
        acquired = 0

        async def extend(self, ttl, replace_ttl=False):
            self.rounds += 1
            self.on_round(self.rounds)
            return self.rounds > 1

        async def acquire(self, blocking=False):
            self.acquired += 1
            return True

    lock = ExpiredLease()
    dispatcher = _run_renewal(lock, rounds_until_stop=3)
    assert lock.acquired == 1
    assert not dispatcher.stopped.is_set()
