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


def _call_health(monkeypatch, redis, last_success):
    monkeypatch.setattr(main, "Session", FakeSession)
    monkeypatch.setattr(main.worker, "last_success", last_success)

    async def call():
        app = main.build_web_app(redis)
        return await _health_handler(app)(None)

    return asyncio.run(call())


def test_health_stays_unavailable_until_alert_worker_succeeds(monkeypatch):
    response = _call_health(monkeypatch, FakeRedis(), 0.0)

    assert response.status == 503
    assert json.loads(response.text) == {"ok": False, "error": "alert worker stale"}


def test_health_is_ok_after_a_recent_alert_worker_success(monkeypatch):
    response = _call_health(monkeypatch, FakeRedis(), time.time())

    assert response.status == 200
    assert json.loads(response.text) == {"ok": True}


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


def test_render_heartbeat_probes_health_endpoint_instead_of_root():
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "render-heartbeat.yml"

    assert '"${SERVICE_URL}/health"' in workflow_path.read_text()
