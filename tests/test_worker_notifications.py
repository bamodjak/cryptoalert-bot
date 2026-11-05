from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.db import Alert
from app.worker import notification_category_filters


def test_alert_worker_query_respects_independent_notification_categories():
    statement = select(Alert).where(*notification_category_filters())
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "notify_price_alerts" in sql
    assert "notify_gas_alerts" in sql
    assert "alerts.kind" in sql
