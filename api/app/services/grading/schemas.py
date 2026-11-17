"""Pydantic schemas used to validate every structured LLM output in the graph."""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class InjectionVerdict(BaseModel):
    is_injection: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class RubricCriterion(BaseModel):
    id: str = Field(min_length=1, max_length=20)
    description: str = Field(min_length=3)
    points: float = Field(gt=0)


class Rubric(BaseModel):
    criteria: List[RubricCriterion] = Field(min_length=1, max_length=8)


class CriterionScore(BaseModel):
    criterion_id: str
    awarded: float = Field(ge=0)
    justification: str
    confidence: float = Field(ge=0.0, le=1.0)


class GraderOutput(BaseModel):
    criterion_scores: List[CriterionScore]
    feedback: str


class CriticOutput(BaseModel):
    verdict: Literal["approve", "adjust"]
    issues: List[str] = []
    manipulation_detected: bool = False
    adjusted_scores: Optional[List[CriterionScore]] = None
    revised_feedback: Optional[str] = None
