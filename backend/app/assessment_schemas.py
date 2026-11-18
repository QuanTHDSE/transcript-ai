from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

CriterionCode = Literal["fluency_coherence", "lexical_resource", "grammar_accuracy", "pronunciation"]


class AssessmentCreate(BaseModel):
    job_id: UUID
    grading_rule_version_id: UUID | None = None
    language: Literal["en"] = "en"
    part: Literal[1, 2, 3, 4] | None = None
    question: str | None = Field(None, max_length=4000)

    @model_validator(mode="after")
    def custom_part(self):
        if self.part == 4 and self.grading_rule_version_id is None:
            raise ValueError("Part 4 requires custom grading rules")
        return self


class Finding(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    segment_id: UUID
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    evidence: str = Field(min_length=1)
    suggested_correction: str | None = None
    explanation: str = Field(min_length=1)
    category: str | None = None
    issue_type: str | None = None
    explanation_vi: str | None = None
    severity: str | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    uncertain: bool = False
    suggested_practice: str | None = None

    @computed_field
    @property
    def original_text(self) -> str:
        return self.evidence

    @computed_field
    @property
    def corrected_text(self) -> str | None:
        return self.suggested_correction

    @computed_field
    @property
    def explanation_en(self) -> str:
        return self.explanation

    @computed_field
    @property
    def start_time(self) -> float:
        return self.start_ms / 1000

    @computed_field
    @property
    def end_time(self) -> float:
        return self.end_ms / 1000

    @computed_field
    @property
    def recommended_practice(self) -> str | None:
        return self.suggested_practice


class Recommendation(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    exercise: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class CriterionResult(BaseModel):
    code: CriterionCode
    score: float | None = Field(None, ge=0, le=9)
    status: Literal["COMPLETED", "NOT_ASSESSED"] = "COMPLETED"
    analysis: dict | None = None
    feedback: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)


class CriterionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    source: str
    status: str
    provider: str | None
    error: str | None
    score: float | None
    analysis: dict | None
    feedback: list[Finding]
    recommendations: list[Recommendation]


class TranscriptSnapshot(BaseModel):
    id: UUID
    start_ms: int
    end_ms: int
    text: str


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    job_id: UUID
    grading_rule_version_id: UUID | None
    status: str
    part: int | None
    question: str | None
    transcript_snapshot: list[TranscriptSnapshot]
    criteria: list[CriterionResponse]
    disclaimer: str = "Automated practice feedback only; not an official IELTS result. Transcript analysis cannot assess pronunciation."


