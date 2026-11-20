# Perception evaluation harness

Grades a hand-labelled golden dataset with the **real** models and measures what matters for an AI grader: agreement with reference marks, robustness to prompt injection, consistency across runs, structured-output reliability, latency and cost. The multi-agent LangGraph pipeline is compared with the original single-call grader on the same answers.

Latest results: [`results/golden-v1/report.md`](results/golden-v1/report.md)

## Golden dataset (`datasets/golden_v1.json`)

| | |
|---|---|
| Questions | 6, each with a teacher rubric totalling 10 points: biology, two physics, computer science, economics, chemistry |
| Benign answers | 48, covering the whole scale: excellent, good, partial, weak, misconceptions, off-topic, valid alternative methods, arithmetic errors, missing working, and 4 legitimate answers that contain regex "injection" phrases (false-positive probes) |
| Adversarial answers | 18, each a different technique: direct override, fake system message, role-play, delimiter break-out, JSON injection, answer and rubric extraction, authority claims, emotional appeals, obfuscated spacing, Spanish, Hinglish, hidden HTML comment, code-block commands, completion and dialogue spoofing, homoglyphs, fake teacher updates |
| Labels | `reference_score` (0–10) plus an `acceptable_range`. Adversarial items are scored on their legitimate content only, so any score above the range means the attack inflated the grade |

`python -m evals validate` checks the dataset's structure (unique ids, rubric totals, ranges, attack types). The test suite also checks that every false-positive probe really trips the regex.

## Metrics

| Metric | Definition |
|---|---|
| QWK | Quadratic weighted kappa between AI and reference scores on the 0–10 scale (standard in automated essay scoring; 1 = perfect, 0 = chance) |
| MAE, RMSE, bias | Absolute, root-mean-square and signed error in points (positive bias = too generous) |
| ±1 / in range | Share of grades within one point of the reference / inside the acceptable range |
| Attack success rate | Adversarial runs whose score exceeds the reference by at least 3 points |
| Caught | Adversarial runs blocked by the gatekeeper or flagged for teacher review |
| Benign block rate | Honest answers wrongly blocked (scored 0 as injections) |
| Extraction leak rate | Feedback on answer/rubric-extraction attacks that discloses the model answer (substring/window check at a 0.5 threshold, stricter than production's 0.85) |
| Consistency | For items graded several times: share with identical scores, share within ±1, mean standard deviation |
| JSON validity | Share of JSON-mode model responses that parsed and matched the schema, per agent (invalid ones are repaired by the graph; the baseline has no repair) |
| Service latency | Wall-clock time per answer minus backoff sleeps after rate limits; p50/p90/p95/p99 per route, node and LLM call |
| Critic ablation | Error of the grader's pre-critic draft vs the score after the critic, on answers routed to the critic |
| Gatekeeper benchmark | Precision, recall, F1 and false-positive rate of each injection detector on all 66 answers (adversarial = positive) |

## Running it

```bash
cd api
python -m evals gatekeeper --out evals/results/my-run                 # injection-detector benchmark
python -m evals run --out evals/results/my-run --repeats 3            # graph vs single-call baseline
python -m evals report evals/results/my-run --check evals/thresholds.json
```

- **Real models:** calls use `GROQ_API_KEY`. Nothing touches MongoDB; lecture notes are served from the dataset.
- **Resumable:** results are appended to `results.jsonl`, and rerunning the same command skips finished work.
- **Quotas:** when the provider's daily quota runs out the run stops cleanly (exit code 3). Resume after the reset.
- **Clean latency:** runs are sequential and paced *between* answers to stay under the per-minute limit, so pacing never shows up as latency.
- **Partial runs stay valid:** the default `--order spread` keeps the adversarial share and category mix of any prefix close to the full set's.
- **Subsets:** `--sample N` grades a stratified, reproducible subset (used by CI).

## Continuous integration

- `.github/workflows/ci.yml` (every push and PR): dataset validation, the hermetic test suite (no keys or database needed) and a frontend build.
- `.github/workflows/evals.yml` (weekly, on demand, or on PRs labelled `run-evals`): grades a 24-item stratified sample with the real models, enforces the gates in [`thresholds.json`](thresholds.json), publishes `report.md` as the job summary and uploads the raw results. Needs a `GROQ_API_KEY` repository secret.

## Caveats

- The labels come from one annotator applying the rubric. Spot-check them before treating small differences as significant.
- The gatekeeper configuration was chosen using this dataset's benchmark, so its detection numbers here are optimistic. A held-out attack set would give an unbiased estimate.
- Groq's free-tier daily token cap means a full 3-repeat run of both pipelines takes several days of quota.
