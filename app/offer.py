"""Runtime-editable Premium offer stored in Redis, with environment defaults."""
from redis.asyncio import Redis
from .config import settings

PRICE_KEY = "config:premium:price_stars"
DAYS_KEY = "config:premium:days"

async def get_offer(redis: Redis) -> tuple[int, int]:
    price, days = await redis.mget([PRICE_KEY, DAYS_KEY])
    try:
        price_value = int(price) if price is not None else settings.premium_price_stars
        days_value = int(days) if days is not None else settings.premium_days
    except (TypeError, ValueError):
        return settings.premium_price_stars, settings.premium_days
    if not 1 <= price_value <= 100000 or not 1 <= days_value <= 3650:
        return settings.premium_price_stars, settings.premium_days
    return price_value, days_value

async def set_offer_value(redis: Redis, field: str, value: int) -> tuple[int, int]:
    if field == "price":
        if not 1 <= value <= 100000:
            raise ValueError("Price must be between 1 and 100000 Stars.")
        await redis.set(PRICE_KEY, value)
    elif field == "days":
        if not 1 <= value <= 3650:
            raise ValueError("Duration must be between 1 and 3650 days.")
        await redis.set(DAYS_KEY, value)
    else:
        raise ValueError("Unknown Premium offer setting.")
    return await get_offer(redis)
