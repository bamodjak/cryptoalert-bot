from functools import cached_property
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_asyncpg_url(url: str) -> str:
    """Ensure Postgres URLs from Neon, Render, or Docker Compose work seamlessly with SQLAlchemy asyncpg."""
    u = (url or "").strip()
    if u.startswith("postgres://"):
        u = "postgresql+asyncpg://" + u[len("postgres://"):]
    elif u.startswith("postgresql://"):
        u = "postgresql+asyncpg://" + u[len("postgresql://"):]
    if "?" in u:
        parts = urlsplit(u)
        query = dict(parse_qsl(parts.query))
        if "sslmode" in query:
            sslmode = query.pop("sslmode")
            if sslmode in ("require", "verify-ca", "verify-full") and "ssl" not in query:
                query["ssl"] = "require"
        query.pop("channel_binding", None)
        u = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return u


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr
    bot_username: str = "Orbsat16bot"
    update_bot_profile_on_startup: bool = False  # opt in only when intentionally changing profile metadata
    admin_ids: str = ""  # comma-separated Telegram user ids
    owner_id: int = 0  # numeric Telegram user ID; owner receives free Premium

    database_url: str = "postgresql+asyncpg://bot:bot@db:5432/bot"
    redis_url: str = "redis://redis:6379/0"

    # Leave WEBHOOK_BASE_URL empty to run in long-polling mode (no domain needed)
    webhook_base_url: str = ""
    webhook_path: str = "/tg/webhook"
    webhook_secret: str = ""
    metrics_token: SecretStr = SecretStr("")  # protect /metrics and /stats.json; disabled when unset
    host: str = "0.0.0.0"
    port: int = 8080

    coingecko_api_key: str = ""  # optional free "demo" key raises rate limits
    eth_rpc_url: str = "https://ethereum-rpc.publicnode.com"

    check_interval: int = 60
    free_alert_limit: int = 30
    premium_alert_limit: int = 500
    premium_price_stars: int = 1500
    premium_days: int = 30
    support_contact: str = "@M_4_iQ"

    @field_validator("database_url", mode="before")
    @classmethod
    def _clean_db_url(cls, v: str) -> str:
        return normalize_asyncpg_url(v)

    @model_validator(mode="after")
    def _default_owner_to_first_admin(self):
        # Keep existing deployments compatible: if OWNER_ID is unset, the first
        # configured ADMIN_IDS entry is the owner as requested in the deployment setup.
        if self.owner_id <= 0:
            admin_ids = [
                int(value)
                for value in self.admin_ids.replace(" ", "").split(",")
                if value.isdigit() and int(value) > 0
            ]
            if admin_ids:
                self.owner_id = admin_ids[0]
        return self

    @cached_property
    def admin_id_set(self) -> frozenset[int]:
        return frozenset(({self.owner_id} if self.owner_id > 0 else set()) | {int(x) for x in self.admin_ids.replace(" ", "").split(",") if x.isdigit()})


settings = Settings()
