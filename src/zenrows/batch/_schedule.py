"""Pythonic schedule builders for `submit_scheduled`.

The wire format is a structured dict (`JobScheduleDict`) — three
mutually exclusive shapes (`at` / `rate` / `calendar`) plus an
optional `timezone`. The dict form is fine for power users; everyone
else gets these typed classes:

    from zenrows.batch import At, Rate, Calendar, Weekly
    from datetime import datetime

    client.submit_scheduled(
        At(datetime(2026, 9, 1, 9, 0), timezone="Europe/Berlin"),
        urls=["https://example.com/once"],
    )
    client.submit_scheduled(
        Rate(every=15, unit="minute"),
        urls=["https://example.com/poll"],
    )
    client.submit_scheduled(
        Calendar(
            times_of_day=["09:00", "18:00"],
            cadence=Weekly(days=["mon", "wed", "fri"]),
            timezone="Europe/Berlin",
        ),
        urls=["https://example.com/recurring"],
    )

Each class validates its inputs in `__post_init__`. The same rules
the server enforces — naive `at`, full-hour `times_of_day`, day
name spelling, valid IANA timezone — fail fast in Python rather
than round-tripping through a 400.

The low-level `client.submit_job({...})` path stays a pure
passthrough: callers who hand-build a dict bypass these checks and
rely on the server's response for error reporting.
"""

from __future__ import annotations

import zoneinfo
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Union

if TYPE_CHECKING:
    from ._typed_dicts import JobScheduleDict

# Public alias for type-checkers and docs.
Schedule = Union["At", "Rate", "Calendar"]


_DAYS_OF_WEEK = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _validate_timezone(tz: str, field: str) -> None:
    """Reject empty or non-IANA timezones."""
    if not tz:
        raise ValueError(f"{field} is required (IANA name, e.g. `Europe/Berlin`)")
    try:
        zoneinfo.ZoneInfo(tz)
    except zoneinfo.ZoneInfoNotFoundError as e:
        raise ValueError(f"{field}: {tz!r} is not a valid IANA timezone") from e


def _validate_full_hour(s: str) -> None:
    """Accept only `HH:00` (24h, full hours)."""
    if len(s) != 5 or s[2] != ":" or s[3:] != "00":
        raise ValueError(
            f"times_of_day entry {s!r} must be on the hour (`HH:00`); "
            "minute granularity is rejected."
        )
    try:
        h = int(s[:2])
    except ValueError as e:
        raise ValueError(f"times_of_day entry {s!r} has a non-numeric hour") from e
    if not 0 <= h <= 23:
        raise ValueError(f"times_of_day entry {s!r} is not a valid hour (00..23)")


@dataclass
class At:
    """One-shot fire at a specific wall-clock time.

    `at` accepts either a tz-naive `datetime` (no tzinfo) or a
    tz-naive ISO string (`"2026-09-01T09:00:00"`). Aware datetimes
    and offset-bearing strings are rejected — `timezone` is the
    single authoritative interpreter, which keeps DST transitions
    deterministic.
    """

    at: str | datetime
    timezone: str

    def __post_init__(self) -> None:
        _validate_timezone(self.timezone, "At.timezone")
        if isinstance(self.at, datetime):
            if self.at.tzinfo is not None and self.at.utcoffset() is not None:
                raise ValueError(
                    "At.at must be a naive datetime (no tzinfo); supply "
                    "timezone separately to keep DST transitions deterministic."
                )
        elif isinstance(self.at, str):
            if not self.at.strip():
                raise ValueError("At.at must be a non-empty ISO timestamp string.")
            if _has_tz_suffix(self.at):
                raise ValueError(
                    f"At.at {self.at!r} must be tz-naive (no `Z`, no offset). "
                    "Supply timezone separately to keep DST transitions deterministic."
                )
        else:
            raise TypeError(f"At.at must be a string or datetime, got {type(self.at).__name__}")

    def to_dict(self) -> "JobScheduleDict":
        at_str = self.at.strftime("%Y-%m-%dT%H:%M:%S") if isinstance(self.at, datetime) else self.at
        return {"at": at_str, "timezone": self.timezone}


@dataclass
class Rate:
    """Interval-based fire policy — every N units, no alignment to
    wall clock. Timezone is irrelevant and not accepted here."""

    every: int
    unit: Literal["minute", "hour", "day"]

    def __post_init__(self) -> None:
        if not isinstance(self.every, int) or isinstance(self.every, bool):
            raise TypeError(f"Rate.every must be int, got {type(self.every).__name__}")
        if self.every < 1:
            raise ValueError(f"Rate.every must be >= 1, got {self.every}")
        if self.unit not in ("minute", "hour", "day"):
            raise ValueError(f"Rate.unit must be one of `minute`, `hour`, `day`; got {self.unit!r}")

    def to_dict(self) -> "JobScheduleDict":
        return {"rate": {"every": self.every, "unit": self.unit}}


@dataclass
class Daily:
    """Fire every day. No knobs."""


@dataclass
class Weekly:
    """Fire on specific days of the week.

    `days` is a list of lower-case 3-letter day names — at least one,
    deduplicated server-side.
    """

    days: list[str]

    def __post_init__(self) -> None:
        if not self.days:
            raise ValueError("Weekly.days must be non-empty")
        for d in self.days:
            if d not in _DAYS_OF_WEEK:
                raise ValueError(
                    f"Weekly.days entry {d!r} is not a valid day "
                    f"(use one of {', '.join(_DAYS_OF_WEEK)})"
                )


@dataclass
class Monthly:
    """Fire on specific days of the month.

    `days` is a list of integers 1-31. Days that don't exist in a
    given month (e.g. 31 in April) are silently skipped by the
    scheduler.
    """

    days: list[int]

    def __post_init__(self) -> None:
        if not self.days:
            raise ValueError("Monthly.days must be non-empty")
        for d in self.days:
            if not isinstance(d, int) or isinstance(d, bool):
                raise TypeError(f"Monthly.days entries must be int, got {type(d).__name__}")
            if not 1 <= d <= 31:
                raise ValueError(f"Monthly.days entry {d} is out of range (1..31)")


Cadence = Daily | Weekly | Monthly


@dataclass
class Calendar:
    """Calendar-style fire policy: a list of times-of-day on a
    daily / weekly / monthly cadence.

    `times_of_day` are full-hour 24h strings (`"09:00"`, not
    `"09:30"`). `cadence` is exactly one of `Daily()`, `Weekly(...)`,
    or `Monthly(...)`. `timezone` is mandatory (IANA name).
    """

    times_of_day: list[str]
    cadence: Cadence
    timezone: str

    def __post_init__(self) -> None:
        _validate_timezone(self.timezone, "Calendar.timezone")
        if not self.times_of_day:
            raise ValueError("Calendar.times_of_day must be non-empty")
        for t in self.times_of_day:
            _validate_full_hour(t)
        if not isinstance(self.cadence, (Daily, Weekly, Monthly)):
            raise TypeError(
                f"Calendar.cadence must be Daily/Weekly/Monthly, got {type(self.cadence).__name__}"
            )

    def to_dict(self) -> "JobScheduleDict":
        cadence_dict: dict
        if isinstance(self.cadence, Daily):
            cadence_dict = {"daily": {}}
        elif isinstance(self.cadence, Weekly):
            cadence_dict = {"weekly": {"days": list(self.cadence.days)}}
        elif isinstance(self.cadence, Monthly):
            cadence_dict = {"monthly": {"days": list(self.cadence.days)}}
        else:  # pragma: no cover — caught in __post_init__
            raise TypeError(f"unknown cadence: {type(self.cadence).__name__}")
        return {
            "calendar": {
                "times_of_day": list(self.times_of_day),
                "cadence": cadence_dict,
            },
            "timezone": self.timezone,
        }


def _has_tz_suffix(raw: str) -> bool:
    """Return True if `raw` carries an RFC 3339-style tz tail (`Z` /
    `+HH:MM` / `-HH:MM`)."""
    raw = raw.strip()
    # Find the time portion (after T or space).
    for sep in ("T", " "):
        i = raw.find(sep)
        if i >= 0:
            tail = raw[i + 1 :]
            if tail.endswith(("Z", "z")):
                return True
            # `+HH:MM` or `-HH:MM` at the end (6 chars).
            return bool(len(tail) >= 6 and tail[-6] in "+-")
    return False


__all__ = [
    "At",
    "Cadence",
    "Calendar",
    "Daily",
    "Monthly",
    "Rate",
    "Schedule",
    "Weekly",
]
