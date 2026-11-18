"""Reviewed rule presets and deterministic duration evaluation."""
import re
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from app.routes import DB
from app.template_models import TemplateVersion
from app.grading_models import GradingPreset, GradingRuleVersion, DurationResult

router = APIRouter(prefix="/api/v1/grading-presets", tags=["grading rules"])

class DurationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part: int = Field(ge=1, le=20)
    min_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)
    max_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)
    @model_validator(mode="after")
    def ordered(self):
        if self.min_seconds > self.max_seconds:
            raise ValueError("Minimum cannot exceed maximum")
        return self

class CorrectionStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["grammar", "pronunciation", "vocabulary"]
    color: Literal["red", "yellow", "green", "blue", "none"]
    correction_placement: Literal["none", "beside_arrow"] = "none"

class Rules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_limits: list[DurationRule] = Field(default_factory=list, max_length=20)
    correction_styles: list[CorrectionStyle] = Field(default_factory=list, max_length=3)
    preserve_original_red_text: bool = False
    required_structures: list[str] = Field(default_factory=list, max_length=100)
    @model_validator(mode="after")
    def unique(self):
        if len({x.part for x in self.duration_limits}) != len(self.duration_limits):
            raise ValueError("Each part must have only one duration rule")
        if len({x.category for x in self.correction_styles}) != len(self.correction_styles):
            raise ValueError("Each category must have only one correction style")
        if any(not s.strip() or len(s) > 1000 for s in self.required_structures):
            raise ValueError("Required structures must be nonempty and at most 1000 characters")
        return self

class ParseInput(BaseModel):
    instructions: str = Field(min_length=1, max_length=20000)

class CreatePreset(ParseInput):
    name: str = Field(min_length=1, max_length=255)
    template_version_id: UUID
    rules: Rules
    reviewed: Literal[True]

class CreateVersion(ParseInput):
    rules: Rules
    reviewed: Literal[True]

class EvaluateDuration(BaseModel):
    part: int = Field(ge=1, le=20)
    duration_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)

def parse_instructions(instructions: str):
    """Recognize limited explicit forms; all results remain subject to review."""
    durations = {}
    conflicts = []
    pattern = re.compile(r"(?:parts?|phần)\s*(\d+(?:\s*(?:,|and|và|&)\s*\d+)*)[^\d\n]{0,80}(\d+(?:[.,]\d+)?)\s*(?:-|–|—|to|đến|tới)\s*(\d+(?:[.,]\d+)?)\s*(?:seconds?|secs?|giây)\b", re.I)
    for match in pattern.finditer(instructions):
        low, high = (float(value.replace(",", ".")) for value in match.group(2, 3))
        for part in re.findall(r"\d+", match.group(1)):
            number = int(part)
            if not 1 <= number <= 20 or low > high or high > 86400 or (number in durations and durations[number] != (low, high)):
                conflicts.append(match.group(0)); continue
            durations[number] = (low, high)
    text = instructions.lower()
    preserve = bool(re.search(r"(?:keep|preserve|unchanged|giữ|bảo vệ).{0,100}(?:red|màu đỏ|chữ đỏ)|(?:red|màu đỏ|chữ đỏ).{0,100}(?:unchanged|keep|preserve|giữ|bảo vệ)", text))
    styles = []
    for sentence in re.split(r"[\n.;]+", text):
        category = next((key for key, terms in [("grammar", ("grammar", "ngữ pháp")), ("pronunciation", ("pronunciation", "phát âm")), ("vocabulary", ("vocabulary", "từ vựng"))] if any(term in sentence for term in terms)), None)
        colors = [color for color, terms in [("red", ("red", "đỏ")), ("yellow", ("yellow", "vàng")), ("blue", ("blue", "xanh dương")), ("green", ("green", "xanh lá"))] if any(term in sentence for term in terms)]
        if category and len(colors) == 1 and not any(s["category"] == category for s in styles):
            styles.append({"category": category, "color": colors[0], "correction_placement": "beside_arrow" if re.search(r"arrow|mũi tên|→", sentence) else "none"})
    structures = []
    for sentence in re.split(r"[\n;]+", instructions):
        if re.search(r"required|must (?:use|include)|bắt buộc|phải (?:dùng|sử dụng|có)", sentence, re.I):
            structures.extend(match.group(1) for match in re.finditer(r'["“]([^"”\n]{1,1000})["”]', sentence))
    rules = Rules(duration_limits=[DurationRule(part=part, min_seconds=bounds[0], max_seconds=bounds[1]) for part, bounds in sorted(durations.items())], correction_styles=styles, preserve_original_red_text=preserve, required_structures=structures[:100])
    return {"rules": rules.model_dump(), "review_required": True, "review_notes": ["Review every instruction and complete any missing or ambiguous rules before saving.", *[f"Conflicting or invalid duration: {item}" for item in conflicts]]}

def version_data(v):
    return {"id": v.id, "preset_id": v.preset_id, "version": v.version, "instructions": v.instructions, "rules": v.rules, "created_at": v.created_at}

@router.post("/parse")
def parse(body: ParseInput):
    return parse_instructions(body.instructions)

@router.post("", status_code=201)
def create(body: CreatePreset, db: DB):
    if not db.get(TemplateVersion, body.template_version_id):
        raise HTTPException(404, "Template version not found")
    if not body.name.strip() or not body.instructions.strip():
        raise HTTPException(422, "Name and instructions are required")
    preset = GradingPreset(name=body.name.strip(), template_version_id=body.template_version_id)
    db.add(preset); db.flush()
    version = GradingRuleVersion(preset_id=preset.id, version=1, instructions=body.instructions, rules=body.rules.model_dump(mode="json"))
    db.add(version); db.commit(); db.refresh(version)
    return version_data(version)

@router.get("")
def listing(db: DB):
    presets = db.scalars(select(GradingPreset).order_by(GradingPreset.created_at.desc()).limit(200)).all()
    return [{"id": p.id, "name": p.name, "template_version_id": p.template_version_id, "versions": [version_data(v) for v in db.scalars(select(GradingRuleVersion).where(GradingRuleVersion.preset_id == p.id).order_by(GradingRuleVersion.version.desc())).all()]} for p in presets]

@router.post("/{preset_id}/versions", status_code=201)
def add_version(preset_id: UUID, body: CreateVersion, db: DB):
    preset = db.scalars(select(GradingPreset).where(GradingPreset.id == preset_id).with_for_update()).first()
    if not preset: raise HTTPException(404, "Preset not found")
    if not body.instructions.strip(): raise HTTPException(422, "Instructions are required")
    number = (db.scalar(select(func.max(GradingRuleVersion.version)).where(GradingRuleVersion.preset_id == preset_id)) or 0) + 1
    version = GradingRuleVersion(preset_id=preset_id, version=number, instructions=body.instructions, rules=body.rules.model_dump(mode="json"))
    db.add(version); db.commit(); db.refresh(version)
    return version_data(version)

@router.get("/versions/{version_id}")
def get_version(version_id: UUID, db: DB):
    version = db.get(GradingRuleVersion, version_id)
    if not version: raise HTTPException(404, "Rule version not found")
    return version_data(version)

@router.post("/versions/{version_id}/duration-results", status_code=201)
def evaluate(version_id: UUID, body: EvaluateDuration, db: DB):
    version = db.get(GradingRuleVersion, version_id)
    if not version: raise HTTPException(404, "Rule version not found")
    rules = Rules.model_validate(version.rules)
    rule = next((r for r in rules.duration_limits if r.part == body.part), None)
    result = {"part": body.part, "duration_seconds": body.duration_seconds, "status": "PASSED" if rule and rule.min_seconds <= body.duration_seconds <= rule.max_seconds else "NOT PASSED" if rule else "NOT_ASSESSED", "min_seconds": rule.min_seconds if rule else None, "max_seconds": rule.max_seconds if rule else None}
    record = DurationResult(rule_version_id=version_id, result=result)
    db.add(record); db.commit(); db.refresh(record)
    return {"id": record.id, "rule_version_id": version_id, **result}

@router.get("/duration-results/{result_id}")
def get_result(result_id: UUID, db: DB):
    result = db.get(DurationResult, result_id)
    if not result: raise HTTPException(404, "Result not found")
    return {"id": result.id, "rule_version_id": result.rule_version_id, **result.result}
