"""Pure helpers for Premium offer validation and entitlement accounting."""
from datetime import datetime, timedelta


def refundable_unused_duration(
    entitlement_start: datetime | None,
    entitlement_end: datetime | None,
    current_expiry: datetime | None,
    now: datetime,
) -> timedelta:
    """Return the unused part of one tracked entitlement, capped by account expiry."""
    if not entitlement_start or not entitlement_end or not current_expiry or current_expiry <= now:
        return timedelta(0)
    lower = max(now, entitlement_start)
    upper = min(entitlement_end, current_expiry)
    return max(timedelta(0), upper - lower)


def premium_payment_matches_offer(
    invoice_payload: str,
    currency: str,
    total_amount: int,
    premium_days: int,
    premium_price_stars: int,
) -> bool:
    """Accept only the exact Premium invoice offer configured by the bot."""
    parts = invoice_payload.split(":")
    if currency != "XTR":
        return False
    if len(parts) == 3 and parts[0] == "premium":
        try:
            quoted_days, quoted_price = int(parts[1]), int(parts[2])
        except ValueError:
            return False
        return 1 <= quoted_days <= 3650 and 1 <= quoted_price <= 100000 and total_amount == quoted_price
    return invoice_payload == f"premium:{premium_days}" and total_amount == premium_price_stars
