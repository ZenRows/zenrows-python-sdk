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


# ----- duplicate detection (ENG-286) -----
#
# Batch does not deduplicate: identical URLs in one job are separate
# scrapes, separate charges, separate results. The estimate counts them
# so the caller sees it before the invoice does.


def test_no_duplicates_in_a_clean_list():
    est = estimate_cost(["https://a.example.com", "https://b.example.com"])
    assert est.duplicate_tasks == 0


def test_repeated_url_counts_as_redundant():
    est = estimate_cost(["https://a.example.com"] * 3)
    # All three are priced — nothing is collapsed.
    assert est.task_count == 3
    assert est.min == 3
    assert est.duplicate_tasks == 2


def test_external_id_and_metadata_do_not_distinguish_tasks():
    est = estimate_cost(
        [
            {"url": "https://a.example.com", "external_id": "row-1"},
            {"url": "https://a.example.com", "external_id": "row-2", "metadata": {"k": "v"}},
        ]
    )
    assert est.duplicate_tasks == 1


def test_urls_are_compared_exactly():
    est = estimate_cost(
        [
            "https://a.example.com/p",
            "https://a.example.com/p/",
            "https://a.example.com/p?x=1&y=2",
            "https://a.example.com/p?y=2&x=1",
        ]
    )
    assert est.duplicate_tasks == 0


def test_differing_task_params_keep_tasks_distinct():
    est = estimate_cost(
        [
            {"url": "https://a.example.com", "zenrows_params": {"proxy_country": "us"}},
            {"url": "https://a.example.com", "zenrows_params": {"proxy_country": "de"}},
        ]
    )
    assert est.duplicate_tasks == 0


def test_task_restating_the_job_default_duplicates_one_that_inherits_it():
    est = estimate_cost(
        [
            "https://a.example.com",
            {"url": "https://a.example.com", "zenrows_params": {"js_render": True}},
        ],
        zenrows_params={"js_render": "true"},
    )
    # Booleans and their string spellings are one value at the wire.
    assert est.duplicate_tasks == 1


def test_task_overriding_the_job_default_does_not():
    est = estimate_cost(
        [
            "https://a.example.com",
            {"url": "https://a.example.com", "zenrows_params": {"js_render": False}},
        ],
        zenrows_params={"js_render": "true"},
    )
    assert est.duplicate_tasks == 0


def test_post_body_distinguishes_tasks():
    same = estimate_cost(
        [
            {"url": "https://a.example.com", "method": "POST", "body": {"q": "shoes"}},
            {"url": "https://a.example.com", "method": "POST", "body": {"q": "shoes"}},
        ]
    )
    different = estimate_cost(
        [
            {"url": "https://a.example.com", "method": "POST", "body": {"q": "shoes"}},
            {"url": "https://a.example.com", "method": "POST", "body": {"q": "boots"}},
        ]
    )
    assert (same.duplicate_tasks, different.duplicate_tasks) == (1, 0)


def test_get_spellings_collapse_onto_the_default():
    est = estimate_cost(
        [
            "https://a.example.com",
            {"url": "https://a.example.com"},
            {"url": "https://a.example.com", "method": "get"},
        ]
    )
    assert est.duplicate_tasks == 2


def test_post_and_get_on_one_url_are_different_requests():
    est = estimate_cost(
        [
            {"url": "https://a.example.com"},
            {"url": "https://a.example.com", "method": "POST", "body": {"q": "shoes"}},
        ]
    )
    assert est.duplicate_tasks == 0


def test_taskinput_models_are_deduplicated_too():
    est = estimate_cost(
        [
            TaskInput(url="https://a.example.com", external_id="one"),
            TaskInput(url="https://a.example.com", external_id="two"),
            TaskInput(url="https://b.example.com"),
        ]
    )
    assert est.duplicate_tasks == 1


def test_format_names_duplicates_only_when_present():
    clean = estimate_cost(["https://a.example.com"])
    assert "duplicate" not in clean.format()

    dirty = estimate_cost(["https://a.example.com"] * 2)
    assert "1 duplicate task — scraped and charged separately" in dirty.format()


def test_client_estimate_cost_reports_duplicates():
    client = ZenRowsBatchClient(api_key="k")
    est = client.estimate_cost(
        {"tasks": [{"url": "https://a.example.com"}, {"url": "https://a.example.com"}]}
    )
    assert isinstance(est, CostEstimate)
    assert est.duplicate_tasks == 1
