from app.advisor import _analyse_klines, format_advisory


def _candles(trend=1.0):
    rows = []
    price = 100.0
    for index in range(120):
        price *= trend
        rows.append([
            index * 14400000, str(price * 0.99), str(price * 1.02),
            str(price * 0.98), str(price), str(1000 + index * 2),
            (index + 1) * 14400000 - 1, "0", 100, "0", "0", "0",
        ])
    return rows


def test_advisor_uses_history_and_returns_bounded_risk_levels():
    result = _analyse_klines("BTC", _candles(1.003))
    assert result["symbol"] == "BTC"
    assert result["price"] > 0
    assert result["score"] <= 6
    assert result["score"] >= -6
    assert result["atr_pct"] > 0
    assert result["reasons"]
    assert result["history"] is not None
    assert result["history"]["samples"] > 0
    assert result["history"]["horizon_hours"] == 24
    assert result["stance"] in {"BULLISH SETUP", "BEARISH / AVOID LONGS", "NEUTRAL · WAIT"}
    if result["score"] >= 3:
        assert result["invalidation"] < result["price"] < result["objective"]


def test_advisor_report_discloses_heuristic_and_risk():
    row = _analyse_klines("ETH", _candles(1.001))
    report = format_advisory([row])
    assert "PREMIUM QUANT DESK" in report
    assert "NOT probabilities" in report
    assert "Not individualized financial advice" in report
    assert "volatility" in report
    assert "Why:" in report
    assert "Historical 24h:" in report
    arabic_report = format_advisory([row], "ar")
    assert "الأسباب:" in arabic_report
    assert "تقييم تاريخي" in arabic_report
