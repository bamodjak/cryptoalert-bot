import re

_NUM = re.compile(r"^\$?([0-9]*\.?[0-9]+)\s*([kmb])?$", re.I)
_ALERT = re.compile(r"^\s*\$?([A-Za-z0-9\-]{1,30})\s*(>|<|above|below)\s*(.+?)\s*$", re.I)
_MULT = {"": 1, "k": 1e3, "m": 1e6, "b": 1e9}


def parse_number(s: str) -> float:
    m = _NUM.match(s.replace(",", "").replace(" ", "").strip())
    if not m:
        raise ValueError("bad number")
    v = float(m[1]) * _MULT[(m[2] or "").lower()]
    if not 0 < v < 1e13:
        raise ValueError("out of range")
    return v


def parse_percentage(text: str) -> float:
    """Parse a custom percentage change in the supported range 0.01..1000."""
    cleaned = (text or "").strip()
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1].strip()
    value = parse_number(cleaned)
    if not 0.01 <= value <= 1000:
        raise ValueError("percentage out of range")
    return value


def parse_alert(text: str) -> tuple[str, str, float]:
    m = _ALERT.match(text or "")
    if not m:
        raise ValueError("bad alert")
    op = ">" if m[2].lower() in (">", "above") else "<"
    return m[1].lower(), op, parse_number(m[3])


def parse_referral(start_args: str | None, current_user_id: int) -> int | None:
    """Extract referrer Telegram user ID from '?start=ref_<id>' or '?start=<id>', blocking self-referrals."""
    if not start_args:
        return None
    cleaned = start_args.strip()
    if cleaned.startswith("ref_"):
        cleaned = cleaned[4:]
    if not cleaned.isdigit():
        return None
    ref_id = int(cleaned)
    if ref_id <= 0 or ref_id == current_user_id:
        return None
    return ref_id


def fmt_usd(v: float) -> str:
    return f"${v:,.2f}" if v >= 1 else f"${v:.4g}"


def fmt_gwei(v: float) -> str:
    return f"{v:.2f}" if v >= 1 else f"{v:.4g}"
