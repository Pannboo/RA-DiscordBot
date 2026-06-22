from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.session import Base
from bot.db.types import UTCDateTime


class User(Base):
    """A Discord user linked to a RetroAchievements account."""

    __tablename__ = "users"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ra_username: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    linked_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    last_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    next_poll_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)

    # Activity signals for the adaptive scheduler.
    last_unlock_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_presence_active_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Cached state used for diffing milestones.
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    total_softcore_points: Mapped[int] = mapped_column(Integer, default=0)
    total_retro_points: Mapped[int] = mapped_column(Integer, default=0)

    # Last awards-check timestamp (mastery/beaten polled on a slower cadence).
    last_awards_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    # Authoritative award counts cached from RA's GetUserAwards. Seeded on link
    # and refreshed/reconciled on each awards poll so /ra top and /ra vs can read
    # them without a per-command API call.
    mastery_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completion_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    beaten_hardcore_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    beaten_softcore_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Per-user preferences.
    notify_hardcore_only: Mapped[bool] = mapped_column(Boolean, default=False)
    mention_in_posts: Mapped[bool] = mapped_column(Boolean, default=True)


class GuildConfig(Base):
    """Per-guild configuration set by a server admin."""

    __tablename__ = "guild_configs"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    announce_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # When True, /ra game replies are posted publicly in the channel
    # (still owner-locked for interactions) instead of ephemerally.
    game_lookup_public: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class UnlockSeen(Base):
    """Dedupe table for already-announced achievement unlocks."""

    __tablename__ = "unlocks_seen"
    __table_args__ = (
        UniqueConstraint("ra_username", "achievement_id", "hardcore", name="uq_unlock"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ra_username: Mapped[str] = mapped_column(String(64), index=True)
    achievement_id: Mapped[int] = mapped_column(Integer)
    hardcore: Mapped[bool] = mapped_column(Boolean)
    unlocked_at: Mapped[datetime] = mapped_column(UTCDateTime())


class GameSession(Base):
    """Tracks the user's currently-playing game so we can announce new sessions once."""

    __tablename__ = "game_sessions"

    ra_username: Mapped[str] = mapped_column(String(64), primary_key=True)
    game_id: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class AwardSeen(Base):
    """Dedupe table for mastery / completion / beaten award announcements."""

    __tablename__ = "awards_seen"
    __table_args__ = (
        UniqueConstraint("ra_username", "game_id", "kind", name="uq_award"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ra_username: Mapped[str] = mapped_column(String(64), index=True)
    game_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))  # mastered, completed, beaten_hc, beaten_sc
    awarded_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MilestoneSeen(Base):
    """Dedupe table for points / leaderboard milestones."""

    __tablename__ = "milestones_seen"
    __table_args__ = (
        UniqueConstraint("ra_username", "kind", "value", name="uq_milestone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ra_username: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # points_hc, points_sc, retropoints
    value: Mapped[int] = mapped_column(Integer)
    awarded_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class AOTWState(Base):
    """Singleton row tracking which Achievement of the Week is currently announced.

    The refresh loop only broadcasts when the achievement ID changes, so this
    survives restarts without re-spamming the announce channels.
    """

    __tablename__ = "aotw_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    achievement_id: Mapped[int | None] = mapped_column(Integer)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AOTWPin(Base):
    """Per-guild record of the AOTW message we pinned, so we can unpin it on rotation."""

    __tablename__ = "aotw_pins"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    achievement_id: Mapped[int] = mapped_column(Integer)
