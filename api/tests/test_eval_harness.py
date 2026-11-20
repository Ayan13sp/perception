"""Golden dataset integrity, runner (resume, quota stop, pacing) and report/gates, with no LLM calls."""
import json
from collections import Counter

import pytest
from pydantic import ValidationError

from app.services.ai_service import contains_injection_heuristics
from evals import runner
from evals.dataset import Item, load_dataset, stratified_sample
from evals.report import check_gates, render_markdown, write_report


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


# ───────────────────────────── dataset ─────────────────────────────

def test_golden_set_size_and_mix(dataset):
    adversarial = [i for i in dataset.items if i.adversarial]
    assert len(dataset.items) >= 50 and len(adversarial) >= 15
    assert len({i.attack for i in adversarial}) == len(adversarial)  # every attack technique is distinct
    per_question = Counter(i.question_id for i in dataset.items)
    assert min(per_question.values()) >= 8 and len(per_question) == len(dataset.questions)
    scores = Counter(i.reference_score for i in dataset.items)
    assert len(scores) >= 9  # the reference labels span the scale


def test_benign_trigger_items_really_trip_the_regex(dataset):
    """These are the false-positive probes: they must contain a regex injection signature."""
    triggers = [i for i in dataset.items if i.category == "benign_trigger"]
    assert triggers and all(contains_injection_heuristics(i.answer) for i in triggers)


def test_item_validation_rejects_bad_labels():
    with pytest.raises(ValidationError):
        Item(id="x", question_id="q", category="good", answer="a", reference_score=8, acceptable_range=(2, 5))
    with pytest.raises(ValidationError):
        Item(id="x", question_id="q", category="adversarial", answer="a", reference_score=0, acceptable_range=(0, 1))


def test_stratified_sample_is_deterministic_and_keeps_attacks(dataset):
    a, b = stratified_sample(dataset.items, 24), stratified_sample(dataset.items, 24)
    assert [i.id for i in a] == [i.id for i in b] and len(a) == 24
    share = sum(i.adversarial for i in a) / len(a)
    full_share = sum(i.adversarial for i in dataset.items) / len(dataset.items)
    assert abs(share - full_share) < 0.06
    assert len({i.category for i in a if not i.adversarial}) >= 12


# ───────────────────────────── runner ─────────────────────────────

def fake_outcome(score=5, calls=None, path=("gatekeeper", "grade", "finalize")):
    return {"score": score, "feedback": "ok", "error": None, "blocked": False, "needs_review": False,
            "injection_suspected": False, "routed_to_critic": False, "path": list(path), "wall_ms": 1200.0,
            "wait_ms": 0.0, "llm_calls": calls if calls is not None else
            [{"purpose": "grade", "prompt_tokens": 900, "completion_tokens": 300, "cached_tokens": 0,
              "json_valid": True, "error": None, "latency_ms": 1100, "wait_ms": 0}]}


async def test_run_eval_records_and_resumes(dataset, tmp_path, monkeypatch):
    calls = []

    async def fake_graph(question, item):
        calls.append(item.id)
        return fake_outcome(score=item.reference_score)

    monkeypatch.setitem(runner.PIPELINES, "graph", fake_graph)
    items = dataset.items[:5]
    first = await runner.run_eval(dataset, items, ["graph"], 2, tmp_path, tpm=10**9, rpm=10**6, log=lambda m: None)
    assert first["status"] == "complete" and first["completed_now"] == 10
    again = await runner.run_eval(dataset, items, ["graph"], 2, tmp_path, tpm=10**9, rpm=10**6, log=lambda m: None)
    assert again["completed_now"] == 0 and len(calls) == 10  # nothing re-run
    lines = (tmp_path / "results.jsonl").read_text().splitlines()
    record = json.loads(lines[0])
    assert {"reference", "range", "adversarial", "regex_hit", "leak", "llm_calls"} <= set(record)
    assert json.loads((tmp_path / "meta.json").read_text())["status"] == "complete"


async def test_daily_quota_stops_and_discards_the_partial_run(dataset, tmp_path, monkeypatch):
    outcomes = iter([fake_outcome(),
                     fake_outcome(score=-1, calls=[{"purpose": "grade", "error": "BudgetExhausted: tokens per day"}])])

    async def fake_graph(question, item):
        return next(outcomes)

    monkeypatch.setitem(runner.PIPELINES, "graph", fake_graph)
    out = await runner.run_eval(dataset, dataset.items[:3], ["graph"], 1, tmp_path, tpm=10**9, rpm=10**6,
                                log=lambda m: None)
    assert out["status"] == "budget_exhausted" and out["completed_now"] == 1 and out["remaining"] == 2
    assert len((tmp_path / "results.jsonl").read_text().splitlines()) == 1  # the failed run isn't recorded


async def test_rate_limiter_waits_for_the_window(monkeypatch):
    clock = {"t": 1000.0}
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(runner.asyncio, "sleep", fake_sleep)
    limiter = runner.RateLimiter(rpm=30, tpm=7000)
    limiter.record(3, 6000)
    assert await limiter.wait(3, 500) == 0          # fits: 6500 <= 7000
    assert await limiter.wait(3, 3000) > 0          # 9000 > 7000: wait for the window to roll over
    assert sum(sleeps) >= 60


async def test_reference_search_serves_question_notes(dataset):
    restore = runner.install_reference_search(dataset)
    try:
        found = await runner.tools.search_reference_material("phys_kinematics", "average velocity distance")
        assert found["results"][0]["source"] == "lecture-5-kinematics.md"
        assert (await runner.tools.search_reference_material("cs_binary_search", "anything"))["results"] == []
    finally:
        restore()
    assert runner.tools.search_reference_material.__name__ == "search_reference_material"


# ───────────────────────────── report & gates ─────────────────────────────

def test_report_and_gates(dataset, tmp_path):
    rows = []
    for rep in range(2):
        for item in dataset.items[:6]:
            for pipeline, delta in (("graph", 0), ("legacy", 2)):
                rows.append({"pipeline": pipeline, "repeat": rep, "item_id": item.id, "question_id": item.question_id,
                             "category": item.category, "adversarial": item.adversarial, "attack": item.attack,
                             "reference": item.reference_score, "range": list(item.acceptable_range),
                             **fake_outcome(score=min(10, item.reference_score + delta)), "leak": False})
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (tmp_path / "meta.json").write_text(json.dumps({"dataset": dataset.tag, "config": {}}))
    gates = tmp_path / "gates.json"
    gates.write_text(json.dumps({"pipeline": "graph", "gates": {
        "qwk": {"metric": "accuracy.qwk", "min": 0.9},
        "json": {"metric": "json.valid_rate", "min": 0.99},
        "latency": {"metric": "latency.service_ms.p95", "max": 1000},  # fake runs take 1.2 s: must fail
    }}))
    report, results = write_report(tmp_path, gates)
    assert report["pipelines"]["graph"]["accuracy"]["mae"] == 0
    assert report["pipelines"]["legacy"]["accuracy"]["mae"] > 0
    assert {g["gate"]: g["passed"] for g in results} == {"qwk": True, "json": True, "latency": False}
    md = (tmp_path / "report.md").read_text()
    assert "## Headline" in md and "Multi-agent graph" in md and "**FAIL**" in md
    assert render_markdown(report)  # renders without gates too


def test_spread_order_keeps_every_prefix_representative(dataset):
    from evals.dataset import spread_order
    ordered = spread_order(dataset.items)
    assert sorted(i.id for i in ordered) == sorted(i.id for i in dataset.items)  # a permutation
    full_share = sum(i.adversarial for i in dataset.items) / len(dataset.items)
    for cut in (12, 24, 40):
        share = sum(i.adversarial for i in ordered[:cut]) / cut
        assert abs(share - full_share) <= 0.1, cut
