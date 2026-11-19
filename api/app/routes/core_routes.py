import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Response, status
from typing import List

from app.db.database import User, QuestionSet, Submission
from app.models.teacher_models import (
    QuestionSetCreate, QuestionSetOut, SubmissionReviewOut, ScoreUpdate, ReferenceUploadOut,
)
from app.models.student_models import SubmissionCreate, SubmissionResultOut, QuestionSetForStudentOut
from app.models.user_models import UserOut
from app.services import access, exams, repo
from app.services.auth_dependencies import get_current_user
from app.services.evaluation import grade_answer

router = APIRouter()

MAX_REFERENCE_BYTES = 5 * 1024 * 1024
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200


def _chunk_text(text: str) -> List[str]:
    """Paragraph-aware chunking with a small character overlap."""
    paragraphs = [p.strip() for p in text.replace("\r", "").split("\n\n") if p.strip()]
    chunks, current = [], ""
    for p in paragraphs:
        while len(p) > CHUNK_CHARS:  # split very long paragraphs
            head, p = p[:CHUNK_CHARS], p[CHUNK_CHARS - CHUNK_OVERLAP:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if len(current) + len(p) + 2 > CHUNK_CHARS and current:
            chunks.append(current)
            current = current[-CHUNK_OVERLAP:] + "\n\n" + p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


def _extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="ignore")
    raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .pdf, .txt and .md files are supported.")

# ----------------- Helper Converters -----------------

def _unknown_user(user_id=None) -> UserOut:
    return UserOut(id=str(user_id) if user_id else "unknown", username="Unknown User", email=None, role="unknown")

async def to_user_out(user_or_link) -> UserOut:
    if isinstance(user_or_link, User):
        user = user_or_link
    else:
        user_id = repo.link_id(user_or_link)
        user = await repo.get_user(user_id) if user_id else None
        if not user:
            return _unknown_user(user_id)
    return UserOut(id=str(user.id), username=user.username, email=user.email, role=user.role)

async def to_question_set_out(qs: QuestionSet, unknown_usernames: List[str] = None) -> QuestionSetOut:
    return QuestionSetOut(
        id=str(qs.id),
        title=qs.title,
        question=qs.question,
        model_answer=qs.model_answer,
        creator=await to_user_out(getattr(qs, 'creator', None)),
        assigned_students=[await to_user_out(s) for s in (getattr(qs, 'assigned_students', None) or [])],
        unknown_usernames=unknown_usernames or [],
    )

async def to_question_set_for_student_out(qs: QuestionSet) -> QuestionSetForStudentOut:
    return QuestionSetForStudentOut(
        id=str(qs.id),
        title=qs.title,
        question=qs.question,
        creator=await to_user_out(getattr(qs, 'creator', None)),
    )

async def to_submission_review_out(sub: Submission) -> SubmissionReviewOut:
    return SubmissionReviewOut(
        id=str(sub.id),
        student=await to_user_out(getattr(sub, 'student', None)),
        student_answer=sub.student_answer,
        ai_score=sub.ai_score,
        ai_feedback=sub.ai_feedback,
        final_score=sub.final_score,
        grading_trace=getattr(sub, 'grading_trace', None),
    )

async def to_submission_result_out(sub: Submission) -> SubmissionResultOut:
    target = getattr(sub, 'question_set', None)
    if isinstance(target, QuestionSet):
        qs = target
    else:
        qs_id = repo.link_id(target)
        qs = await repo.get_exam(qs_id) if qs_id else None

    if qs:
        qset_out = await to_question_set_for_student_out(qs)
    else:
        qset_out = QuestionSetForStudentOut(
            id=str(getattr(sub, 'id', 'archived')),
            title="Archived Question Set",
            question="Question details no longer available",
            creator=UserOut(id="system", username="Teacher", email=None, role="teacher")
        )

    return SubmissionResultOut(
        id=str(sub.id),
        question_set=qset_out,
        student_answer=sub.student_answer,
        ai_score=sub.ai_score,
        ai_feedback=sub.ai_feedback,
        final_score=sub.final_score
    )

# ----------------- Teacher Routes -----------------

@router.get("/teacher/question-sets", response_model=List[QuestionSetOut])
async def get_teacher_question_sets(current_user: User = Depends(get_current_user)):
    if current_user.role != "teacher":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only teachers can view their question sets.")
    return [await to_question_set_out(qs) for qs in await repo.list_teacher_exams(current_user.id)]

@router.post("/teacher/question-sets", response_model=QuestionSetOut, status_code=status.HTTP_201_CREATED)
@router.post("/rubric", response_model=QuestionSetOut, status_code=status.HTTP_201_CREATED)
async def create_question_set(qs_data: QuestionSetCreate, current_user: User = Depends(get_current_user)):
    if current_user.role != "teacher":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only teachers can create question sets.")
    exam, unknown = await exams.create_exam(
        current_user,
        title=qs_data.title,
        question=qs_data.question,
        model_answer=qs_data.model_answer,
        assigned_usernames=qs_data.assigned_usernames,
    )
    return await to_question_set_out(exam, unknown)

@router.get("/teacher/question-sets/{id}/submissions", response_model=List[SubmissionReviewOut])
async def get_question_set_submissions(id: str, current_user: User = Depends(get_current_user)):
    qs = await access.get_teacher_exam(id, current_user)
    return [await to_submission_review_out(sub) for sub in await repo.list_exam_submissions(qs.id)]

@router.put("/teacher/submissions/{id}/finalize", response_model=SubmissionReviewOut)
async def finalize_submission_score(id: str, score_data: ScoreUpdate, current_user: User = Depends(get_current_user)):
    # Ownership check: only the teacher who owns the question set may finalize its grades
    sub, _ = await access.get_teacher_submission(id, current_user)
    await repo.update_submission(sub, {"final_score": score_data.final_score})
    return await to_submission_review_out(sub)

@router.post("/teacher/question-sets/{id}/references", response_model=ReferenceUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_reference_material(
    id: str,
    file: UploadFile = File(...),
    student_visible: bool = Form(False, description="Let students see excerpts in their study pointers"),
    current_user: User = Depends(get_current_user),
):
    """Upload lecture notes (PDF/TXT/MD) the fact-checker agent can search."""
    qs = await access.get_teacher_exam(id, current_user)
    data = await file.read(MAX_REFERENCE_BYTES + 1)
    if len(data) > MAX_REFERENCE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 5 MB.")

    text = _extract_text(file.filename or "", data)
    chunks = _chunk_text(text)
    if not chunks:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No extractable text found in file.")

    source = (file.filename or "upload")[:200]
    # Re-uploading the same file replaces its previous chunks
    await repo.replace_reference_source(qs.id, source, chunks, student_visible)
    return ReferenceUploadOut(source_name=source, chunks=len(chunks), characters=len(text),
                              student_visible=student_visible)

@router.get("/teacher/question-sets/{id}/references", response_model=List[ReferenceUploadOut])
async def list_reference_material(id: str, current_user: User = Depends(get_current_user)):
    qs = await access.get_teacher_exam(id, current_user)
    return [ReferenceUploadOut(**source) for source in await repo.list_reference_sources(qs.id)]

@router.delete("/teacher/question-sets/{id}/references/{source_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reference_material(id: str, source_name: str, current_user: User = Depends(get_current_user)):
    qs = await access.get_teacher_exam(id, current_user)
    await repo.delete_reference_source(qs.id, source_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# ----------------- Student Routes -----------------

@router.get("/student/question-sets", response_model=List[QuestionSetForStudentOut])
async def get_student_question_sets(current_user: User = Depends(get_current_user)):
    if current_user.role != "student":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only students can view available question sets.")

    try:
        all_qsets = await repo.list_open_or_assigned_exams(current_user.id)
        submissions = await repo.list_student_submissions(current_user.id)
        submitted_qs_ids = {repo.link_id(sub.question_set) for sub in submissions}
        available_qsets = [qs for qs in all_qsets if qs.id not in submitted_qs_ids]
        return [await to_question_set_for_student_out(qs) for qs in available_qsets]
    except Exception as e:
        print(f"Error in get_student_question_sets: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Failed to load question sets: {str(e)}")

@router.get("/student/submissions", response_model=List[SubmissionResultOut])
async def get_student_submissions(current_user: User = Depends(get_current_user)):
    if current_user.role != "student":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only students can view their submissions.")

    try:
        submissions = await repo.list_student_submissions(current_user.id)
        return [await to_submission_result_out(sub) for sub in submissions]
    except Exception as e:
        print(f"Error in get_student_submissions: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Failed to load submissions: {str(e)}")

@router.post("/student/submissions", response_model=SubmissionResultOut, status_code=status.HTTP_201_CREATED)
@router.post("/evaluate", response_model=SubmissionResultOut, status_code=status.HTTP_201_CREATED)
async def submit_student_answer(sub_data: SubmissionCreate, current_user: User = Depends(get_current_user)):
    # Students may only answer exams that are open to all or assigned to them
    question_set = await access.get_student_exam(sub_data.question_set_id, current_user)
    evaluation = await grade_answer(question_set, sub_data.answer)

    submission = await repo.create_submission(
        question_set=question_set,
        student=current_user,
        student_answer=sub_data.answer,
        ai_score=evaluation["score"],
        ai_feedback=evaluation["feedback"],
        grading_trace=evaluation.get("trace"),
    )
    return await to_submission_result_out(submission)

# ----------------- Results & Analytics Routes -----------------

@router.get("/results/{id}", response_model=SubmissionResultOut)
async def get_results(id: str, current_user: User = Depends(get_current_user)):
    if current_user.role == "student":
        submission = await access.get_student_submission(id, current_user)
    else:
        submission, _ = await access.get_teacher_submission(id, current_user)
    return await to_submission_result_out(submission)

@router.get("/analytics")
async def get_analytics(current_user: User = Depends(get_current_user)):
    if current_user.role != "teacher":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only teachers can view analytics.")

    my_qsets = await repo.list_teacher_exams(current_user.id)
    submissions = await repo.list_exams_submissions([qs.id for qs in my_qsets])

    total_submissions = len(submissions)
    average_score = 0
    if total_submissions > 0:
        average_score = sum([sub.ai_score for sub in submissions]) / total_submissions

    return {
        "total_submissions": total_submissions,
        "average_score": round(average_score, 2),
        "total_rubrics": len(my_qsets)
    }
