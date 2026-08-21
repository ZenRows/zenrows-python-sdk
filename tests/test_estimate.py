"""Cost estimation (SPEC §8.1) — pure, client-side, no network."""

import pytest

from zenrows.batch import (
    CostEstimate,
    Tier,
    ZenRowsBatchClient,
)

# Internal pricing helpers — no public function surface; estimation is
# reached via `client.estimate_cost` / `client.estimate_job`. These tests
# reach the private primitives to cover the rate card directly.
from zenrows.batch._estimate import _cost_for_params as cost_for_params
from zenrows.batch._estimate import _estimate_cost as estimate_cost
from zenrows.batch.models import TaskInput

# ----- single-task pricing (the rate card) -----


@pytest.mark.parametrize(
    ("params", "tier", "lo", "hi"),
    [
        ({}, Tier.BASE, 1, 1),
        ({"js_render": "true"}, Tier.JS, 5, 5),
        ({"premium_proxy": "true"}, Tier.PREMIUM, 10, 10),
        ({"js_render": "true", "premium_proxy": "true"}, Tier.JS_AND_PREMIUM, 25, 25),
        ({"mode": "auto"}, Tier.AUTO, 1, 25),
    ],
)
def test_cost_for_params_rate_card(params, tier, lo, hi):
    tc = cost_for_params(params)
    assert tc.tier is tier
    assert (tc.min, tc.max) == (lo, hi)
    assert tc.exact == (lo == hi)


@pytest.mark.parametrize("value", [True, "true", "True", " TRUE ", 1, "1", "yes", "on"])
def test_truthy_spellings_turn_on_a_flag(value):
    assert cost_for_params({"js_render": value}).tier is Tier.JS


@pytest.mark.parametrize("value", [False, "false", "0", 0, "", "no"])
def test_falsy_spellings_keep_base(value):
    assert cost_for_params({"js_render": value}).tier is Tier.BASE


def test_auto_wins_over_explicit_flags():
    # Server rejects this combo at submit (mutually exclusive), but if
    # it slips through, auto is what the engine honors.
    tc = cost_for_params({"mode": "auto", "js_render": "true", "premium_proxy": "true"})
    assert tc.tier is Tier.AUTO
    assert (tc.min, tc.max) == (1, 25)


# ----- job aggregation -----


def test_empty_job_is_zero():
    est = estimate_cost([])
    assert (est.task_count, est.min, est.max) == (0, 0, 0)
    assert est.exact
    assert est.breakdown == ()


def test_all_base_is_exact():
    est = estimate_cost(["https://a", "https://b", "https://c"])
    assert (est.min, est.max) == (3, 3)
    assert est.exact
    assert est.auto_tasks == 0


def test_job_level_params_apply_to_every_task():
    est = estimate_cost(["https://a", "https://b"], zenrows_params={"premium_proxy": True})
    assert (est.min, est.max) == (20, 20)
    assert est.breakdown[0].tier is Tier.PREMIUM
    assert est.breakdown[0].count == 2


def test_task_params_override_job_params():
    # Job says premium (10); one task overrides to plain base (1).
    est = estimate_cost(
        [
            {"url": "https://a", "zenrows_params": {"premium_proxy": False}},
            {"url": "https://b"},
        ],
        zenrows_params={"premium_proxy": True},
    )
    assert (est.min, est.max) == (1 + 10, 1 + 10)
    tiers = {line.tier for line in est.breakdown}
    assert tiers == {Tier.BASE, Tier.PREMIUM}


def test_auto_drives_the_range():
    est = estimate_cost([{"url": "https://a", "zenrows_params": {"mode": "auto"}}] * 50)
    assert (est.min, est.max) == (50, 1250)
    assert not est.exact
    assert est.auto_tasks == 50
    # width is exactly 24 x auto_tasks
    assert est.max - est.min == 24 * est.auto_tasks


def test_mixed_breakdown_sums_and_orders():
    tasks = (
        ["https://base1", "https://base2"]  # 2 x base
        + [{"url": "https://js", "zenrows_params": {"js_render": "true"}}]  # 1 x js
        + [{"url": f"https://auto{i}", "zenrows_params": {"mode": "auto"}} for i in range(3)]
    )
    est = estimate_cost(tasks)
    assert est.task_count == 6
    # min: 2*1 + 1*5 + 3*1 = 10 ; max: 2*1 + 1*5 + 3*25 = 82
    assert (est.min, est.max) == (10, 82)
    # breakdown renders in tier order: base, js, auto
    assert [line.tier for line in est.breakdown] == [Tier.BASE, Tier.JS, Tier.AUTO]
    base, js, auto = est.breakdown
    assert (base.count, base.subtotal_min, base.subtotal_max) == (2, 2, 2)
    assert (js.count, js.subtotal_min, js.subtotal_max) == (1, 5, 5)
    assert (auto.count, auto.subtotal_min, auto.subtotal_max) == (3, 3, 75)


def test_taskinput_model_input_supported():
    tasks = [
        TaskInput(url="https://a", zenrows_params={"js_render": True}),
        TaskInput(url="https://b"),
    ]
    est = estimate_cost(tasks)
    assert (est.min, est.max) == (6, 6)


# ----- presentation -----


def test_str_and_format():
    est = estimate_cost(["https://a", {"url": "https://b", "zenrows_params": {"mode": "auto"}}])
    assert str(est) == "2-26 credits (2 tasks)"
    out = est.format()
    assert "2 tasks → 2-26 credits" in out
    assert "base" in out and "auto" in out


# ----- client convenience (no network) -----


def test_client_estimate_cost_is_offline():
    client = ZenRowsBatchClient(api_key="test-key")
    est = client.estimate_cost(
        {
            "type": "regular",
            "status": "closed",
            "zenrows_params": {"js_render": "true"},
            "tasks": [{"url": "https://a"}, {"url": "https://b"}],
        }
    )
    assert isinstance(est, CostEstimate)
    assert (est.min, est.max) == (10, 10)


def test_client_estimate_cost_file_input_is_zero():
    client = ZenRowsBatchClient(api_key="test-key")
    est = client.estimate_cost({"type": "regular", "status": "closed", "file_input_id": "01HKE..."})
    assert (est.task_count, est.min, est.max) == (0, 0, 0)
