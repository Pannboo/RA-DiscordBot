from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from bot.config import get_settings
from bot.db import get_session
from bot.db.models import AwardSeen, User
from bot.embeds import (
    award_embed,
    points_milestone_embed,
    session_started_embed,
    unlock_embed,
)
from bot.poller.detector import Detector, apply_award_counts
from bot.poller.dispatcher import Dispatcher
from bot.ra.client import RAClient

log = logging.getLogger(__name__)


class PollScheduler:
    """Drives two cooperating loops:

      * `tick_loop` — runs every POLL_TICK_SECONDS, selects users whose
        `next_poll_at` has elapsed, polls recent achievements + summary,
        emits unlock / session / milestone events, and reschedules the user
        into the active or idle tier.

      * `awards_loop` — runs every AWARDS_INTERVAL_SECONDS and, for any user
        whose last awards-check is older than the interval, fetches awards
        and emits mastery / completion / beaten events.
    """

    def __init__(self, ra: RAClient, dispatcher: Dispatcher) -> None:
        settings = get_settings()
        self._ra = ra
        self._dispatcher = dispatcher
        self._detector = Detector(ra, settings.recent_lookback_minutes)
        self._tick = settings.poll_tick_seconds
        self._active = timedelta(seconds=settings.poll_active_seconds)
        self._idle = timedelta(seconds=settings.poll_idle_seconds)
        self._active_window = timedelta(seconds=settings.active_window_seconds)
        self._awards_interval = timedelta(seconds=settings.awards_interval_seconds)
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    def start(self) -> None:
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="ra-poller-tick"),
            asyncio.create_task(self._awards_loop(), name="ra-poller-awards"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []

    # ---- tier helpers -------------------------------------------------------

    def _is_active(self, user: User, now: datetime) -> bool:
        cutoff = now - self._active_window
        if user.last_presence_active_at and user.last_presence_active_at >= cutoff:
            return True
        if user.last_unlock_at and user.last_unlock_at >= cutoff:
            return True
        return False

    def _next_poll_at(self, user: User, now: datetime) -> datetime:
        return now + (self._active if self._is_active(user, now) else self._idle)

    # ---- loops --------------------------------------------------------------

    async def _tick_loop(self) -> None:
        log.info("poller tick loop started (tick=%ss)", self._tick)
        while not self._stop.is_set():
            try:
                await self._run_tick()
            except Exception:
                log.exception("tick loop error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)
            except asyncio.TimeoutError:
                pass

    async def _awards_loop(self) -> None:
        log.info("poller awards loop started (interval=%ss)", self._awards_interval.total_seconds())
        # Stagger the first awards run so it doesn't collide with the first tick.
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass
        while not self._stop.is_set():
            try:
                await self._run_awards()
            except Exception:
                log.exception("awards loop error")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._awards_interval.total_seconds()
                )
            except asyncio.TimeoutError:
                pass

    # ---- ticks --------------------------------------------------------------

    async def _due_users(self) -> list[User]:
        now = datetime.now(timezone.utc)
        async with get_session() as db:
            stmt = select(User).where(
                (User.next_poll_at.is_(None)) | (User.next_poll_at <= now)
            )
            rows = (await db.execute(stmt)).scalars().all()
        return list(rows)

    async def _run_tick(self) -> None:
        users = await self._due_users()
        if not users:
            return
        log.debug("tick: %d users due", len(users))
        # Process users concurrently but bounded by the RA client's own semaphore.
        await asyncio.gather(*(self._process_user(u) for u in users), return_exceptions=True)

    async def _process_user(self, user_snapshot: User) -> None:
        try:
            await self._process_user_inner(user_snapshot)
        except Exception:
            log.exception(
                "process_user failed for %s — forcing reschedule",
                getattr(user_snapshot, "ra_username", "?"),
            )
            # Guarantee a reschedule so a poison user doesn't get re-polled every tick.
            now = datetime.now(timezone.utc)
            try:
                async with get_session() as db:
                    u = await db.get(User, user_snapshot.discord_id)
                    if u is not None:
                        u.last_polled_at = now
                        u.next_poll_at = now + self._idle
                        await db.commit()
            except Exception:
                log.exception("reschedule fallback also failed for %s", user_snapshot.discord_id)

    async def _process_user_inner(self, user_snapshot: User) -> None:
        now = datetime.now(timezone.utc)
        # Re-load inside a fresh session so mutations stick.
        async with get_session() as db:
            user = await db.get(User, user_snapshot.discord_id)
            if user is None:
                return
            try:
                unlocks, session_evt, milestones = await self._detector.detect_for_user(user)
            except Exception:
                log.exception("detect failed for %s", user.ra_username)
                # Still reschedule so a broken user doesn't get hammered.
                user.last_polled_at = now
                user.next_poll_at = self._next_poll_at(user, now)
                await db.commit()
                return

            user.last_polled_at = now
            user.next_poll_at = self._next_poll_at(user, now)
            await db.commit()
            discord_id = user.discord_id
            ra_username = user.ra_username
            hardcore_only = user.notify_hardcore_only

        if session_evt is not None:
            embed = session_started_embed(
                ra_username,
                session_evt.game_id,
                session_evt.game_title,
                session_evt.console_name,
                session_evt.game_icon,
            )
            await self._dispatcher.post(discord_id, embed)

        for u in unlocks:
            if hardcore_only and not u.achievement.is_hardcore:
                continue
            await self._dispatcher.post(discord_id, unlock_embed(u.achievement, ra_username))

        for m in milestones:
            await self._dispatcher.post(
                discord_id,
                points_milestone_embed(ra_username, m.kind, m.threshold, m.total),
            )

    # ---- awards -------------------------------------------------------------

    async def _due_award_users(self) -> list[User]:
        now = datetime.now(timezone.utc)
        cutoff = now - self._awards_interval
        async with get_session() as db:
            stmt = select(User).where(
                (User.last_awards_polled_at.is_(None))
                | (User.last_awards_polled_at <= cutoff)
            )
            rows = (await db.execute(stmt)).scalars().all()
        return list(rows)

    async def _run_awards(self) -> None:
        users = await self._due_award_users()
        if not users:
            return
        log.debug("awards: %d users due", len(users))
        await asyncio.gather(
            *(self._process_user_awards(u) for u in users), return_exceptions=True
        )

    async def _process_user_awards(self, user_snapshot: User) -> None:
        now = datetime.now(timezone.utc)
        events: list = []
        discord_id: int | None = None
        async with get_session() as db:
            user = await db.get(User, user_snapshot.discord_id)
            if user is None:
                return
            # Seed silently when we've never recorded any award for this user.
            # Using the row count (instead of a timestamp) means a failed first
            # poll can't masquerade as "already seeded" on the next attempt.
            seen_count = await db.scalar(
                select(func.count())
                .select_from(AwardSeen)
                .where(AwardSeen.ra_username == user.ra_username)
            )
            first_time = (seen_count or 0) == 0
            try:
                events, awards = await self._detector.detect_awards_for_user(
                    user, seed_only=first_time
                )
            except Exception:
                log.exception(
                    "awards detect failed for %s — will retry next cycle", user.ra_username
                )
                await db.rollback()
                return
            # A None payload is a transient fetch failure: leave the user
            # un-polled so the next cycle retries instead of caching zeroes.
            if awards is None:
                return
            # Reconcile the cached counts against RA's authoritative numbers.
            apply_award_counts(user, awards)
            # Only mark polled on success — failures retry next cycle.
            user.last_awards_polled_at = now
            await db.commit()
            discord_id = user.discord_id

        ra_username = user_snapshot.ra_username
        for e in events:
            await self._dispatcher.post(
                discord_id, award_embed(ra_username, e.kind, e.award)
            )
