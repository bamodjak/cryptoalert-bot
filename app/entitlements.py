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
    return (
        invoice_payload == f"premium:{premium_days}"
        and currency == "XTR"
        and total_amount == premium_price_stars
    )
