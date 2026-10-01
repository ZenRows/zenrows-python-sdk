"""Open (extensible) enums for the generated Batch models.

The Batch API marks its response enums `x-extensible-enum: true`:
clients must accept values the spec does not list yet. A plain `Enum`
raises on an unknown value, so pydantic would reject the whole
response the day the server adds one.

`make generate` renders every enum in `models.py` through
`codegen/templates/Enum.jinja2`, which wires this module's
`open_enum_missing` in as the enum's `_missing_` hook. An unknown value
then resolves to a pseudo-member that:

- keeps the raw wire value in `.value` (so JSON serialization emits it);
- is named `UNKNOWN` and is not part of iteration / `len()`;
- is cached per (enum, value), so equal raw values give the same object,
  and it compares equal only to itself;
- is hashable and pickles back through the same lookup.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

UNKNOWN_NAME = "UNKNOWN"


def open_enum_missing(cls: type[Enum], value: Any) -> Enum | None:
    """`Enum._missing_` hook: return an `UNKNOWN` pseudo-member for `value`."""
    if value is None:
        # pydantic-core's JSON path probes `_missing_(None)` before retrying
        # with the real input; answering it would swallow the raw value.
        # None is never a wire enum value (nullable fields are `X | None`).
        return None
    try:
        hash(value)
    except TypeError:
        return None  # unhashable — let Enum raise its normal ValueError
    member = object.__new__(cls)
    member._name_ = UNKNOWN_NAME
    member._value_ = value
    # Cache so `cls(value) is cls(value)`. Not added to `_member_map_`,
    # so the pseudo-member never shows up when iterating the enum.
    cls._value2member_map_.setdefault(value, member)
    return cls._value2member_map_[value]


def is_unknown(member: Enum) -> bool:
    """True when `member` is a value the SDK's spec did not list."""
    return member._name_ == UNKNOWN_NAME and member not in type(member).__members__.values()
