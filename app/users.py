"""User lifecycle and Telegram identity synchronization."""
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .db import User
from .parsing import parse_referral


async def get_user(
    session: AsyncSession, tg_user, start_args: str | None = None
) -> User:
    """Load or safely create a Telegram user and refresh mutable profile fields."""
    user = await session.get(User, tg_user.id)

    if user is None:
        referrer_id = parse_referral(start_args, tg_user.id)
        if referrer_id and not await session.get(User, referrer_id):
            referrer_id = None

        statement = (
            pg_insert(User)
            .values(
                id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                language=(
                    "ar"
                    if (getattr(tg_user, "language_code", None) or "").lower().startswith("ar")
                    else "en"
                ),
                referred_by=referrer_id,
                affiliation_shares=0,
                referrals_count=0,
            )
            .on_conflict_do_nothing(index_elements=["id"])
            .returning(User.id)
        )
        inserted_id = await session.scalar(statement)

        if inserted_id is not None and referrer_id is not None:
            await session.execute(
                update(User)
                .where(User.id == referrer_id)
                .values(
                    referrals_count=func.coalesce(User.referrals_count, 0) + 1
                )
            )

        user = await session.get(User, tg_user.id)

    if user is None:
        raise RuntimeError("Could not load or create Telegram user")

    user.blocked = False
    user.username = tg_user.username
    user.first_name = tg_user.first_name
    user.last_name = tg_user.last_name
    await session.flush()
    return user
