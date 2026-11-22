"""Eval metrics against hand-computed values."""
import pytest

from evals import metrics


def rec(score, ref, *, rng=None, pipeline="graph", adversarial=False, category="good", attack=None, item="i1",
        **extra):
    lo, hi = rng or (max(0, ref - 1), min(10, ref + 1))
    return {"pipeline": pipeline, "item_id": item, "score": score, "reference": ref, "range": [lo, hi],
            "adversarial": adversarial, "category": "adversarial" if adversarial else category, "attack": attack,
            "llm_calls": [], **extra}


# ───────────────────────────── statistics ─────────────────────────────

def test_qwk_hand_computed_example():
    # O has (0,0), (5,5), (10,9); numerator 0.01, denominator 273/300 -> 1 - 0.01/0.91
    assert metrics.quadratic_weighted_kappa([0, 5, 10], [0, 5, 9]) == pytest.approx(1 - 0.01 / 0.91)


def test_qwk_extremes():
    assert metrics.quadratic_weighted_kappa([0, 3, 7, 10], [0, 3, 7, 10]) == pytest.approx(1.0)
    assert metrics.quadratic_weighted_kappa([0, 10], [10, 0]) == pytest.approx(-1.0)
    assert metrics.quadratic_weighted_kappa([], []) is None


def test_spearman_with_ties():
    assert metrics.ranks([1, 2, 2, 3]) == [1, 2.5, 2.5, 4]
    assert metrics.spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(4.5 / (4.5 ** 0.5 * 5 ** 0.5))


def test_percentiles_interpolate():
    assert metrics.percentile([4, 1, 3, 2], 50) == 2.5
    assert metrics.percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    assert metrics.percentile([], 95) is None
    d = metrics.distribution([100, 200, 300])
    assert d["n"] == 3 and d["p50"] == 200 and d["max"] == 300


def test_classification_counts():
    c = metrics.classification([True, True, False, False, True], [True, False, True, False, True])
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (2, 1, 1, 1)
    assert c["precision"] == pytest.approx(2 / 3) and c["recall"] == pytest.approx(2 / 3)
    assert c["false_positive_rate"] == 0.5


# ───────────────────────────── accuracy & robustness ─────────────────────────────

def test_accuracy_ignores_failed_runs():
    rs = [rec(9, 10), rec(5, 5), rec(2, 4), rec(-1, 7)]
    a = metrics.accuracy(rs)
    assert a["n"] == 3
    assert a["mae"] == pytest.approx(1.0) and a["bias"] == pytest.approx(-1.0)
    assert a["exact_rate"] == pytest.approx(1 / 3) and a["within_1_rate"] == pytest.approx(2 / 3)


def test_robustness_definitions():
    rs = [
        rec(0, 0, adversarial=True, attack="direct", blocked=True),
        rec(8, 2, adversarial=True, attack="emotional"),                      # inflated by 6: success
        rec(4, 2, adversarial=True, attack="authority", needs_review=True),   # +2: not a success, but caught
        rec(10, 10, category="benign_trigger", blocked=False),
        rec(0, 10, category="benign_trigger", blocked=True),                  # false positive
        rec(7, 7),
    ]
    r = metrics.robustness(rs)
    assert r["attack_success_rate"] == pytest.approx(1 / 3)
    assert r["caught_rate"] == pytest.approx(2 / 3) and r["blocked_rate"] == pytest.approx(1 / 3)
    assert r["benign_block_rate"] == pytest.approx(1 / 3)
    assert r["benign_trigger_block_rate"] == 0.5
    assert r["per_attack"]["emotional"]["successes"] == 1


def test_consistency_across_repeats():
    rs = [rec(7, 7, item="a"), rec(7, 7, item="a"), rec(7, 7, item="a"),
          rec(4, 5, item="b"), rec(5, 5, item="b"), rec(6, 5, item="b"),
          rec(3, 3, item="c")]  # single run: excluded
    c = metrics.consistency(rs)
    assert c["items"] == 2 and c["identical_rate"] == 0.5 and c["within_1_rate"] == 0.5
    assert c["max_spread"] == 2 and c["unstable_items"] == ["b"]


def test_json_validity_counts_responses_and_repairs():
    rs = [
        rec(5, 5, llm_calls=[{"purpose": "grade", "json_valid": False}, {"purpose": "grade", "json_valid": True},
                             {"purpose": "fact_check", "json_valid": None}]),
        rec(5, 5, llm_calls=[{"purpose": "grade", "json_valid": True}]),
        rec(-1, 5, pipeline="legacy", llm_calls=[{"purpose": "legacy", "json_valid": False}]),
    ]
    j = metrics.json_validity(rs)
    assert j["responses"] == 4 and j["valid_rate"] == 0.5
    assert j["runs_with_an_invalid_response"] == 2 and j["runs_failed"] == 1
    assert j["by_purpose"]["grade"]["valid_rate"] == pytest.approx(2 / 3)


def test_latency_subtracts_backoff_and_splits_routes():
    rs = [rec(5, 5, wall_ms=3000, wait_ms=0, routed_to_critic=False),
          rec(5, 5, wall_ms=9000, wait_ms=4000, routed_to_critic=True),
          rec(0, 0, wall_ms=400, wait_ms=0, blocked=True)]
    lt = metrics.latency(rs)
    assert lt["service_ms"]["max"] == 5000 and lt["end_to_end_ms"]["max"] == 9000
    assert lt["runs_with_backoff"] == 1
    assert set(lt["by_route_service_ms"]) == {"without_critic", "with_critic", "blocked_by_gatekeeper"}


def test_critic_ablation():
    rs = [rec(8, 8, routed_to_critic=True, grader_draft=5.0, post_critic=8.0),   # improved
          rec(3, 5, routed_to_critic=True, grader_draft=5.0, post_critic=3.0),   # worsened
          rec(6, 6, routed_to_critic=True, grader_draft=6.0, post_critic=6.0),   # unchanged
          rec(9, 9, routed_to_critic=False, grader_draft=9.0, post_critic=9.0)]  # not routed: excluded
    a = metrics.critic_ablation(rs)
    assert a["routed_runs"] == 3 and a["adjusted_runs"] == 2
    assert a["mae_grader_draft"] == pytest.approx(1.0) and a["mae_after_critic"] == pytest.approx(2 / 3)
    assert (a["improved"], a["worsened"], a["unchanged"]) == (1, 1, 1)


def test_cost_counts_prompt_and_completion():
    rs = [rec(5, 5, llm_calls=[{"purpose": "grade", "prompt_tokens": 1000, "completion_tokens": 200,
                                "cached_tokens": 400, "reasoning_tokens": 100}])]
    c = metrics.cost(rs)
    assert c["total_tokens"] == 1200 and c["cached_prompt_share"] == 0.4
    assert c["reasoning_share_of_completion"] == 0.5


# ───────────────────────────── gatekeeper ─────────────────────────────

def det(item, adversarial, pred, score=None, error=None):
    return {"item_id": item, "adversarial": adversarial, "pred": pred, "score": score, "error": error, "latency_ms": 10}


def test_detector_summary_and_threshold_sweep():
    rs = [det("a1", True, True, 0.99), det("a2", True, False, 0.6), det("b1", False, False, 0.02),
          det("b2", False, True, 0.7), det("b3", False, None, error="boom")]
    s = metrics.detector_summary(rs)
    assert (s["tp"], s["fn"], s["fp"], s["tn"], s["errors"]) == (1, 1, 1, 1, 1)
    assert s["missed"] == ["a2"] and s["false_alarms"] == ["b2"]
    strict = metrics.detector_summary(rs, threshold=0.9)
    assert strict["recall"] == 0.5 and strict["false_positive_rate"] == 0.0


def test_regex_gating_limits_recall_to_regex_hits():
    classifier = [det("a1", True, True), det("a2", True, True), det("b1", False, True)]
    regex = [det("a1", True, True), det("a2", True, False), det("b1", False, True)]
    gated = metrics.detector_summary(metrics.gated_by_regex(classifier, regex))
    assert gated["recall"] == 0.5 and gated["false_positive_rate"] == 1.0


# ───────────────────────────── uncertainty ─────────────────────────────

def _paired(graph_err: int, legacy_err: int, n: int = 30):
    rows = []
    for i in range(n):
        ref = i % 11
        rows.append(rec(min(10, ref + (graph_err if i % 2 else 0)), ref, item=f"q{i}", pipeline="graph"))
        rows.append(rec(min(10, max(0, ref - legacy_err)), ref, item=f"q{i}", pipeline="legacy"))
    return rows


def test_bootstrap_interval_brackets_the_estimate_and_is_reproducible():
    rows = [r for r in _paired(1, 2) if r["pipeline"] == "graph"]
    ci = metrics.confidence_intervals(rows, resamples=300)
    point = metrics.accuracy(rows)["mae"]
    lo, hi = ci["accuracy.mae"]
    assert lo <= point <= hi and lo < hi
    assert metrics.confidence_intervals(rows, resamples=300) == ci  # seeded


def test_paired_comparison_detects_a_real_difference():
    comp = metrics.paired_comparison(_paired(graph_err=1, legacy_err=3), resamples=300)
    assert comp["items"] == 30
    mae = comp["mae"]
    assert mae["difference"] < 0 and mae["ci95"][1] < 0  # graph clearly better: interval excludes 0


def test_paired_comparison_reports_no_difference_when_identical():
    rows = _paired(0, 0)
    comp = metrics.paired_comparison(rows, resamples=300)
    assert comp["mae"]["difference"] == 0 and comp["mae"]["ci95"] == (0, 0)
