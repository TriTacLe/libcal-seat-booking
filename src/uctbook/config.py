from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, field_validator, model_validator

from uctbook.rules import Rules, _hhmm

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class Want(BaseModel):
    weekdays: list[str] = ["mon", "tue", "wed", "thu", "fri", "sat"]
    earliest_start: str = "09:00"
    latest_end: str = "17:00"
    minutes: int = 240
    min_minutes: int = 120
    step_minutes: int = 15

    @field_validator("weekdays")
    @classmethod
    def check_days(cls, v: list[str]) -> list[str]:
        bad = [d for d in v if d.lower() not in WEEKDAYS]
        if bad:
            raise ValueError(f"unknown weekday(s): {', '.join(bad)}")
        return [d.lower() for d in v]

    @field_validator("earliest_start", "latest_end")
    @classmethod
    def check_time(cls, v: str) -> str:
        _hhmm(v)
        return v

    @model_validator(mode="after")
    def check_minutes(self) -> "Want":
        if self.min_minutes > self.minutes:
            raise ValueError("min_minutes cannot exceed minutes")
        if _hhmm(self.earliest_start) >= _hhmm(self.latest_end):
            raise ValueError("earliest_start is not before latest_end")
        return self

    @property
    def earliest_start_time(self) -> time:
        return _hhmm(self.earliest_start)

    @property
    def latest_end_time(self) -> time:
        return _hhmm(self.latest_end)

    def allows(self, weekday: int) -> bool:
        return weekday in {WEEKDAYS[d] for d in self.weekdays}


class ItemPrefs(BaseModel):
    prefer: list[str] = []
    exclude: list[str] = []


class TargetSpec(BaseModel):
    name: str
    enabled: bool = True
    priority: int = 1
    location: str
    zone: str | None = None
    category: str | None = None
    capacity: str | None = None
    filters: list[int] = []
    rules: Rules = Rules()
    want: Want = Want()
    items: ItemPrefs = ItemPrefs()

    @model_validator(mode="after")
    def check_against_rules(self) -> "TargetSpec":
        if self.want.minutes > self.rules.max_minutes_per_day:
            raise ValueError(
                f"target '{self.name}' wants {self.want.minutes} minutes but the rules "
                f"cap a day at {self.rules.max_minutes_per_day}"
            )
        for field in ("minutes", "min_minutes", "step_minutes"):
            value = getattr(self.want, field)
            if value % self.rules.slot_minutes:
                raise ValueError(f"want.{field} must be a multiple of {self.rules.slot_minutes}")
        return self


class Account(BaseModel):
    username: str = ""


class CheckinConfig(BaseModel):
    # Off by default. Checking in for a seat you are not sitting at holds it for
    # the full booking instead of releasing it after 15 minutes.
    enabled: bool = False
    offset_minutes: int = 1


class GmailConfig(BaseModel):
    to: str = ""
    from_addr: str = ""
    app_password: str = ""
    extra_to: list[str] = []

    def resolve(self) -> tuple[str, str, str]:
        to = self.to or os.getenv("GMAIL_TO", "")
        from_addr = self.from_addr or os.getenv("GMAIL_FROM", to)
        app_password = self.app_password or os.getenv("GMAIL_APP_PASSWORD", "")
        return to, from_addr, app_password

    def all_recipients(self) -> list[str]:
        primary = self.to or os.getenv("GMAIL_TO", "")
        recipients = [primary] if primary else []
        for addr in self.extra_to:
            if addr not in recipients:
                recipients.append(addr)
        return recipients


class NotificationsConfig(BaseModel):
    gmail: GmailConfig | None = None


class Config(BaseModel):
    timezone: str = "Africa/Johannesburg"
    catalogue: Path = Path("data/catalogue.yaml")
    state: Path = Path("data/state.json")
    account: Account = Account()
    targets: list[TargetSpec]
    checkin: CheckinConfig = CheckinConfig()
    notifications: NotificationsConfig = NotificationsConfig()

    @field_validator("timezone")
    @classmethod
    def check_tz(cls, v: str) -> str:
        ZoneInfo(v)
        return v

    @model_validator(mode="after")
    def check_targets(self) -> "Config":
        names = [t.name for t in self.targets]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate target name(s): {', '.join(sorted(dupes))}")
        if not any(t.enabled for t in self.targets):
            raise ValueError("no enabled targets")
        return self

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def target(self, name: str) -> TargetSpec:
        for spec in self.targets:
            if spec.name == name:
                return spec
        known = ", ".join(t.name for t in self.targets)
        raise KeyError(f"No target named '{name}'. Config has: {known}")

    def active(self) -> list[TargetSpec]:
        return sorted((t for t in self.targets if t.enabled), key=lambda t: t.priority)


def load_config(path: Path) -> Config:
    return Config.model_validate(yaml.safe_load(path.read_text()))
