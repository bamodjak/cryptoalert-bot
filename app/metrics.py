from prometheus_client import Counter, Gauge

ALERTS_FIRED = Counter("alerts_fired_total", "Alerts delivered to users")
SEND_ERRORS = Counter("send_errors_total", "Failed alert deliveries")
PRICE_ERRORS = Counter("price_source_errors_total", "Failed calls to price/gas sources")
PAYMENTS = Counter("payments_total", "Successful Stars payments")
LAST_CHECK = Gauge("alert_check_last_success_unixtime", "Last successful alert check")
LIVE_RUNTIME_ACTIVE = Gauge("live_market_runtime_active", "Whether this instance owns the live market runtime lease")
LIVE_STREAM_CONNECTED = Gauge("live_trade_stream_connected", "Whether the Binance public trade stream is connected")
LIVE_STREAM_RECONNECTS = Counter("live_trade_stream_reconnects_total", "Live trade stream reconnect attempts")
LIVE_TRADES_RECEIVED = Counter("live_trades_received_total", "Valid live trade updates received")
LIVE_DASHBOARD_EDITS = Counter("live_dashboard_edits_total", "Successful live Telegram dashboard edits")
LIVE_DASHBOARD_EDIT_ERRORS = Counter("live_dashboard_edit_errors_total", "Failed live Telegram dashboard edits")
LIVE_RUNTIME_LEASE_LOSSES = Counter("live_runtime_lease_losses_total", "Lost live runtime lease events")
