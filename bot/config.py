from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    discord_token: str = Field(..., alias="DISCORD_TOKEN")
    dev_guild_ids: str = Field("", alias="DEV_GUILD_IDS")

    ra_api_username: str = Field(..., alias="RA_API_USERNAME")
    ra_api_key: str = Field(..., alias="RA_API_KEY")

    database_url: str = Field(..., alias="DATABASE_URL")

    poll_tick_seconds: int = Field(10, alias="POLL_TICK_SECONDS")
    poll_active_seconds: int = Field(30, alias="POLL_ACTIVE_SECONDS")
    poll_idle_seconds: int = Field(900, alias="POLL_IDLE_SECONDS")
    active_window_seconds: int = Field(3600, alias="ACTIVE_WINDOW_SECONDS")
    recent_lookback_minutes: int = Field(60, alias="RECENT_LOOKBACK_MINUTES")
    awards_interval_seconds: int = Field(900, alias="AWARDS_INTERVAL_SECONDS")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @property
    def dev_guild_id_list(self) -> list[int]:
        if not self.dev_guild_ids.strip():
            return []
        return [int(x.strip()) for x in self.dev_guild_ids.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
