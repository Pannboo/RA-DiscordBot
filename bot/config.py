from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    discord_token: str = Field(..., alias="DISCORD_TOKEN")
    dev_guild_ids: str = Field("", alias="DEV_GUILD_IDS")

    ra_api_username: str = Field(..., alias="RA_API_USERNAME")
    ra_api_key: str = Field(..., alias="RA_API_KEY")

    # Either give a full DATABASE_URL, or leave it unset and the URL is built
    # from the parts below. The POSTGRES_* names match the postgres image, so
    # one .env can serve both containers. In Docker, `python -m bot.db.wait`
    # also searches for the database if the configured host doesn't answer.
    database_url: str = Field("", alias="DATABASE_URL")
    db_host: str = Field("db", alias="DB_HOST")
    db_port: int = Field(5432, alias="DB_PORT")
    db_user: str = Field("ra", validation_alias=AliasChoices("DB_USER", "POSTGRES_USER"))
    db_password: str = Field(
        "ra", validation_alias=AliasChoices("DB_PASSWORD", "POSTGRES_PASSWORD")
    )
    db_name: str = Field("ra_bot", validation_alias=AliasChoices("DB_NAME", "POSTGRES_DB"))
    # Set to false to only ever use the configured host (no searching).
    db_discovery: bool = Field(True, alias="DB_DISCOVERY")

    poll_tick_seconds: int = Field(10, alias="POLL_TICK_SECONDS")
    poll_active_seconds: int = Field(30, alias="POLL_ACTIVE_SECONDS")
    poll_idle_seconds: int = Field(900, alias="POLL_IDLE_SECONDS")
    active_window_seconds: int = Field(3600, alias="ACTIVE_WINDOW_SECONDS")
    recent_lookback_minutes: int = Field(60, alias="RECENT_LOOKBACK_MINUTES")
    awards_interval_seconds: int = Field(900, alias="AWARDS_INTERVAL_SECONDS")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @model_validator(mode="after")
    def _build_database_url(self) -> Settings:
        if not self.database_url:
            self.database_url = URL.create(
                "postgresql+asyncpg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
                database=self.db_name,
            ).render_as_string(hide_password=False)
        return self

    @property
    def dev_guild_id_list(self) -> list[int]:
        if not self.dev_guild_ids.strip():
            return []
        return [int(x.strip()) for x in self.dev_guild_ids.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
