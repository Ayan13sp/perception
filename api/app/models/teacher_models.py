from pydantic import BaseModel, Field
from typing import List, Optional
from .user_models import UserOut, PyObjectId

class QuestionSetCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=100)
    question: str = Field(..., min_length=10)
    model_answer: str = Field(..., min_length=10)
    assigned_usernames: Optional[List[str]] = None

class QuestionSetOut(BaseModel):
    id: PyObjectId
    title: str
    question: str
    model_answer: str
    creator: UserOut
    assigned_students: List[UserOut]
    # Usernames from the request that didn't match a student (only set on create)
    unknown_usernames: List[str] = []

    class Config:
        from_attributes = True
        arbitrary_types_allowed = True

class SubmissionReviewOut(BaseModel):
    id: PyObjectId
    student: UserOut
    student_answer: str
    ai_score: int
    ai_feedback: str
    final_score: int | None
    # Multi-agent audit trail (path, per-criterion scores, critic verdict...)
    grading_trace: Optional[dict] = None

    class Config:
        from_attributes = True
        arbitrary_types_allowed = True

class ReferenceUploadOut(BaseModel):
    source_name: str
    chunks: int
    characters: int
    student_visible: bool = False

class ScoreUpdate(BaseModel):
    final_score: int = Field(..., ge=0, le=10)
