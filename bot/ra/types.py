from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse a datetime field from the RA API.

    The API is inconsistent across endpoints: some return the legacy
    "YYYY-MM-DD HH:MM:SS" (naive UTC), others ISO 8601 already with a
    timezone (e.g. "2026-05-17T13:42:37+00:00" or trailing "Z").
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    s = value.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Naive ISO (no tz info) -> assume UTC.
    has_tz = "+" in s[10:] or s[10:].count("-") > 0
    if not has_tz:
        s = s + "+00:00"
    return datetime.fromisoformat(s)


class RABase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class RecentAchievement(RABase):
    achievement_id: int = Field(alias="AchievementID")
    title: str = Field(alias="Title")
    description: str = Field(alias="Description")
    points: int = Field(alias="Points")
    true_ratio: int = Field(default=0, alias="TrueRatio")
    badge_name: str = Field(default="", alias="BadgeName")
    game_id: int = Field(alias="GameID")
    game_title: str = Field(alias="GameTitle")
    game_icon: str = Field(default="", alias="GameIcon")
    console_name: str = Field(default="", alias="ConsoleName")
    hardcore_mode: int = Field(default=0, alias="HardcoreMode")
    date: datetime = Field(alias="Date")
    type: str | None = Field(default=None, alias="Type")  # e.g. "progression", "win_condition"

    @field_validator("date", mode="before")
    @classmethod
    def _parse_date(cls, v):  # noqa: ANN001
        return _parse_dt(v)

    @property
    def is_hardcore(self) -> bool:
        return bool(self.hardcore_mode)


class UserSummary(RABase):
    user: str = Field(alias="User")
    total_points: int = Field(default=0, alias="TotalPoints")
    total_softcore_points: int = Field(default=0, alias="TotalSoftcorePoints")
    total_true_points: int = Field(default=0, alias="TotalTruePoints")
    rank: int | None = Field(default=None, alias="Rank")
    last_game_id: int | None = Field(default=None, alias="LastGameID")
    rich_presence_msg: str | None = Field(default=None, alias="RichPresenceMsg")
    member_since: datetime | None = Field(default=None, alias="MemberSince")
    user_pic: str | None = Field(default=None, alias="UserPic")
    motto: str | None = Field(default=None, alias="Motto")
    last_game: dict | None = Field(default=None, alias="LastGame")

    @field_validator("member_since", mode="before")
    @classmethod
    def _parse_member_since(cls, v):  # noqa: ANN001
        return _parse_dt(v)


class AwardEntry(RABase):
    awarded_at: datetime = Field(alias="AwardedAt")
    award_type: str = Field(alias="AwardType")  # "Mastery/Completion", "Game Beaten", etc.
    award_data: int | None = Field(default=None, alias="AwardData")  # game id when applicable
    award_data_extra: int | None = Field(default=None, alias="AwardDataExtra")  # 1=HC, 0=SC
    title: str | None = Field(default=None, alias="Title")
    console_name: str | None = Field(default=None, alias="ConsoleName")
    image_icon: str | None = Field(default=None, alias="ImageIcon")

    @field_validator("awarded_at", mode="before")
    @classmethod
    def _parse_awarded(cls, v):  # noqa: ANN001
        return _parse_dt(v)

    @property
    def is_hardcore(self) -> bool:
        return self.award_data_extra == 1


class UserAwards(RABase):
    total_awards_count: int = Field(default=0, alias="TotalAwardsCount")
    hidden_awards_count: int = Field(default=0, alias="HiddenAwardsCount")
    mastery_awards_count: int = Field(default=0, alias="MasteryAwardsCount")
    completion_awards_count: int = Field(default=0, alias="CompletionAwardsCount")
    beaten_hardcore_awards_count: int = Field(default=0, alias="BeatenHardcoreAwardsCount")
    beaten_softcore_awards_count: int = Field(default=0, alias="BeatenSoftcoreAwardsCount")
    visible_user_awards: list[AwardEntry] = Field(default_factory=list, alias="VisibleUserAwards")


class GameInfoProgress(RABase):
    game_id: int = Field(alias="ID")
    title: str = Field(alias="Title")
    console_name: str = Field(default="", alias="ConsoleName")
    image_icon: str = Field(default="", alias="ImageIcon")
    num_achievements: int = Field(default=0, alias="NumAchievements")
    num_awarded_to_user: int = Field(default=0, alias="NumAwardedToUser")
    num_awarded_to_user_hardcore: int = Field(default=0, alias="NumAwardedToUserHardcore")
    user_completion: str | None = Field(default=None, alias="UserCompletion")
    user_completion_hardcore: str | None = Field(default=None, alias="UserCompletionHardcore")
