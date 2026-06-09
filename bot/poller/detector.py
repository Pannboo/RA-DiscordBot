from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from bot.db import get_session
from bot.db.models import AwardSeen, GameSession, MilestoneSeen, UnlockSeen, User
from bot.ra.client import RAClient
from bot.ra.types import AwardEntry, RecentAchievement

log = logging.getLogger(__name__)


# Points milestones we'll announce when the user crosses them.
POINTS_THRESHOLDS = [
    1_000,
    5_000,
    10_000,
    25_000,
    50_000,
    75_000,
    100_000,
    150_000,
    200_000,
    250_000,
    500_000,
    1_000_000,
]


def _new_thresholds_crossed(old: int, new: int, thresholds: list[int]) -> list[int]:
    return [t for t in thresholds if old < t <= new]


@dataclass
class UnlockEvent:
    achievement: RecentAchievement


@dataclass
class SessionStartedEvent:
    game_id: int
    game_title: str
    console_name: str
    game_icon: str


@dataclass
class AwardEvent:
    kind: str  # mastered, completed, beaten_hc, beaten_sc
    award: AwardEntry


@dataclass
class PointsMilestoneEvent:
    kind: str  # points_hc, points_sc, retropoints
    threshold: int
    total: int


def _classify_award(entry: AwardEntry) -> str | None:
    """Map RA's award metadata into our internal kinds.

    RA returns "Mastery/Completion" as a single award_type and disambiguates
    HC vs SC via `AwardDataExtra` (1 = hardcore). "Game Beaten" works the same
    way. So we always branch on `is_hardcore`, not the textual type alone.
    """
    t = (entry.award_type or "").lower()
    if "beaten" in t:
        return "beaten_hc" if entry.is_hardcore else "beaten_sc"
    if "mastery" in t or "completion" in t or "mastered" in t or "completed" in t:
        return "mastered" if entry.is_hardcore else "completed"
    return None


class Detector:
    """Diffs current RA state against the DB and emits structured events."""

    def __init__(self, ra: RAClient, recent_lookback_minutes: int) -> None:
        self._ra = ra
        self._lookback = recent_lookback_minutes

    async def detect_for_user(
        self, user: User
    ) -> tuple[list[UnlockEvent], SessionStartedEvent | None, list[PointsMilestoneEvent]]:
        """Run the cheap per-tick detection: recent unlocks, session start, point milestones.

        The user row is mutated with new cached totals and last_unlock_at.
        Caller is responsible for committing the session.
        """
        unlocks_out: list[UnlockEvent] = []
        session_out: SessionStartedEvent | None = None
        milestones_out: list[PointsMilestoneEvent] = []

        recent = await self._ra.get_user_recent_achievements(
            user.ra_username, minutes=self._lookback
        )

        latest_unlock_game: tuple[int, str, str, str] | None = None
        if recent:
            # RA returns newest-first; process oldest-first so timestamps make sense.
            recent = sorted(recent, key=lambda a: a.date)
            async with get_session() as db:
                # Bulk-fetch already-seen rows in one query.
                pairs = [(a.achievement_id, bool(a.hardcore_mode)) for a in recent]
                ach_ids = [p[0] for p in pairs]
                existing = await db.scalars(
                    select(UnlockSeen).where(
                        UnlockSeen.ra_username == user.ra_username,
                        UnlockSeen.achievement_id.in_(ach_ids),
                    )
                )
                seen_set = {(u.achievement_id, u.hardcore) for u in existing.all()}

                for a in recent:
                    key = (a.achievement_id, a.is_hardcore)
                    if key in seen_set:
                        continue
                    db.add(
                        UnlockSeen(
                            ra_username=user.ra_username,
                            achievement_id=a.achievement_id,
                            hardcore=a.is_hardcore,
                            unlocked_at=a.date,
                        )
                    )
                    unlocks_out.append(UnlockEvent(a))
                    latest_unlock_game = (
                        a.game_id,
                        a.game_title,
                        a.console_name,
                        a.game_icon,
                    )
                    if user.last_unlock_at is None or a.date > user.last_unlock_at:
                        user.last_unlock_at = a.date
                await db.commit()

        # Pull a summary — used for both session detection (LastGame) and milestones.
        # We ask for 1 recent game so LastGame is populated even with no unlocks.
        summary = await self._ra.get_user_summary(user.ra_username, recent_games=1)

        # Session detection: prefer the most-recent unlock's game (authoritative),
        # otherwise fall back to summary.LastGame. This catches "started a game"
        # even when the user hasn't unlocked anything yet.
        current_game: tuple[int, str, str, str] | None = latest_unlock_game
        if current_game is None and summary is not None and summary.last_game:
            lg = summary.last_game
            gid = lg.get("ID")
            if gid is not None:
                current_game = (
                    int(gid),
                    lg.get("Title", "Unknown game"),
                    lg.get("ConsoleName", "") or "",
                    lg.get("ImageIcon", "") or "",
                )

        if current_game is not None:
            async with get_session() as db2:
                gs = await db2.get(GameSession, user.ra_username)
                first_time = gs is None
                if first_time or gs.game_id != current_game[0]:
                    if first_time:
                        db2.add(
                            GameSession(
                                ra_username=user.ra_username,
                                game_id=current_game[0],
                                started_at=datetime.now(timezone.utc),
                            )
                        )
                    else:
                        gs.game_id = current_game[0]
                        gs.started_at = datetime.now(timezone.utc)
                    await db2.commit()
                    # Seed silently on first-ever poll (no prior context = not "started now").
                    if not first_time:
                        session_out = SessionStartedEvent(*current_game)
                        # Treat a new session as an activity signal so next poll is 30s,
                        # not 15min. Useful when the user has no Discord presence.
                        user.last_presence_active_at = datetime.now(timezone.utc)

        if summary is not None:
            for kind, old, new in (
                ("points_hc", user.total_points, summary.total_points),
                ("points_sc", user.total_softcore_points, summary.total_softcore_points),
                ("retropoints", user.total_retro_points, summary.total_true_points),
            ):
                for t in _new_thresholds_crossed(old, new, POINTS_THRESHOLDS):
                    # Dedupe across restarts.
                    async with get_session() as db3:
                        exists = await db3.scalar(
                            select(MilestoneSeen).where(
                                MilestoneSeen.ra_username == user.ra_username,
                                MilestoneSeen.kind == kind,
                                MilestoneSeen.value == t,
                            )
                        )
                        if exists is None:
                            db3.add(
                                MilestoneSeen(
                                    ra_username=user.ra_username, kind=kind, value=t
                                )
                            )
                            await db3.commit()
                            milestones_out.append(PointsMilestoneEvent(kind, t, new))
            user.total_points = summary.total_points
            user.total_softcore_points = summary.total_softcore_points
            user.total_retro_points = summary.total_true_points

        return unlocks_out, session_out, milestones_out

    async def detect_awards_for_user(
        self, user: User, *, seed_only: bool = False
    ) -> list[AwardEvent]:
        """Slower-cadence check for mastery / completion / beaten awards.

        When `seed_only` is True (first poll for a freshly-linked user) we record
        every existing award without emitting events, so we don't spam the channel
        with the user's entire historical award list.
        """
        out: list[AwardEvent] = []
        awards = await self._ra.get_user_awards(user.ra_username)
        if awards is None:
            return out

        async with get_session() as db:
            for entry in awards.visible_user_awards:
                kind = _classify_award(entry)
                if kind is None or entry.award_data is None:
                    continue
                exists = await db.scalar(
                    select(AwardSeen).where(
                        AwardSeen.ra_username == user.ra_username,
                        AwardSeen.game_id == entry.award_data,
                        AwardSeen.kind == kind,
                    )
                )
                if exists is not None:
                    continue
                db.add(
                    AwardSeen(
                        ra_username=user.ra_username,
                        game_id=entry.award_data,
                        kind=kind,
                        awarded_at=entry.awarded_at,
                    )
                )
                if not seed_only:
                    out.append(AwardEvent(kind=kind, award=entry))
            await db.commit()

        return out
