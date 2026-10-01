"""Contract: a Batch response never fails to parse because the server
added an enum value (docs/openapi.yaml marks response enums
`x-extensible-enum`).

Enums the spec marks extensible must carry the open-enum `_missing_` hook
(added by scripts/open_extensible_enums.py); every other enum must stay
strict. Both sets are derived from docs/openapi.yaml and the generated
module, so a regeneration that drops or over-applies the hook fails here.
"""

import enum
import inspect
import json
import pickle
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter

from zenrows.batch import models
from zenrows.batch._open_enum import is_unknown
from zenrows.batch.models import Job, Run

FUTURE = "some_future_value"

GENERATED_ENUMS = [
    obj
    for _, obj in inspect.getmembers(models, inspect.isclass)
    if issubclass(obj, enum.Enum) and obj.__module__ == models.__name__
]


SPEC = yaml.safe_load((Path(__file__).parent.parent / "docs" / "openapi.yaml").read_text())


def _extensible_value_sets(node, out: set) -> set:
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list) and node.get("x-extensible-enum") is True:
            out.add(frozenset(node["enum"]))
        for v in node.values():
            _extensible_value_sets(v, out)
    elif isinstance(node, list):
        for v in node:
            _extensible_value_sets(v, out)
    return out


EXTENSIBLE_SETS = _extensible_value_sets(SPEC, set())
EXTENSIBLE = [c for c in GENERATED_ENUMS if frozenset(m.value for m in c) in EXTENSIBLE_SETS]
STRICT = [c for c in GENERATED_ENUMS if c not in EXTENSIBLE]


def test_module_has_enums():
    # Guard against the iteration silently finding nothing.
    assert len(GENERATED_ENUMS) >= 17
    assert len(EXTENSIBLE) >= len(EXTENSIBLE_SETS) >= 9
    assert STRICT, "request-side enums must stay strict"
    assert models.FailureReason in EXTENSIBLE
    assert models.JobType in STRICT


@pytest.mark.parametrize("enum_cls", STRICT, ids=lambda c: c.__name__)
def test_non_extensible_enum_rejects_unknown_value(enum_cls):
    with pytest.raises(ValueError):
        enum_cls(FUTURE)
    with pytest.raises(ValueError):  # pydantic ValidationError is a ValueError
        TypeAdapter(enum_cls).validate_python(FUTURE)


def test_request_typo_still_fails_locally():
    with pytest.raises(ValueError):
        models.SubmitJobRequest.model_validate(
            {"type": "regulr", "tasks": [{"url": "https://example.com"}]}
        )


@pytest.mark.parametrize("enum_cls", EXTENSIBLE, ids=lambda c: c.__name__)
def test_unknown_value_parses_keeps_raw_value_and_round_trips(enum_cls):
    adapter = TypeAdapter(enum_cls)
    member = adapter.validate_python(FUTURE)
    assert isinstance(member, enum_cls)
    assert member.value == FUTURE
    assert member.name == "UNKNOWN"
    assert is_unknown(member)
    assert adapter.validate_json(json.dumps(FUTURE)) is member
    assert adapter.dump_json(member) == json.dumps(FUTURE).encode()
    assert adapter.dump_python(member, mode="json") == FUTURE
    # Equal only to itself; hashable; picklable; not listed as a member.
    assert all(member != known for known in enum_cls)
    assert member not in list(enum_cls)
    assert {member: 1}[enum_cls(FUTURE)] == 1
    assert pickle.loads(pickle.dumps(member)) is member


@pytest.mark.parametrize("enum_cls", GENERATED_ENUMS, ids=lambda c: c.__name__)
def test_known_values_still_map_to_members(enum_cls):
    adapter = TypeAdapter(enum_cls)
    for known in enum_cls:
        parsed = adapter.validate_python(known.value)
        assert parsed is known
        assert not is_unknown(parsed)


RUN = {
    "run_id": "01R000000000000000000A",
    "job_id": "01J000000000000000000A",
    "run_sequence": 1,
    "status": "failed",
    "stats": {"total": 10, "completed": 3, "successful": 3, "failed": 0},
    "created_at": "2026-09-30T10:00:00Z",
    "updated_at": "2026-09-30T10:05:00Z",
    "failure_reason": "some_future_reason",
}


def test_run_with_future_failure_reason_parses():
    run = Run.model_validate(RUN)
    assert run.failure_reason is not None
    assert run.failure_reason.value == "some_future_reason"
    assert json.loads(run.model_dump_json())["failure_reason"] == "some_future_reason"


def test_job_with_future_values_parses():
    job = Job.model_validate(
        {
            "job_id": "01J000000000000000000A",
            "type": "regular",
            "status": "some_future_status",
            "created_at": "2026-09-30T10:00:00Z",
            "updated_at": "2026-09-30T10:05:00Z",
            "latest_run": {**RUN, "status": "some_future_run_status"},
        }
    )
    assert job.status.value == "some_future_status"
    assert job.latest_run is not None
    assert job.latest_run.failure_reason is not None
    assert job.latest_run.failure_reason.value == "some_future_reason"
