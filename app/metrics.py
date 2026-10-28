from prometheus_client import Counter, Gauge

ALERTS_FIRED = Counter("alerts_fired_total", "Alerts delivered to users")
SEND_ERRORS = Counter("send_errors_total", "Failed alert deliveries")
PRICE_ERRORS = Counter("price_source_errors_total", "Failed calls to price/gas sources")
PAYMENTS = Counter("payments_total", "Successful Stars payments")
LAST_CHECK = Gauge("alert_check_last_success_unixtime", "Last successful alert check")
