import asyncio
import json
import time
from pathlib import Path

from app import main


class FakeRedis:
    async def ping(self):
        return True


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, statement):
        return None


def _health_handler(app):
    for route in app.router.routes():
        if route.method == "GET" and route.resource.canonical == "/health":
            return route.handler
    raise AssertionError("GET /health route was not registered")


def _call_health(monkeypatch, redis, last_success, session_factory=FakeSession, transport_ready=True, live_runtime=False, live_stream=False):
    monkeypatch.setattr(main, "Session", session_factory)
    monkeypatch.setattr(main.worker, "last_success", last_success)
    monkeypatch.setattr(main, "telegram_transport_ready", transport_ready)
    main.metrics.LIVE_RUNTIME_ACTIVE.set(1 if live_runtime else 0)
    main.metrics.LIVE_STREAM_CONNECTED.set(1 if live_stream else 0)

    async def call():
        app = main.build_web_app(redis)
        return await _health_handler(app)(None)

    return asyncio.run(call())


def test_health_stays_unavailable_until_alert_worker_succeeds(monkeypatch):
    response = _call_health(monkeypatch, FakeRedis(), 0.0)

    assert response.status == 503
    assert json.loads(response.text) == {"ok": False, "error": "alert worker stale"}


def test_health_is_ok_after_telegram_and_alert_worker_are_ready(monkeypatch):
    response = _call_health(monkeypatch, FakeRedis(), time.time(), transport_ready=True)

    assert response.status == 200
    assert json.loads(response.text) == {
        "ok": True,
        "live_runtime_active": False,
        "live_stream_connected": False,
    }


def test_health_reports_live_runtime_and_stream_state(monkeypatch):
    response = _call_health(
        monkeypatch, FakeRedis(), time.time(), live_runtime=True, live_stream=True,
    )
    assert response.status == 200
    assert json.loads(response.text) == {
        "ok": True,
        "live_runtime_active": True,
        "live_stream_connected": True,
    }


def test_health_fails_while_telegram_transport_is_not_ready(monkeypatch):
    response = _call_health(monkeypatch, FakeRedis(), time.time(), transport_ready=False)

    assert response.status == 503
    assert json.loads(response.text) == {
        "ok": False,
        "error": "telegram transport not ready",
    }


def test_health_fails_when_redis_dependency_is_unavailable(monkeypatch):
    class BrokenRedis:
        async def ping(self):
            raise RuntimeError("Redis unavailable")

    response = _call_health(monkeypatch, BrokenRedis(), time.time())

    assert response.status == 503
    assert json.loads(response.text) == {
        "ok": False,
        "error": "dependency check failed",
    }


def test_health_fails_when_database_dependency_is_unavailable(monkeypatch):
    class BrokenSession(FakeSession):
        async def execute(self, statement):
            raise RuntimeError("Database unavailable")

    response = _call_health(monkeypatch, FakeRedis(), time.time(), BrokenSession)

    assert response.status == 503
    assert json.loads(response.text) == {
        "ok": False,
        "error": "dependency check failed",
    }


def test_health_rejects_an_alert_worker_that_has_gone_stale(monkeypatch):
    stale_after = main.settings.check_interval * 5
    response = _call_health(monkeypatch, FakeRedis(), time.time() - stale_after - 1)

    assert response.status == 503
    assert json.loads(response.text) == {"ok": False, "error": "alert worker stale"}



def test_docker_healthcheck_uses_botkeep_or_standard_runtime_port():
    dockerfile = Path(__file__).parents[1] / "Dockerfile"
    content = dockerfile.read_text()

    assert "os.getenv('SERVER_PORT', os.getenv('PORT', '8080'))" in content
    assert "127.0.0.1:8080/health" not in content



def test_read_only_operations_dashboard_is_served_without_embedded_credentials():
    async def run():
        app = main.build_web_app(FakeRedis())
        route = next(route for route in app.router.routes() if route.method == "GET" and route.resource.canonical == "/dashboard")
        response = await route.handler(None)
        return response

    response = asyncio.run(run())
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "Orbsat · Operations" in response.text
    assert "METRICS_TOKEN" in response.text
    assert "localStorage" not in response.text
    assert 'id="live"' in response.text
    assert "/stats.json" in response.text


def test_operations_stats_endpoint_rejects_requests_without_bearer_token():
    async def run():
        app = main.build_web_app(FakeRedis())
        route = next(route for route in app.router.routes() if route.method == "GET" and route.resource.canonical == "/stats.json")
        return await route.handler(type("Request", (), {"headers": {}})())

    response = asyncio.run(run())
    assert response.status == 404



def test_operations_stats_endpoint_returns_extended_counts_only_with_valid_token(monkeypatch):
    from pydantic import SecretStr

    class StatsSession(FakeSession):
        async def scalar(self, statement):
            if not hasattr(self, "values"):
                self.values = iter([100, 10, 200, 2, 3, 7, 4, 999, 12, 5])
            return next(self.values)

    class StatsRedis(FakeRedis):
        async def exists(self, key):
            return False

    monkeypatch.setattr(main, "Session", StatsSession)
    monkeypatch.setattr(main.settings, "metrics_token", SecretStr("test-operations-token"))

    async def run():
        app = main.build_web_app(StatsRedis())
        route = next(route for route in app.router.routes() if route.method == "GET" and route.resource.canonical == "/stats.json")
        request = type("Request", (), {"headers": {"Authorization": "Bearer test-operations-token"}})()
        return await route.handler(request)

    response = asyncio.run(run())
    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["users"] == 100
    assert payload["new_users"] == 7
    assert payload["premium_users"] == 10
    assert payload["suspended_users"] == 2
    assert payload["blocked_users"] == 3
    assert payload["pending_manual_payments"] == 4
    assert payload["stars_earned"] == 999
    assert payload["live_runtime_active"] is False
    assert payload["live_stream_connected"] is False
