"""
In-memory stand-in for `app.services.repo`, shared by service, route and MCP tests.

Documents are built with `model_construct` (Beanie refuses normal construction
before `init_beanie`), and links are stored as DBRefs, the way Beanie returns
them when loading from MongoDB.
"""
import os
import struct
from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from bson import DBRef

from app.db.database import ApiToken, QuestionSet, Submission, User
from app.services import analytics, evaluation, repo
from app.services.exams import model_answer_hash

MODEL_ANSWER = (
    "Photosynthesis converts light energy into chemical energy, producing glucose and oxygen in the chloroplast."
)
RUBRIC = [
    {"id": "C1", "description": "Light energy converted to chemical energy", "points": 5.0},
    {"id": "C2", "description": "Products are glucose and oxygen", "points": 5.0},
]


def oid_at(when: Optional[datetime] = None) -> PydanticObjectId:
    """A unique ObjectId whose embedded timestamp is `when` (ObjectId.from_datetime isn't unique)."""
    ts = int((when or datetime.now(timezone.utc)).timestamp())
    return PydanticObjectId(struct.pack(">I", ts) + os.urandom(8))


def _ref(collection: str, value) -> DBRef:
    return DBRef(collection, value.id if hasattr(value, "id") else value)


def make_trace(
    scores: list[float],
    *,
    criteria: list[dict] = RUBRIC,
    grader_scores: Optional[list[float]] = None,
    critic_verdict: Optional[str] = None,
    critic_issues: Optional[list[str]] = None,
    latency_ms: int = 1000,
    tokens: int = 500,
    needs_review: bool = False,
    injection_suspected: bool = False,
    injection_flag: bool = False,
    justification: str = "ok",
) -> dict:
    """A grading_trace shaped like run_grading_graph's output."""
    def scored(values):
        return [
            {"criterion_id": c["id"], "awarded": v, "max_points": c["points"],
             "justification": justification, "confidence": 0.9}
            for c, v in zip(criteria, values)
        ]

    final = scored(scores)
    grader = scored(grader_scores if grader_scores is not None else scores)
    routed = critic_verdict is not None
    grader_total, final_total = sum(c["awarded"] for c in grader), sum(c["awarded"] for c in final)
    return {
        "pipeline": "graph",
        "path": ["gatekeeper", "decompose_rubric", "grade"] + (["critic"] if routed else []) + ["finalize"],
        "total_latency_ms": latency_ms,
        "total_tokens": tokens,
        "rubric_criteria": criteria,
        "criterion_scores": final,
        "grader_criterion_scores": grader,
        "grader_draft_score": grader_total,
        "post_critic_score": final_total,
        "score_discrepancy": round(abs(final_total - grader_total), 2) if routed else 0.0,
        "routed_to_critic": routed,
        "critic_verdict": {"verdict": critic_verdict, "issues": critic_issues or [], "draft_score": grader_total,
                           "manipulation_detected": False} if routed else None,
        "needs_review": needs_review,
        "injection_suspected": injection_suspected,
        "injection_flag": injection_flag,
        "audit_notes": [],
    }


class FakeRepo:
    def __init__(self):
        self.users: dict = {}
        self.exams: dict = {}
        self.submissions: dict = {}
        self.tokens: dict = {}
        self.references: list[dict] = []
        self.graph_calls: list[dict] = []

    # ───────────── builders ─────────────

    def add_user(self, username: str, role: str) -> User:
        user = User.model_construct(id=oid_at(), username=username, role=role, email=None, hashed_password=None)
        self.users[user.id] = user
        return user

    def add_exam(self, teacher: Optional[User], *, title="Photosynthesis", question="Explain photosynthesis.",
                 model_answer=MODEL_ANSWER, assigned=(), rubric=None) -> QuestionSet:
        exam = QuestionSet.model_construct(
            id=oid_at(), title=title, question=question, model_answer=model_answer,
            creator=_ref("users", teacher) if teacher else None,
            assigned_students=[_ref("users", s) for s in assigned],
            rubric_criteria=rubric, rubric_hash=model_answer_hash(model_answer) if rubric else None,
        )
        self.exams[exam.id] = exam
        return exam

    def add_submission(self, exam: QuestionSet, student: User, *, ai_score=5, final_score=None, trace=None,
                       answer="Plants make food from light.", feedback="Good effort.",
                       at: Optional[datetime] = None) -> Submission:
        sub = Submission.model_construct(
            id=oid_at(at), question_set=_ref("question_sets", exam), student=_ref("users", student),
            student_answer=answer, ai_score=ai_score, ai_feedback=feedback, final_score=final_score,
            grading_trace=trace, regrade_history=[],
        )
        self.submissions[sub.id] = sub
        return sub

    def add_reference(self, exam: QuestionSet, source: str, content: str, student_visible: bool):
        self.references.append({"exam_id": exam.id, "source_name": source, "content": content,
                                "student_visible": student_visible})

    # ───────────── repo API ─────────────

    async def get_user(self, user_id):
        return self.users.get(user_id)

    async def get_users(self, user_ids):
        return {i: self.users[i] for i in user_ids if i in self.users}

    async def find_students_by_usernames(self, usernames):
        return [u for u in self.users.values() if u.username in usernames and u.role == "student"]

    async def get_exam(self, exam_id):
        return self.exams.get(exam_id)

    async def get_exams(self, exam_ids):
        return {i: self.exams[i] for i in exam_ids if i in self.exams}

    async def list_teacher_exams(self, teacher_id):
        return [e for e in self.exams.values() if repo.link_id(e.creator) == teacher_id]

    async def list_open_or_assigned_exams(self, student_id):
        return [e for e in self.exams.values()
                if not e.assigned_students or student_id in [repo.link_id(s) for s in e.assigned_students]]

    async def create_exam(self, **fields):
        fields["creator"] = _ref("users", fields["creator"])
        fields["assigned_students"] = [_ref("users", s) for s in fields.get("assigned_students") or []]
        exam = QuestionSet.model_construct(id=oid_at(), **fields)
        self.exams[exam.id] = exam
        return exam

    async def update_exam(self, exam, fields):
        for key, value in fields.items():
            setattr(exam, key, value)

    async def get_submission(self, submission_id):
        return self.submissions.get(submission_id)

    async def list_exam_submissions(self, exam_id):
        return [s for s in self.submissions.values() if repo.link_id(s.question_set) == exam_id]

    async def list_exams_submissions(self, exam_ids):
        return [s for s in self.submissions.values() if repo.link_id(s.question_set) in set(exam_ids)]

    async def list_student_submissions(self, student_id):
        return [s for s in self.submissions.values() if repo.link_id(s.student) == student_id]

    async def create_submission(self, **fields):
        fields["question_set"] = _ref("question_sets", fields["question_set"])
        fields["student"] = _ref("users", fields["student"])
        sub = Submission.model_construct(id=oid_at(), regrade_history=[], final_score=None, **fields)
        self.submissions[sub.id] = sub
        return sub

    async def update_submission(self, submission, fields):
        for key, value in fields.items():
            setattr(submission, key, value)

    async def list_reference_sources(self, exam_id):
        out: dict = {}
        for r in self.references:
            if r["exam_id"] == exam_id:
                entry = out.setdefault(r["source_name"], {"source_name": r["source_name"], "chunks": 0,
                                                          "characters": 0, "student_visible": r["student_visible"]})
                entry["chunks"] += 1
                entry["characters"] += len(r["content"])
        return list(out.values())

    async def create_token(self, **fields):
        token = ApiToken.model_construct(id=oid_at(), last_used_at=None, revoked_at=None, **fields)
        self.tokens[token.id] = token
        return token

    async def get_token(self, token_id):
        return self.tokens.get(token_id)

    async def find_token_by_hash(self, token_hash):
        return next((t for t in self.tokens.values() if t.token_hash == token_hash), None)

    async def list_user_tokens(self, user_id):
        return [t for t in self.tokens.values() if t.user_id == user_id and t.revoked_at is None]

    async def update_token(self, token, fields):
        for key, value in fields.items():
            setattr(token, key, value)

    # ───────────── non-repo collaborators ─────────────

    async def has_reference_material(self, exam_id):
        return any(r["exam_id"] == exam_id for r in self.references)

    async def search_reference_material(self, exam_id, query, student_only=False):
        terms = set(query.lower().split())
        hits = [r for r in self.references
                if r["exam_id"] == exam_id and (r["student_visible"] or not student_only)
                and terms & set(r["content"].lower().split())]
        return {"query": query, "results": [{"source": h["source_name"], "excerpt": h["content"]} for h in hits]}

    async def run_grading_graph(self, **kwargs):
        """Stub for the LangGraph pipeline: records the call and returns a fixed grade."""
        self.graph_calls.append(kwargs)
        if kwargs.get("on_node"):
            for node in ("gatekeeper", "grade", "finalize"):
                await kwargs["on_node"](node)
        trace = make_trace([5.0, 4.0])
        trace["teacher_note"] = kwargs.get("teacher_note")
        return {"score": 9, "feedback": "Regraded feedback.", "rubric_criteria": RUBRIC,
                "rubric_generated": not kwargs.get("rubric_criteria"), "trace": trace}

    def install(self, monkeypatch) -> "FakeRepo":
        names = [
            "get_user", "get_users", "find_students_by_usernames", "get_exam", "get_exams",
            "list_teacher_exams", "list_open_or_assigned_exams", "create_exam", "update_exam",
            "get_submission", "list_exam_submissions", "list_exams_submissions", "list_student_submissions",
            "create_submission", "update_submission", "list_reference_sources",
            "create_token", "get_token", "find_token_by_hash", "list_user_tokens", "update_token",
        ]
        for name in names:
            monkeypatch.setattr(repo, name, getattr(self, name))
        monkeypatch.setattr(evaluation, "has_reference_material", self.has_reference_material)
        monkeypatch.setattr(evaluation, "run_grading_graph", self.run_grading_graph)
        monkeypatch.setattr(analytics, "search_reference_material", self.search_reference_material)
        return self
