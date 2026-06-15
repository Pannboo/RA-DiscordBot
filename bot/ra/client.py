from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from bot.config import get_settings
from bot.ra.types import GameInfoProgress, RecentAchievement, UserAwards, UserSummary

log = logging.getLogger(__name__)

RA_BASE_URL = "https://retroachievements.org/API"
DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RATE_LIMIT_DELAY = 30.0


class RAClient:
    """Async client for the RetroAchievements Web API.

    Auth is via the `z` (username) + `y` (API key) query params on every request.
    A small concurrency limit keeps us friendly to the API.
    """

    def __init__(self, max_concurrency: int = 4) -> None:
        settings = get_settings()
        self._username = settings.ra_api_username
        self._api_key = settings.ra_api_key
        self._client = httpx.AsyncClient(
            base_url=RA_BASE_URL,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "RA-DiscordBot/0.1 (+https://retroachievements.org)"},
        )
        self._sem = asyncio.Semaphore(max_concurrency)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        params = {**params, "z": self._username, "y": self._api_key}
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            async with self._sem:
                r = await self._client.get(path, params=params)

            if r.status_code != 429:
                break

            retry_after = _retry_after_seconds(r)
            if attempt >= MAX_RATE_LIMIT_RETRIES:
                log.warning(
                    "RA %s is still rate-limited after %d retries; skipping this poll",
                    path,
                    MAX_RATE_LIMIT_RETRIES,
                )
                return None

            delay = retry_after if retry_after is not None else DEFAULT_RATE_LIMIT_DELAY
            delay += random.uniform(0.0, 2.0)
            log.warning("RA %s rate-limited; retrying in %.1fs", path, delay)
            await asyncio.sleep(delay)

        if r.status_code != 200:
            log.warning("RA %s returned %s: %s", path, r.status_code, r.text[:200])
            return None
        # RA occasionally serves text/html for some errors; handle JSON parse defensively.
        try:
            return r.json()
        except ValueError:
            log.warning("RA %s returned non-JSON body: %s", path, r.text[:200])
            return None

    async def get_user_recent_achievements(
        self, username: str, minutes: int = 60
    ) -> list[RecentAchievement]:
        data = await self._get(
            "/API_GetUserRecentAchievements.php", {"u": username, "m": minutes}
        )
        if not isinstance(data, list):
            return []
        return [RecentAchievement.model_validate(item) for item in data]

    async def get_user_summary(self, username: str, recent_games: int = 1) -> UserSummary | None:
        data = await self._get(
            "/API_GetUserSummary.php", {"u": username, "g": recent_games, "a": 5}
        )
        if not isinstance(data, dict) or not data.get("User"):
            return None
        return UserSummary.model_validate(data)

    async def get_user_awards(self, username: str) -> UserAwards | None:
        data = await self._get("/API_GetUserAwards.php", {"u": username})
        if not isinstance(data, dict):
            return None
        return UserAwards.model_validate(data)

    async def get_game_info_and_user_progress(
        self, username: str, game_id: int
    ) -> GameInfoProgress | None:
        data = await self._get(
            "/API_GetGameInfoAndUserProgress.php", {"u": username, "g": game_id}
        )
        if not isinstance(data, dict):
            return None
        return GameInfoProgress.model_validate(data)

    async def get_console_ids(self) -> list[dict]:
        """Fetch the canonical RA console list. Stable; cache at startup."""
        data = await self._get("/API_GetConsoleIDs.php", {})
        return data if isinstance(data, list) else []

    async def get_achievement_of_the_week(self) -> dict | None:
        """Fetch the current Achievement of the Week (game + achievement)."""
        data = await self._get("/API_GetAchievementOfTheWeek.php", {})
        return data if isinstance(data, dict) else None

    async def get_game_list(self, console_id: int) -> list[dict]:
        """All games on a given console. Stable; cache aggressively.

        We ask for hashes off and patch-required games included so the index
        matches what users actually see when searching the RA site.
        """
        data = await self._get(
            "/API_GetGameList.php",
            {"i": console_id, "h": 0, "f": 1},
        )
        return data if isinstance(data, list) else []


def badge_url(badge_name: str) -> str:
    """Build the asset URL for an achievement badge image."""
    if not badge_name:
        return ""
    return f"https://media.retroachievements.org/Badge/{badge_name}.png"


def game_icon_url(path: str) -> str:
    """RA icon fields come back as a site-relative path like '/Images/12345.png'."""
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return f"https://media.retroachievements.org{path}"


def user_pic_url(path: str | None) -> str:
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return f"https://media.retroachievements.org{path}"


def achievement_url(achievement_id: int) -> str:
    return f"https://retroachievements.org/achievement/{achievement_id}"


def game_url(game_id: int) -> str:
    return f"https://retroachievements.org/game/{game_id}"


def user_url(username: str) -> str:
    return f"https://retroachievements.org/user/{username}"


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
