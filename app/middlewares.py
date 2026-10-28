from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update
from redis.asyncio import Redis

from .config import settings
from .db import Session, User

KILL_KEY = "bot:killed"


class KillSwitchMiddleware(BaseMiddleware):
    """Outer update middleware: when the kill switch is on, only admins get through."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]], event: TelegramObject, data: dict) -> Any:
        user = data.get("event_from_user")
        if user and user.id in settings.admin_id_set:
            return await handler(event, data)
        if user and user.id not in settings.admin_id_set:
            async with Session() as session:
                account = await session.get(User, user.id)
                if account and account.suspended:
                    if isinstance(event, Update):
                        if event.message:
                            await event.message.answer("Your account is currently suspended. Contact support if you believe this is a mistake.")
                        elif event.callback_query:
                            await event.callback_query.answer("Your account is suspended.", show_alert=True)
                        elif event.pre_checkout_query:
                            await event.pre_checkout_query.answer(ok=False, error_message="This account is suspended.")
                    return None
        if await self.redis.exists(KILL_KEY):
            if isinstance(event, Update):
                if event.pre_checkout_query:
                    await event.pre_checkout_query.answer(ok=False, error_message="Paused for maintenance. Try again shortly.")
                elif event.message:
                    await event.message.answer("Paused for maintenance. Back shortly.")
            return None
        return await handler(event, data)


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with Session() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


class ThrottleMiddleware(BaseMiddleware):
    """At most `limit` messages per user per minute."""

    def __init__(self, redis: Redis, limit: int = 30):
        self.redis = redis
        self.limit = limit

    async def __call__(self, handler, event: Message, data):
        user = data.get("event_from_user")
        if not user or user.id in settings.admin_id_set:
            return await handler(event, data)
        key = f"thr:{user.id}"
        n = await self.redis.incr(key)
        if n == 1:
            await self.redis.expire(key, 60)
        if n > self.limit:
            if n == self.limit + 1:
                await event.answer("Slow down a little, try again in a minute.")
            return None
        return await handler(event, data)
