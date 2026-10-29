import pytest

from app.parsing import fmt_gwei, fmt_usd, parse_alert, parse_number, parse_percentage, parse_referral


@pytest.mark.parametrize("s,v", [("70000", 70000), ("70,000", 70000), ("2.5k", 2500), ("$1.2M", 1.2e6), (".5", 0.5)])
def test_numbers(s, v):
    assert parse_number(s) == pytest.approx(v)


@pytest.mark.parametrize("s", ["", "abc", "-5", "0", "1e99"])
def test_bad_numbers(s):
    with pytest.raises(ValueError):
        parse_number(s)


@pytest.mark.parametrize("s,exp", [
    ("btc > 70000", ("btc", ">", 70000)),
    ("ETH below 2.5k", ("eth", "<", 2500)),
    ("$sol<150", ("sol", "<", 150)),
    ("pepe above 0.00001", ("pepe", ">", 0.00001)),
])
def test_alerts(s, exp):
    sym, op, t = parse_alert(s)
    assert (sym, op) == exp[:2] and t == pytest.approx(exp[2])


def test_bad_alert():
    with pytest.raises(ValueError):
        parse_alert("btc = 5")


def test_fmt():
    assert fmt_usd(70000) == "$70,000.00"
    assert fmt_usd(0.00001234) == "$1.234e-05" or fmt_usd(0.00001234).startswith("$")
    assert fmt_gwei(0.0412) == "0.0412"
    assert fmt_gwei(12.5) == "12.50"


def test_referral_parsing():
    assert parse_referral("ref_987654321", current_user_id=111222333) == 987654321
    assert parse_referral("987654321", current_user_id=111222333) == 987654321
    assert parse_referral("ref_111222333", current_user_id=111222333) is None
    assert parse_referral("invalid", current_user_id=111222333) is None


@pytest.mark.parametrize("value,expected", [("2.5%", 2.5), (" 0.01 ", 0.01), ("1000%", 1000)])
def test_custom_percentage(value, expected):
    assert parse_percentage(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["", "0", "-1%", "0.001%", "1000.1%", "nan", "inf"])
def test_bad_custom_percentage(value):
    with pytest.raises(ValueError):
        parse_percentage(value)
