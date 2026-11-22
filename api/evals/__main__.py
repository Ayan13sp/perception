"""
Perception evaluation harness.

    python -m evals validate
    python -m evals run --out evals/results/<name> [--pipelines graph,legacy] [--repeats 3] [--sample 24]
    python -m evals gatekeeper --out evals/results/<name> [--detectors regex,prompt-guard-86m,...]
    python -m evals report evals/results/<name> [--check evals/thresholds.json]

Runs call the real LLM provider (GROQ_API_KEY) and are resumable: rerunning the
same command skips work already recorded in the output directory.
"""
import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from evals.dataset import DEFAULT_DATASET, load_dataset, spread_order, stratified_sample

EXIT_GATES_FAILED = 1
EXIT_BUDGET = 3


def _items(args, dataset):
    items = dataset.items
    if getattr(args, "items", ""):
        wanted = {i.strip() for i in args.items.split(",") if i.strip()}
        items = [i for i in items if i.id in wanted]
    if getattr(args, "sample", 0):
        items = stratified_sample(items, args.sample, seed=args.seed)
    if getattr(args, "order", "dataset") == "spread":
        items = spread_order(items, seed=args.seed)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate the golden dataset")

    run = sub.add_parser("run", help="grade the golden set with the real pipelines")
    run.add_argument("--out", required=True)
    run.add_argument("--pipelines", default="graph,legacy")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--sample", type=int, default=0, help="stratified subset size (0 = all items)")
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--items", default="", help="comma-separated item ids")
    run.add_argument("--order", choices=["spread", "dataset"], default="spread",
                     help="spread: every prefix keeps the category mix, so a quota-stopped run stays representative")
    run.add_argument("--rpm", type=int, default=28, help="requests/minute budget for the grader model")
    run.add_argument("--tpm", type=int, default=7000, help="tokens/minute budget for the grader model")

    gk = sub.add_parser("gatekeeper", help="benchmark injection classifiers on the golden set")
    gk.add_argument("--out", required=True)
    gk.add_argument("--detectors", default="regex,prompt-guard-22m,prompt-guard-86m,gpt-oss-20b,gpt-oss-safeguard-20b")
    gk.add_argument("--sample", type=int, default=0)
    gk.add_argument("--seed", type=int, default=7)
    gk.add_argument("--items", default="")

    rep = sub.add_parser("report", help="write report.md / report.json for a results directory")
    rep.add_argument("run_dir")
    rep.add_argument("--check", default=None, help="quality-gate file; exit 1 if any gate fails")

    args = parser.parse_args()
    dataset = load_dataset(args.dataset)

    if args.command == "validate":
        adv = sum(i.adversarial for i in dataset.items)
        print(f"{dataset.tag}: {len(dataset.questions)} questions, {len(dataset.items)} items "
              f"({len(dataset.items) - adv} benign, {adv} adversarial)")
        print("categories:", dict(Counter(i.category for i in dataset.items)))
        return 0

    if args.command == "run":
        from evals.runner import run_eval
        items = _items(args, dataset)
        pipelines = [p.strip() for p in args.pipelines.split(",") if p.strip()]
        print(f"Grading {len(items)} items x {args.repeats} repeat(s) with {pipelines} -> {args.out}")
        outcome = asyncio.run(run_eval(dataset, items, pipelines, args.repeats, Path(args.out), rpm=args.rpm, tpm=args.tpm))
        print(outcome)
        return EXIT_BUDGET if outcome["status"] == "budget_exhausted" else 0

    if args.command == "gatekeeper":
        from evals.runner import run_gatekeeper_bench
        detectors = [d.strip() for d in args.detectors.split(",") if d.strip()]
        statuses = asyncio.run(run_gatekeeper_bench(dataset, _items(args, dataset), detectors, Path(args.out)))
        print(statuses)
        return EXIT_BUDGET if "budget_exhausted" in statuses.values() else 0

    if args.command == "report":
        from evals.report import write_report
        _, gates = write_report(Path(args.run_dir), Path(args.check) if args.check else None)
        print(f"Wrote {Path(args.run_dir) / 'report.md'}")
        if gates is not None:
            failed = [g for g in gates if not g["passed"]]
            for g in gates:
                print(f"  {'PASS' if g['passed'] else 'FAIL'}  {g['gate']:<28} {g['value']}")
            return EXIT_GATES_FAILED if failed else 0
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
