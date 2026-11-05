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


def test_render_heartbeat_probes_health_endpoint_instead_of_root():
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "render-heartbeat.yml"

    assert '"${SERVICE_URL}/health"' in workflow_path.read_text()


def test_render_heartbeat_allows_cold_start_recovery_without_canceling_active_run():
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "render-heartbeat.yml"
    workflow = workflow_path.read_text()

    assert "timeout-minutes: 8" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "for attempt in {1..18}; do" in workflow
    assert "--max-time 20" in workflow
    assert 'if [[ "${attempt}" -lt 18 ]]; then sleep 5; fi' in workflow


def test_render_heartbeat_schedule_avoids_top_of_hour_contention():
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "render-heartbeat.yml"
    workflow = workflow_path.read_text()

    assert '- cron: "1-59/5 * * * *"' in workflow
    assert "top of each hour" in workflow


def test_render_heartbeat_retry_budget_fits_eight_minute_job():
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "render-heartbeat.yml"
    lines = workflow_path.read_text().splitlines()

    request_timeout = int(next(line for line in lines if "--max-time" in line).split("--max-time", 1)[1].split()[0])
    retry_delay = int(next(line for line in lines if "sleep " in line).split("sleep ", 1)[1].split()[0].rstrip(";"))
    job_timeout = int(next(line for line in lines if "timeout-minutes:" in line).split("timeout-minutes:", 1)[1].strip())
    max_attempts = 18
    worst_case_seconds = max_attempts * request_timeout + (max_attempts - 1) * retry_delay

    assert worst_case_seconds < job_timeout * 60



def test_docker_healthcheck_uses_render_runtime_port():
    dockerfile = Path(__file__).parents[1] / "Dockerfile"
    content = dockerfile.read_text()

    assert "os.getenv('SERVER_PORT', os.getenv('PORT', '8080'))" in content
    assert "127.0.0.1:8080/health" not in content


def test_server_port_environment_variable_is_supported(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("SERVER_PORT", "8123")
    monkeypatch.setenv("PORT", "8124")

    assert Settings(_env_file=None).port == 8123


def test_render_port_environment_variable_remains_supported(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.setenv("PORT", "8124")

    assert Settings(_env_file=None).port == 8124
