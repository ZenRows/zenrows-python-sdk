"""Client-side cost estimation for Batch jobs.

The Batch API prices a scrape per **successful request**, driven by two
scraper knobs (`js_render`, `premium_proxy`) plus the dynamic
`mode=auto`. The rate card is small, stable, and identical for every
caller, so estimation lives client-side rather than behind a network
round-trip — there is intentionally no `POST /v1/jobs/estimate`
endpoint.

What an estimate answers: *if every URL succeeds exactly once, what
is the charge?* It is **not** a consumption forecast — it ignores
failures, `retries`, and `reruns`, which affect realized usage but
not the per-success price. Realized cost is whatever teller bills
post-factum; this is advisory.

The math is a sum of per-task intervals:

    task in `mode=auto`        → [1, 25]   (dynamic, billed post-factum)
    otherwise, by merged flags → exact 1 | 5 | 10 | 25

    job.min = Σ taskᵢ.min      job.max = Σ taskᵢ.max

An estimate is *exact* (`min == max`) iff it contains no auto tasks;
the interval width is exactly `24 x (number of auto tasks)`.

`mode=auto` and the explicit `js_render` / `premium_proxy` flags are
mutually exclusive at submit, so every task sits in
exactly one tier. If a malformed param map carries both, `auto`
wins here (it's what the engine would honor) — but the server would
reject that body at submit anyway.

The per-task `method` / `body` fields (POST tasks) do NOT affect the
rate card — pricing is driven by the flags above regardless of HTTP
method, and the render-tier combinations the platform can't execute
for POST (`js_render`, `js_instructions`, `json_response`) are
rejected at submit, so a priced job is a billable job.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from zenrows.batch.models import TaskInput

# Credits charged per *successful* request, by configuration.
BASE_CREDITS = 1
JS_CREDITS = 5
PREMIUM_PROXY_CREDITS = 10
JS_AND_PROXY_CREDITS = 25
# `mode=auto` is charged dynamically post-factum, anywhere in this range.
AUTO_MIN_CREDITS = 1
AUTO_MAX_CREDITS = 25

# Param values arrive as str | bool | int. These spellings
# count as "on" for the boolean flags.
_TRUTHY = frozenset({"true", "1", "yes", "on"})

# What `Tier` keys can appear, in the order a breakdown should render.
ParamValue = str | bool | int | dict[str, str]  # dict form: `custom_headers` map
ParamMap = dict[str, ParamValue]
TaskLike = str | TaskInput | dict


class Tier(str, Enum):
    """The pricing tier a task falls into. Exactly one per task."""

    BASE = "base"
    JS = "js_render"
    PREMIUM = "premium_proxy"
    JS_AND_PREMIUM = "js_render+premium_proxy"
    AUTO = "auto"


# Stable render order for breakdown lines.
_TIER_ORDER = (
    Tier.BASE,
    Tier.JS,
    Tier.PREMIUM,
    Tier.JS_AND_PREMIUM,
    Tier.AUTO,
)


@dataclass(slots=True, frozen=True)
class TaskCost:
    """The credit interval for a single task. `min == max` for every
    tier except `auto`."""

    tier: Tier
    min: int
    max: int

    @property
    def exact(self) -> bool:
        return self.min == self.max


@dataclass(slots=True, frozen=True)
class CostLine:
    """One row of a breakdown: all tasks sharing a tier, aggregated."""

    tier: Tier
    count: int
    unit_min: int
    unit_max: int

    @property
    def subtotal_min(self) -> int:
        return self.count * self.unit_min

    @property
    def subtotal_max(self) -> int:
        return self.count * self.unit_max

    @property
    def exact(self) -> bool:
        return self.unit_min == self.unit_max


@dataclass(slots=True, frozen=True)
class CostEstimate:
    """Result of `client.estimate_cost`. Credits assuming every task succeeds
    once. `min == max` (`exact`) when no task uses `mode=auto`.

    Designed-for-later (not built today): money pricing. Credits are
    the only unit now. Money would layer in as `money = credits x
    price_per_credit`, where the per-credit price is plan-dependent and
    not part of the static rate card. The natural, non-breaking
    extension is additive fields — e.g. an optional `money_min` /
    `money_max` (or a nested `money` object) on this class and a
    matching subtotal on `CostLine` — populated only once a per-credit
    price is supplied. Nothing here is renamed to a credit-specific
    name precisely so that addition reads naturally."""

    task_count: int
    min: int
    max: int
    breakdown: tuple[CostLine, ...]

    @property
    def exact(self) -> bool:
        """True when the charge is a single number (no auto tasks)."""
        return self.min == self.max

    @property
    def auto_tasks(self) -> int:
        """How many tasks use `mode=auto` — the only source of range."""
        return sum(line.count for line in self.breakdown if line.tier is Tier.AUTO)

    def __str__(self) -> str:
        credits = f"{self.min}" if self.exact else f"{self.min}-{self.max}"
        return f"{credits} credits ({self.task_count} tasks)"

    def format(self) -> str:
        """Multi-line breakdown table, e.g.::

        1000 tasks → 4800-6000 credits
           950 x base (1)     =    950
            50 x auto (1-25)  = 50-1250
        """
        head = f"{self.task_count} tasks → {self}"
        lines = [head]
        for line in self.breakdown:
            unit = f"{line.unit_min}" if line.exact else f"{line.unit_min}-{line.unit_max}"
            sub = (
                f"{line.subtotal_min}" if line.exact else f"{line.subtotal_min}-{line.subtotal_max}"
            )
            lines.append(f"  {line.count:>6} x {line.tier.value} ({unit}) = {sub}")
        return "\n".join(lines)


def _truthy(value: ParamValue | None) -> bool:
    """Coerce a scraper-param scalar to a boolean. Booleans pass
    through; ints are nonzero-truthy; strings match the on-spellings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False


def _is_auto(params: ParamMap) -> bool:
    return str(params.get("mode", "")).strip().lower() == "auto"


def _cost_for_params(params: ParamMap) -> TaskCost:
    """Price one task from its **merged** scraper params (job-level
    overlaid with per-task, task wins)."""
    if _is_auto(params):
        return TaskCost(Tier.AUTO, AUTO_MIN_CREDITS, AUTO_MAX_CREDITS)
    js = _truthy(params.get("js_render"))
    px = _truthy(params.get("premium_proxy"))
    if js and px:
        return TaskCost(Tier.JS_AND_PREMIUM, JS_AND_PROXY_CREDITS, JS_AND_PROXY_CREDITS)
    if px:
        return TaskCost(Tier.PREMIUM, PREMIUM_PROXY_CREDITS, PREMIUM_PROXY_CREDITS)
    if js:
        return TaskCost(Tier.JS, JS_CREDITS, JS_CREDITS)
    return TaskCost(Tier.BASE, BASE_CREDITS, BASE_CREDITS)


def _task_params(task: TaskLike) -> ParamMap:
    """Pull per-task `zenrows_params` from any accepted task shape."""
    if isinstance(task, str):
        return {}
    if isinstance(task, TaskInput):
        return dict(task.zenrows_params or {})
    if isinstance(task, dict):
        return dict(task.get("zenrows_params") or {})
    raise TypeError(f"unsupported task type for estimation: {type(task).__name__}")


def _estimate_cost(
    tasks: Iterable[TaskLike],
    *,
    zenrows_params: ParamMap | None = None,
) -> CostEstimate:
    """Estimate the credit cost of a job, assuming every task succeeds
    once. Pure and offline — no network call.

    `tasks` is the same shape `submit_regular` accepts: bare URL
    strings, ``TaskInput`` models, or task dicts. Per-task
    ``zenrows_params`` override the job-level ``zenrows_params`` on key
    collision (task wins), matching the worker's merge.

    Returns a `CostEstimate` with `min`/`max` credits and a per-tier
    `breakdown`. `min == max` (``.exact``) when no task uses
    ``mode=auto``.

    Note: `file_input` (CSV) jobs can't be estimated this way — the
    row count isn't known client-side. Estimate from the in-memory
    task list, or count the rows yourself first.
    """
    job_params = dict(zenrows_params or {})
    # tier -> [count, unit_min, unit_max]
    agg: dict[Tier, list[int]] = {}
    total_min = 0
    total_max = 0
    count = 0
    for task in tasks:
        count += 1
        merged = {**job_params, **_task_params(task)}
        tc = _cost_for_params(merged)
        total_min += tc.min
        total_max += tc.max
        if tc.tier in agg:
            agg[tc.tier][0] += 1
        else:
            agg[tc.tier] = [1, tc.min, tc.max]

    breakdown = tuple(
        CostLine(tier, agg[tier][0], agg[tier][1], agg[tier][2])
        for tier in _TIER_ORDER
        if tier in agg
    )
    return CostEstimate(task_count=count, min=total_min, max=total_max, breakdown=breakdown)


__all__ = [
    "AUTO_MAX_CREDITS",
    "AUTO_MIN_CREDITS",
    "BASE_CREDITS",
    "JS_AND_PROXY_CREDITS",
    "JS_CREDITS",
    "PREMIUM_PROXY_CREDITS",
    "CostEstimate",
    "CostLine",
    "TaskCost",
    "Tier",
]
