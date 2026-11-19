"""Authorization rules (all fail closed), exam creation and regrades, against the in-memory repo."""
import pytest

from app.services import access, evaluation, exams
from app.services.errors import ForbiddenError, InvalidRequestError, NotFoundError
from tests.fakes import RUBRIC, make_trace


@pytest.fixture
def school(fake_repo):
    alice = fake_repo.add_user("alice", "teacher")
    bob = fake_repo.add_user("bob", "teacher")
    sam = fake_repo.add_user("sam", "student")
    sue = fake_repo.add_user("sue", "student")
    exam = fake_repo.add_exam(alice, assigned=[sam])
    open_exam = fake_repo.add_exam(alice, title="Open to all")
    sub = fake_repo.add_submission(exam, sam, trace=make_trace([3.0, 4.0]))
    return fake_repo, alice, bob, sam, sue, exam, open_exam, sub


# ───────────────────────────── ownership ─────────────────────────────

async def test_teacher_can_open_own_exam(school):
    _, alice, *_rest = school
    exam = school[5]
    assert (await access.get_teacher_exam(str(exam.id), alice)).id == exam.id


async def test_teacher_cannot_open_another_teachers_exam(school):
    _, _, bob, *_ = school
    with pytest.raises(ForbiddenError):
        await access.get_teacher_exam(str(school[5].id), bob)


async def test_unresolvable_creator_fails_closed(school):
    repo_, alice = school[0], school[1]
    orphan = repo_.add_exam(None)  # creator missing: the old check allowed anyone in
    with pytest.raises(ForbiddenError):
        await access.get_teacher_exam(str(orphan.id), alice)


async def test_finalize_idor_is_blocked(school):
    """Teacher B must not reach a submission on teacher A's exam (old finalize route allowed it)."""
    _, alice, bob, *_ = school
    sub = school[7]
    with pytest.raises(ForbiddenError):
        await access.get_teacher_submission(str(sub.id), bob)
    found, exam = await access.get_teacher_submission(str(sub.id), alice)
    assert found.id == sub.id and exam.id == school[5].id


async def test_students_cannot_use_teacher_checks(school):
    sam = school[3]
    with pytest.raises(ForbiddenError):
        await access.get_teacher_exam(str(school[5].id), sam)


@pytest.mark.parametrize("bad_id", ["not-an-id", "", "123"])
async def test_malformed_ids_are_rejected(school, bad_id):
    with pytest.raises(InvalidRequestError):
        await access.get_teacher_exam(bad_id, school[1])


# ───────────────────────────── student scope ─────────────────────────────

async def test_student_sees_assigned_and_open_exams_only(school):
    _, _, _, sam, sue, exam, open_exam, _ = school
    assert (await access.get_student_exam(str(exam.id), sam)).id == exam.id
    assert (await access.get_student_exam(str(open_exam.id), sue)).id == open_exam.id
    with pytest.raises(NotFoundError):  # not assigned: same error as "doesn't exist"
        await access.get_student_exam(str(exam.id), sue)


async def test_student_cannot_read_another_students_result(school):
    _, _, _, sam, sue, *_ = school
    sub = school[7]
    assert (await access.get_student_submission(str(sub.id), sam)).id == sub.id
    with pytest.raises(NotFoundError):
        await access.get_student_submission(str(sub.id), sue)


# ───────────────────────────── exam creation ─────────────────────────────

async def test_create_exam_reports_unknown_usernames(school):
    _, alice, *_ = school
    exam, unknown = await exams.create_exam(alice, title="Cells", question="Describe a plant cell.",
                                            model_answer="A plant cell has a wall, chloroplasts and a vacuole.",
                                            assigned_usernames=["sam", "ghost", "sam", " "])
    assert unknown == ["ghost"]
    assert len(exam.assigned_students) == 1


async def test_all_unknown_usernames_never_creates_an_open_exam(school):
    _, alice, *_ = school
    with pytest.raises(InvalidRequestError, match="open to everyone"):
        await exams.create_exam(alice, title="Cells", question="Describe a plant cell.",
                                model_answer="A plant cell has a wall and chloroplasts.",
                                assigned_usernames=["typo1", "typo2"])


async def test_teacher_rubric_is_validated_and_normalised(school):
    _, alice, *_ = school
    exam, _ = await exams.create_exam(alice, title="Cells", question="Describe a plant cell.",
                                      model_answer="A plant cell has a wall and chloroplasts.",
                                      rubric_criteria=[{"id": "A", "description": "Mentions wall", "points": 1},
                                                       {"id": "B", "description": "Mentions chloroplasts", "points": 3}])
    assert [c["points"] for c in exam.rubric_criteria] == [2.5, 7.5]
    assert exams.current_rubric(exam)  # hash ties it to this model answer


async def test_invalid_rubric_rejected(school):
    with pytest.raises(InvalidRequestError, match="Invalid rubric"):
        exams.validate_rubric([{"id": "A", "description": "x", "points": -1}])


async def test_generated_rubric_on_create(school, monkeypatch):
    async def fake_generate(question, model_answer):
        return RUBRIC, 10
    monkeypatch.setattr(exams, "generate_rubric", fake_generate)
    exam, _ = await exams.create_exam(school[1], title="Cells", question="Describe a plant cell.",
                                      model_answer="A plant cell has a wall and chloroplasts.",
                                      generate_rubric_now=True)
    assert exam.rubric_criteria == RUBRIC


async def test_students_cannot_create_exams(school):
    with pytest.raises(ForbiddenError):
        await exams.create_exam(school[3], title="Cells", question="Describe a plant cell.",
                                model_answer="A plant cell has a wall and chloroplasts.")


async def test_student_exam_listing_never_includes_model_answers(school):
    items = await exams.list_exams_for(school[3])
    assert {i["title"] for i in items} == {"Photosynthesis", "Open to all"}
    assert all("model_answer" not in i for i in items)
    assert next(i for i in items if i["title"] == "Photosynthesis")["status"] == "submitted"


# ───────────────────────────── regrade ─────────────────────────────

async def test_regrade_keeps_history_and_never_touches_final_score(school):
    repo_, alice = school[0], school[1]
    sub = school[7]
    sub.final_score = 6
    seen = []

    async def on_node(name):
        seen.append(name)

    result = await evaluation.regrade_submission(alice, str(sub.id), "Accept 'sugar' for glucose.", on_node=on_node)
    assert repo_.graph_calls[-1]["teacher_note"] == "Accept 'sugar' for glucose."
    assert result["previous_ai_score"] == 5 and result["new_ai_score"] == 9
    assert sub.ai_score == 9 and sub.final_score == 6
    assert sub.regrade_history[-1]["ai_score"] == 5
    assert sub.regrade_history[-1]["note"] == "Accept 'sugar' for glucose."
    assert seen == ["gatekeeper", "grade", "finalize"]


async def test_regrade_of_other_teachers_submission_is_forbidden(school):
    with pytest.raises(ForbiddenError):
        await evaluation.regrade_submission(school[2], str(school[7].id), "be generous")


async def test_regrade_note_length_is_capped(school):
    with pytest.raises(InvalidRequestError):
        await evaluation.regrade_submission(school[1], str(school[7].id), "x" * 1001)
