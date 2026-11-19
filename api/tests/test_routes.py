"""HTTP-level checks of the REST API on the in-memory repo (real routes, real JWTs, no MongoDB)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.security import create_access_token
from tests.fakes import make_trace


def auth(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id), user.role)}"}


@pytest.fixture
def api(fake_repo):
    alice = fake_repo.add_user("alice", "teacher")
    bob = fake_repo.add_user("bob", "teacher")
    sam = fake_repo.add_user("sam", "student")
    sue = fake_repo.add_user("sue", "student")
    exam = fake_repo.add_exam(alice, assigned=[sam])
    sub = fake_repo.add_submission(exam, sam, trace=make_trace([3.0, 4.0]))
    # No `with`: the lifespan (MongoDB init, MCP session manager) doesn't run
    return TestClient(app), fake_repo, alice, bob, sam, sue, exam, sub


def test_spoofed_x_user_id_header_is_rejected(api):
    client, _, alice, *_ = api
    assert client.get("/api/teacher/question-sets", headers={"x-user-id": str(alice.id)}).status_code == 401


def test_finalize_requires_owning_the_exam(api):
    client, _, alice, bob, _, _, _, sub = api
    r = client.put(f"/api/teacher/submissions/{sub.id}/finalize", json={"final_score": 10}, headers=auth(bob))
    assert r.status_code == 403 and sub.final_score is None
    r = client.put(f"/api/teacher/submissions/{sub.id}/finalize", json={"final_score": 8}, headers=auth(alice))
    assert r.status_code == 200 and r.json()["final_score"] == 8


def test_submissions_list_is_owner_only(api):
    client, _, alice, bob, *_ = api
    exam = api[6]
    assert client.get(f"/api/teacher/question-sets/{exam.id}/submissions", headers=auth(bob)).status_code == 403
    r = client.get(f"/api/teacher/question-sets/{exam.id}/submissions", headers=auth(alice))
    assert r.status_code == 200 and r.json()[0]["grading_trace"]["pipeline"] == "graph"


def test_student_cannot_submit_to_unassigned_exam(api):
    client, _, _, _, _, sue, exam, _ = api
    r = client.post("/api/evaluate", json={"question_set_id": str(exam.id), "answer": "Plants make food."},
                    headers=auth(sue))
    assert r.status_code == 404


def test_assigned_student_submission_is_graded_and_traced(api):
    client, repo_, _, _, sam, _, exam, _ = api
    r = client.post("/api/student/submissions", json={"question_set_id": str(exam.id), "answer": "Light to glucose."},
                    headers=auth(sam))
    assert r.status_code == 201 and r.json()["ai_score"] == 9
    stored = next(s for s in repo_.submissions.values() if s.student_answer == "Light to glucose.")
    assert stored.grading_trace["pipeline"] == "graph"
    assert exam.rubric_criteria  # first submission caches the generated rubric


def test_results_are_private_to_owner(api):
    client, _, alice, bob, sam, sue, _, sub = api
    assert client.get(f"/api/results/{sub.id}", headers=auth(sam)).status_code == 200
    assert client.get(f"/api/results/{sub.id}", headers=auth(sue)).status_code == 404
    assert client.get(f"/api/results/{sub.id}", headers=auth(alice)).status_code == 200
    assert client.get(f"/api/results/{sub.id}", headers=auth(bob)).status_code == 403


def test_create_exam_guards_against_all_typo_assignment(api):
    client, _, alice, *_ = api
    body = {"title": "Cells", "question": "Describe a plant cell.",
            "model_answer": "A plant cell has a wall and chloroplasts.", "assigned_usernames": ["nobody"]}
    r = client.post("/api/teacher/question-sets", json=body, headers=auth(alice))
    assert r.status_code == 422 and "open to everyone" in r.json()["detail"]

    body["assigned_usernames"] = ["sam", "nobody"]
    r = client.post("/api/teacher/question-sets", json=body, headers=auth(alice))
    assert r.status_code == 201 and r.json()["unknown_usernames"] == ["nobody"]
