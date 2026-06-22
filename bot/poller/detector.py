from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from bot.db import get_session
from bot.db.models import AwardSeen, GameSession, MilestoneSeen, UnlockSeen, User
from bot.ra.client import RAClient
from bot.ra.types import AwardEntry, RecentAchievement, UserAwards

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
    # None when the user just switched to this game; an int when they're picking
    # the same game back up after being away from it for that many minutes.
    # Used by the dispatcher to filter per-guild against `replay_announce_after_minutes`.
    replay_gap_minutes: int | None = None


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


def apply_award_counts(user: User, awards: UserAwards) -> None:
    """Cache RA's authoritative award counts on the User row.

    These are the numbers RA shows on a user's profile and are what `/ra top`
    and `/ra vs` read, so they stay correct even when individual awards are
    hidden from `VisibleUserAwards`.
    """
    user.mastery_count = awards.mastery_awards_count
    user.completion_count = awards.completion_awards_count
    user.beaten_hardcore_count = awards.beaten_hardcore_awards_count
    user.beaten_softcore_count = awards.beaten_softcore_awards_count


async def _reconcile_awards_seen(
    db, ra_username: str, awards: UserAwards, *, emit: bool
) -> list[AwardEvent]:
    """Insert any visible awards we haven't recorded yet into `awards_seen`.

    Returns an `AwardEvent` for each newly-recorded award when `emit` is True;
    during the initial seed (`emit=False`) we record silently so we don't spam
    the channel with a user's entire back-catalogue. The caller commits.
    """
    out: list[AwardEvent] = []
    for entry in awards.visible_user_awards:
        kind = _classify_award(entry)
        if kind is None or entry.award_data is None:
            continue
        exists = await db.scalar(
            select(AwardSeen).where(
                AwardSeen.ra_username == ra_username,
                AwardSeen.game_id == entry.award_data,
                AwardSeen.kind == kind,
            )
        )
        if exists is not None:
            continue
        db.add(
            AwardSeen(
                ra_username=ra_username,
                game_id=entry.award_data,
                kind=kind,
                awarded_at=entry.awarded_at,
            )
        )
        if emit:
            out.append(AwardEvent(kind=kind, award=entry))
    return out


async def seed_awards_on_link(ra: RAClient, discord_id: int) -> None:
    """Populate the awards cache immediately after a user links.

    Pulls `GetUserAwards` once, caches the authoritative counts and seeds the
    `awards_seen` rows silently. Best-effort: if the fetch fails we just leave
    it for the next awards poll to reconcile.
    """
    async with get_session() as db:
        user = await db.get(User, discord_id)
        ra_username = user.ra_username if user is not None else None
    if ra_username is None:
        return

    awards = await ra.get_user_awards(ra_username)
    if awards is None:
        return

    async with get_session() as db:
        user = await db.get(User, discord_id)
        if user is None:
            return
        apply_award_counts(user, awards)
        await _reconcile_awards_seen(db, user.ra_username, awards, emit=False)
        user.last_awards_polled_at = datetime.now(timezone.utc)
        await db.commit()


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
            now = datetime.now(timezone.utc)
            # We only trust "user is on this game right now" when we have fresh
            # unlock evidence for it — RA's summary.LastGame is sticky and stays
            # set long after a user has stopped playing, so it can't anchor
            # "they came back to it".
            has_fresh_unlock = bool(
                latest_unlock_game and latest_unlock_game[0] == current_game[0]
            )
            async with get_session() as db2:
                gs = await db2.get(GameSession, user.ra_username)
                first_time = gs is None
                if first_time:
                    db2.add(
                        GameSession(
                            ra_username=user.ra_username,
                            game_id=current_game[0],
                            started_at=now,
                            last_seen_at=now if has_fresh_unlock else None,
                        )
                    )
                    # Seed silently on first-ever poll (no prior context = not "started now").
                elif gs.game_id != current_game[0]:
                    gs.game_id = current_game[0]
                    gs.started_at = now
                    gs.last_seen_at = now if has_fresh_unlock else None
                    session_out = SessionStartedEvent(*current_game)
                    user.last_presence_active_at = now
                elif has_fresh_unlock:
                    # Same game and we have fresh activity. If we previously saw
                    # them on this game, the gap from then to now is how long
                    # they were away from it; the dispatcher filters per-guild.
                    prev_seen = gs.last_seen_at
                    gs.last_seen_at = now
                    if prev_seen is not None:
                        gap_minutes = int((now - prev_seen).total_seconds() // 60)
                        if gap_minutes >= 1:
                            gs.started_at = now
                            session_out = SessionStartedEvent(
                                *current_game, replay_gap_minutes=gap_minutes
                            )
                            user.last_presence_active_at = now
                await db2.commit()

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
    ) -> tuple[list[AwardEvent], UserAwards | None]:
        """Slower-cadence check for mastery / completion / beaten awards.

        Returns the new `AwardEvent`s plus the raw `UserAwards` payload (or
        `None` if the fetch failed) so the caller can refresh the user's cached
        counts from the same response. When `seed_only` is True (first poll for
        a freshly-linked user) we record every existing award without emitting
        events, so we don't spam the channel with the user's entire history.
        """
        awards = await self._ra.get_user_awards(user.ra_username)
        if awards is None:
            return [], None

        async with get_session() as db:
            out = await _reconcile_awards_seen(
                db, user.ra_username, awards, emit=not seed_only
            )
            await db.commit()

        return out, awards
