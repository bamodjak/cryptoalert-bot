import asyncio

import aiohttp

from app.advisor import _analyse_klines, _fetch_one, format_advisory


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
    row = _analyse_klines("ETH", _candles(1.003))
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



def test_advisor_uses_binance_market_data_only_endpoint_first():
    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        async def json(self):
            return _candles(1.003)

    class Http:
        def __init__(self):
            self.urls = []

        def get(self, url, **kwargs):
            self.urls.append(url)
            return Response()

    async def scenario():
        http = Http()
        result = await _fetch_one(http, "BTCUSDT")
        assert result is not None
        assert result["symbol"] == "BTC"
        assert http.urls[0].startswith("https://data-api.binance.vision/")

    asyncio.run(scenario())


def test_advisor_falls_back_when_primary_binance_host_is_restricted():
    class Response:
        def __init__(self, status):
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def raise_for_status(self):
            if self.status:
                raise aiohttp.ClientResponseError(None, (), status=self.status)

        async def json(self):
            return _candles(1.003)

    class Http:
        def __init__(self):
            self.urls = []

        def get(self, url, **kwargs):
            self.urls.append(url)
            return Response(451 if len(self.urls) == 1 else 0)

    async def scenario():
        http = Http()
        result = await _fetch_one(http, "BTCUSDT")
        assert result is not None
        assert len(http.urls) == 2
        assert http.urls[0].startswith("https://data-api.binance.vision/")
        assert http.urls[1].startswith("https://api.binance.com/")

    asyncio.run(scenario())
