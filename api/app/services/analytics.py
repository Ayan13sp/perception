"""
Analytics over graded submissions.

The top half is pure functions over plain "records" (dicts), so the maths is
unit-tested without a database. The bottom half loads records through `repo`
after an access check.

Anything derived from a student's text is untrusted: answers are wrapped in
<untrusted_student_answer> tags before they leave the server, because they are
read by the teacher's AI assistant (indirect prompt injection).
"""
import math
import statistics
from datetime import datetime
from typing import Any, Optional

from app.db.database import QuestionSet, Submission, User
from app.services import access, repo
from app.services.ai_service import detect_model_answer_leak
from app.services.errors import NotFoundError
from app.services.exams import current_rubric
from app.services.grading.tools import search_reference_material

UNTRUSTED_TAG = "untrusted_student_answer"
WEAK_PCT = 70      # a criterion below this is a weak area for the student
STRONG_PCT = 90
PASS_RATIO = 0.5   # "below half" for the misconception report
MAX_STUDY_POINTERS = 5


# ───────────────────────────── helpers ─────────────────────────────

def wrap_untrusted(text: str, limit: int = 4000) -> str:
    body = (text or "")[:limit].replace(f"</{UNTRUSTED_TAG}>", f"</ {UNTRUSTED_TAG}>")
    return f"<{UNTRUSTED_TAG}>\n{body}\n</{UNTRUSTED_TAG}>"


def _mean(values: list[float]) -> Optional[float]:
    return round(statistics.fmean(values), 2) if values else None


def percentile(values: list[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile (pct in 0-100)."""
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = math.floor(k), math.ceil(k)
    value = ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)
    return round(value, 2)


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 3) if whole else None


def score_stats(scores: list[float]) -> dict:
    histogram = {str(i): 0 for i in range(11)}
    for s in scores:
        histogram[str(min(10, max(0, int(round(s)))))] += 1
    if not scores:
        return {"n": 0, "mean": None, "median": None, "stdev": None, "min": None, "max": None, "histogram": histogram}
    return {
        "n": len(scores),
        "mean": _mean(scores),
        "median": round(statistics.median(scores), 2),
        "stdev": round(statistics.stdev(scores), 2) if len(scores) > 1 else 0.0,
        "min": min(scores),
        "max": max(scores),
        "histogram": histogram,
    }


def is_graph_trace(trace: Any) -> bool:
    return isinstance(trace, dict) and trace.get("pipeline") == "graph"


def _sum_awarded(scores: list[dict]) -> Optional[float]:
    return round(sum(float(s.get("awarded", 0)) for s in scores), 2) if scores else None


def grader_draft(trace: dict) -> Optional[float]:
    """The primary grader's score before any critic adjustment."""
    if not is_graph_trace(trace):
        return None
    if trace.get("grader_draft_score") is not None:
        return float(trace["grader_draft_score"])
    verdict = trace.get("critic_verdict") or {}
    if verdict.get("draft_score") is not None:  # traces written before score snapshots existed
        return float(verdict["draft_score"])
    return _sum_awarded(trace.get("criterion_scores") or [])


def post_critic(trace: dict) -> Optional[float]:
    if not is_graph_trace(trace):
        return None
    if trace.get("post_critic_score") is not None:
        return float(trace["post_critic_score"])
    return _sum_awarded(trace.get("criterion_scores") or [])


def discrepancy(trace: dict) -> Optional[float]:
    """|grader draft − post-critic score|; 0 when the critic didn't run; None if unknown."""
    if not is_graph_trace(trace):
        return None
    if trace.get("score_discrepancy") is not None:
        return float(trace["score_discrepancy"])
    if not trace.get("routed_to_critic"):
        return 0.0 if trace.get("criterion_scores") else None
    g, p = grader_draft(trace), post_critic(trace)
    return round(abs(p - g), 2) if g is not None and p is not None else None


def criterion_deltas(trace: dict) -> list[dict]:
    """Per-criterion changes the critic made (needs the pre-critic snapshot)."""
    before = {c["criterion_id"]: c for c in trace.get("grader_criterion_scores") or []}
    deltas = []
    for after in trace.get("criterion_scores") or []:
        prior = before.get(after["criterion_id"])
        if prior and prior.get("awarded") != after.get("awarded"):
            deltas.append({
                "criterion_id": after["criterion_id"],
                "grader": prior.get("awarded"),
                "critic": after.get("awarded"),
                "max_points": after.get("max_points"),
                "critic_reason": (after.get("justification") or "")[:300],
            })
    return deltas


def effective_score(record: dict) -> Optional[float]:
    return record["final_score"] if record.get("final_score") is not None else record.get("ai_score")


# ───────────────────────────── records ─────────────────────────────

def submission_record(sub: Submission, usernames: dict, exam_titles: Optional[dict] = None) -> dict:
    student_id = repo.link_id(sub.student)
    exam_id = repo.link_id(sub.question_set)
    submitted_at: datetime = sub.id.generation_time  # ObjectIds embed their creation time
    return {
        "id": str(sub.id),
        "exam_id": str(exam_id) if exam_id else None,
        "exam_title": (exam_titles or {}).get(exam_id),
        "student_id": str(student_id) if student_id else None,
        "student_username": usernames.get(student_id, "unknown"),
        "student_answer": sub.student_answer,
        "ai_score": sub.ai_score,
        "ai_feedback": sub.ai_feedback,
        "final_score": sub.final_score,
        "trace": sub.grading_trace,
        "submitted_at": submitted_at,
        "regrade_count": len(getattr(sub, "regrade_history", None) or []),
    }


# ───────────────────────────── teacher analytics ─────────────────────────────

def exam_summary(exam: dict, records: list[dict]) -> dict:
    graded = [r for r in records if r.get("ai_score") is not None and r["ai_score"] >= 0]
    finalized = [r for r in graded if r.get("final_score") is not None]
    diffs = [abs(r["ai_score"] - r["final_score"]) for r in finalized]
    traces = [r["trace"] for r in records if is_graph_trace(r.get("trace"))]
    latencies = [t["total_latency_ms"] for t in traces if isinstance(t.get("total_latency_ms"), (int, float))]
    tokens = [t["total_tokens"] for t in traces if isinstance(t.get("total_tokens"), (int, float))]
    routed = [t for t in traces if t.get("routed_to_critic")]
    adjusted = [t for t in routed if (t.get("critic_verdict") or {}).get("verdict") == "adjust"]
    gaps = [d for d in (discrepancy(t) for t in routed) if d is not None]

    return {
        "exam": exam,
        "submissions": len(records),
        "finalized_by_teacher": len(finalized),
        "ai_scores": score_stats([r["ai_score"] for r in graded]),
        "final_scores": score_stats([r["final_score"] for r in finalized]),
        "ai_vs_teacher": {
            "n": len(diffs),
            "mean_abs_diff": _mean(diffs),
            "exact_match_rate": _rate(sum(1 for d in diffs if d == 0), len(diffs)),
            "within_one_point_rate": _rate(sum(1 for d in diffs if d <= 1), len(diffs)),
        },
        "grading_pipeline": {
            "graph_graded": len(traces),
            "legacy_or_untraced": len(records) - len(traces),
            "latency_ms": {
                "mean": _mean(latencies),
                "p50": percentile(latencies, 50),
                "p95": percentile(latencies, 95),
                "max": max(latencies) if latencies else None,
            },
            "mean_tokens": _mean(tokens),
            "critic_route_rate": _rate(len(routed), len(traces)),
            "critic_adjust_rate": _rate(len(adjusted), len(routed)),
            "mean_grader_critic_gap": _mean(gaps),
        },
        "flags": {
            "needs_review": sum(1 for t in traces if t.get("needs_review")),
            "injection_suspected": sum(1 for t in traces if t.get("injection_suspected")),
            "injection_blocked": sum(1 for t in traces if t.get("injection_flag")),
            "regraded_submissions": sum(1 for r in records if r.get("regrade_count")),
        },
    }


def flagged_submissions(
    records: list[dict],
    *,
    min_discrepancy: float = 1.0,
    include_needs_review: bool = True,
    limit: int = 20,
) -> list[dict]:
    flagged = []
    for r in records:
        trace = r.get("trace")
        if not is_graph_trace(trace):
            continue
        gap = discrepancy(trace)
        reasons = []
        if gap is not None and gap >= min_discrepancy:
            reasons.append(f"grader and critic disagree by {gap:g} points")
        if include_needs_review and trace.get("needs_review"):
            reasons.append("flagged for teacher review")
        if not reasons:
            continue
        if trace.get("injection_flag"):
            reasons.append("blocked as a prompt-injection attempt")
        elif trace.get("injection_suspected"):
            reasons.append("contains prompt-injection-like language")

        verdict = trace.get("critic_verdict") or {}
        flagged.append({
            "submission_id": r["id"],
            "student": r["student_username"],
            "ai_score": r["ai_score"],
            "final_score": r["final_score"],
            "grader_draft_score": grader_draft(trace),
            "post_critic_score": post_critic(trace),
            "discrepancy": gap,
            "reasons": reasons,
            "critic": {
                "verdict": verdict.get("verdict"),
                "issues": verdict.get("issues", []),
                "manipulation_detected": verdict.get("manipulation_detected", False),
            } if verdict else None,
            "criterion_changes": criterion_deltas(trace),
            "audit_notes": (trace.get("audit_notes") or [])[-5:],
            "regrade_count": r.get("regrade_count", 0),
            "student_answer": wrap_untrusted(r["student_answer"]),
        })

    flagged.sort(key=lambda f: (f["discrepancy"] or 0, "flagged for teacher review" in f["reasons"]), reverse=True)
    return flagged[:limit]


def _rubric_signature(criteria: list[dict]) -> set[tuple]:
    return {(c.get("id"), c.get("description")) for c in criteria or []}


def misconception_report(exam: dict, criteria: list[dict], records: list[dict], *, samples: int = 4) -> dict:
    if not criteria:
        return {"exam": exam, "submissions_analyzed": 0, "submissions_skipped": len(records), "criteria": [],
                "markdown": "", "note": "This exam has no rubric yet. It is generated with the first graded submission."}

    signature = _rubric_signature(criteria)
    usable = [
        r for r in records
        if is_graph_trace(r.get("trace"))
        and not r["trace"].get("injection_flag")  # blocked attempts aren't real answers
        and _rubric_signature(r["trace"].get("rubric_criteria")) == signature  # graded on this rubric
    ]

    rows = []
    for c in criteria:
        entries = []
        for r in usable:
            score = next((s for s in r["trace"].get("criterion_scores") or [] if s["criterion_id"] == c["id"]), None)
            if score and score.get("max_points"):
                entries.append((score["awarded"] / score["max_points"], score.get("justification") or "", r))
        row = {"criterion_id": c["id"], "description": c["description"], "max_points": c["points"], "n": len(entries)}
        if entries:
            ratios = [e[0] for e in entries]
            lowest = sorted(entries, key=lambda e: e[0])
            row.update({
                "mean_pct": round(100 * statistics.fmean(ratios), 1),
                "pct_students_below_half": round(100 * sum(1 for x in ratios if x < PASS_RATIO) / len(ratios), 1),
                "pct_full_marks": round(100 * sum(1 for x in ratios if x >= 0.999) / len(ratios), 1),
                # Grader notes on the weakest answers: the raw material for spotting misconceptions
                "grader_notes_from_low_scorers": [
                    {"student": e[2]["student_username"], "pct": round(100 * e[0]), "note": e[1][:300]}
                    for e in lowest[:samples] if e[0] < 0.75
                ],
            })
        rows.append(row)

    rows.sort(key=lambda row: row.get("mean_pct", 101))
    lines = ["| Criterion | Avg % | Below half | Full marks | n |", "|---|---|---|---|---|"]
    for row in rows:
        if row["n"]:
            desc = row["description"].replace("|", "/")[:80]
            lines.append(f"| {row['criterion_id']}: {desc} | {row['mean_pct']}% | "
                         f"{row['pct_students_below_half']}% | {row['pct_full_marks']}% | {row['n']} |")
    return {
        "exam": exam,
        "submissions_analyzed": len(usable),
        "submissions_skipped": len(records) - len(usable),
        "criteria": rows,  # weakest first
        "markdown": "\n".join(lines),
    }


# ───────────────────────────── student analytics ─────────────────────────────

def _slope(values: list[float]) -> Optional[float]:
    n = len(values)
    if n < 2:
        return None
    x_mean, y_mean = (n - 1) / 2, statistics.fmean(values)
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return round(num / den, 3)


def student_progress(records: list[dict]) -> dict:
    ordered = sorted(records, key=lambda r: r["submitted_at"])
    graded = [r for r in ordered if r.get("ai_score") is not None and r["ai_score"] >= 0]
    scores = [effective_score(r) for r in graded]
    slope = _slope(scores)
    if len(scores) < 3 or slope is None:
        trend = "not enough submissions yet"
    else:
        trend = "improving" if slope > 0.25 else "declining" if slope < -0.25 else "steady"

    def brief(r):
        return {"exam_id": r["exam_id"], "exam_title": r["exam_title"], "score": effective_score(r)}

    return {
        "submissions": len(ordered),
        "finalized_by_teacher": sum(1 for r in ordered if r.get("final_score") is not None),
        "average_score": _mean(scores),
        "recent_average_last_3": _mean(scores[-3:]),
        "trend": trend,
        "trend_points_per_submission": slope,
        "best": brief(max(graded, key=effective_score)) if graded else None,
        "lowest": brief(min(graded, key=effective_score)) if graded else None,
        "timeline": [
            {
                "exam_id": r["exam_id"],
                "exam_title": r["exam_title"],
                "submitted_at": r["submitted_at"].isoformat(),
                "ai_score": r["ai_score"],
                "final_score": r["final_score"],
                "score": effective_score(r),
                "status": "finalized" if r.get("final_score") is not None else "awaiting teacher review",
            }
            for r in ordered
        ],
    }


def submission_breakdown(record: dict, model_answer: str = "") -> dict:
    """
    What a student may see about one submission. The criterion breakdown is
    released only after the teacher finalizes the grade (rubric criteria are
    derived from the hidden model answer).
    """
    out = {
        "exam_id": record["exam_id"],
        "exam_title": record["exam_title"],
        "score": effective_score(record),
        "status": "finalized" if record.get("final_score") is not None else "awaiting teacher review",
        "feedback": record.get("ai_feedback"),
        "weak_areas": [],
        "strengths": [],
    }
    trace = record.get("trace")
    if record.get("final_score") is None:
        out["breakdown_note"] = "The per-criterion breakdown is released after your teacher finalizes this grade."
        return out
    if not is_graph_trace(trace) or not trace.get("criterion_scores"):
        out["breakdown_note"] = "No per-criterion breakdown is available for this submission."
        return out

    descriptions = {c["id"]: c["description"] for c in trace.get("rubric_criteria") or []}
    for s in trace["criterion_scores"]:
        if not s.get("max_points"):
            continue
        pct = round(100 * s["awarded"] / s["max_points"])
        reason = (s.get("justification") or "")[:400]
        if model_answer and detect_model_answer_leak(reason, model_answer):
            reason = "Review this concept in your class material."
        item = {"criterion": descriptions.get(s["criterion_id"], s["criterion_id"]), "awarded": s["awarded"],
                "max_points": s["max_points"], "pct": pct, "grader_reason": reason}
        if pct < WEAK_PCT:
            out["weak_areas"].append(item)
        elif pct >= STRONG_PCT:
            out["strengths"].append(item["criterion"])
    out["weak_areas"].sort(key=lambda i: i["pct"])
    return out


# ───────────────────────────── loaders (access-checked) ─────────────────────────────

def exam_ref(exam: QuestionSet) -> dict:
    return {"exam_id": str(exam.id), "title": exam.title}


async def _exam_records(exam: QuestionSet) -> list[dict]:
    subs = await repo.list_exam_submissions(exam.id)
    users = await repo.get_users(repo.link_id(s.student) for s in subs)
    names = {uid: u.username for uid, u in users.items()}
    return [submission_record(s, names, {exam.id: exam.title}) for s in subs]


async def get_exam_summary(teacher: User, exam_id: str) -> dict:
    exam = await access.get_teacher_exam(exam_id, teacher)
    return exam_summary(exam_ref(exam), await _exam_records(exam))


async def get_flagged_submissions(teacher: User, exam_id: str, *, min_discrepancy: float = 1.0,
                                  include_needs_review: bool = True, limit: int = 20) -> dict:
    exam = await access.get_teacher_exam(exam_id, teacher)
    items = flagged_submissions(await _exam_records(exam), min_discrepancy=max(0.0, min_discrepancy),
                                include_needs_review=include_needs_review, limit=max(1, min(limit, 100)))
    return {"exam": exam_ref(exam), "count": len(items), "submissions": items}


async def get_misconception_report(teacher: User, exam_id: str) -> dict:
    exam = await access.get_teacher_exam(exam_id, teacher)
    return misconception_report(exam_ref(exam), current_rubric(exam), await _exam_records(exam))


async def _student_records(student: User) -> tuple[list[dict], dict]:
    access.require_role(student, "student", "view their own progress")
    subs = await repo.list_student_submissions(student.id)
    exams = await repo.get_exams(repo.link_id(s.question_set) for s in subs)
    titles = {eid: e.title for eid, e in exams.items()}
    records = [submission_record(s, {student.id: student.username}, titles) for s in subs]
    return records, exams


async def get_student_progress(student: User) -> dict:
    records, _ = await _student_records(student)
    return student_progress(records)


async def get_improvement_plan(student: User, exam_id: Optional[str] = None) -> dict:
    records, exams = await _student_records(student)
    if exam_id:
        wanted = str(access.parse_object_id(exam_id, "question set"))
        records = [r for r in records if r["exam_id"] == wanted]
        if not records:
            raise NotFoundError("You have no submission for that exam.")

    by_id = {str(k): v for k, v in exams.items()}
    items = []
    for r in sorted(records, key=lambda r: r["submitted_at"], reverse=True):
        exam = by_id.get(r["exam_id"])
        items.append(submission_breakdown(r, exam.model_answer if exam else ""))

    # Study pointers: excerpts from notes the teacher shared, for the weakest areas
    budget = MAX_STUDY_POINTERS
    for item in items:
        exam = by_id.get(item["exam_id"])
        for area in item["weak_areas"]:
            if budget <= 0 or exam is None:
                break
            budget -= 1
            found = await search_reference_material(exam.id, area["criterion"], student_only=True)
            area["study_pointers"] = [
                {"source": hit["source"], "excerpt": hit["excerpt"][:500]} for hit in found["results"][:2]
            ]

    return {
        "progress": student_progress(records),
        "submissions": items,
        "guidance": "Turn the weak areas into 3-5 concrete practice steps. Cite the study pointers when present.",
    }
