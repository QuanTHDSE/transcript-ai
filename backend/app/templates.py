import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.routes import DB
from app.template_models import SpeakingTemplate, TemplateVersion
from app.template_inspection import inspect_docx

router = APIRouter(prefix="/api/v1/templates", tags=["speaking templates"])


def storage_root():
    return (Path(os.getenv("UPLOAD_DIR", "storage/uploads")) / "templates").resolve()


class ProtectionChoice(BaseModel):
    run_id: str
    protected: bool


class ProtectionUpdate(BaseModel):
    decisions: list[ProtectionChoice] = Field(min_length=1)


def candidate_ids(parsed):
    ids = set()
    def walk(blocks):
        for block in blocks:
            if block["type"] == "paragraph":
                ids.update(run["id"] for run in block["runs"] if run["protected_candidate"])
            else:
                for row in block["rows"]:
                    for cell in row:
                        walk(cell)
    walk(parsed["blocks"])
    for section in parsed["sections"]:
        walk(section["header"]); walk(section["footer"])
    return ids


def version_payload(version):
    return {"id": version.id, "template_id": version.template_id, "version": version.version,
            "original_filename": version.original_filename, "sha256": version.sha256,
            "size_bytes": version.size_bytes, "created_at": version.created_at,
            "parsed": version.parsed, "protection_decisions": version.protection_decisions}


@router.post("", status_code=201)
def upload_template(db: DB, file: UploadFile = File(...), name: str | None = Form(None), template_id: UUID | None = Form(None)):
    filename = (file.filename or "").replace("\\", "/").split("/")[-1][:255]
    if not filename.lower().endswith(".docx"):
        raise HTTPException(422, "Upload a DOCX file")
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4()}.docx"
    path = root / stored
    size, digest = 0, hashlib.sha256()
    try:
        with path.open("xb") as output:
            while chunk := file.file.read(64 * 1024):
                size += len(chunk)
                if size > int(os.getenv("MAX_TEMPLATE_BYTES", "10485760")):
                    raise HTTPException(413, "Template exceeds configured size limit")
                digest.update(chunk)
                output.write(chunk)
        if not size:
            raise HTTPException(422, "Empty template")
        try:
            parsed = inspect_docx(path)
        except Exception as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, ValueError) else "DOCX inspection failed") from exc
        if template_id:
            template = db.scalars(select(SpeakingTemplate).where(SpeakingTemplate.id == template_id).with_for_update()).first()
            if not template:
                raise HTTPException(404, "Template not found")
            next_version = db.scalar(select(func.max(TemplateVersion.version)).where(TemplateVersion.template_id == template_id)) or 0
        else:
            template = SpeakingTemplate(name=(name or filename.removesuffix(".docx")).strip()[:255])
            if not template.name:
                raise HTTPException(422, "Template name is required")
            db.add(template)
            db.flush()
            next_version = 0
        version = TemplateVersion(template_id=template.id, version=next_version + 1, original_filename=filename,
            stored_filename=stored, sha256=digest.hexdigest(), size_bytes=size, parsed=parsed, protection_decisions={})
        db.add(version)
        db.commit()
        db.refresh(version)
        return version_payload(version)
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


@router.get("")
def list_templates(db: DB, limit: int = Query(100, ge=1, le=200)):
    templates = db.scalars(select(SpeakingTemplate).order_by(SpeakingTemplate.created_at.desc(), SpeakingTemplate.id.desc()).limit(limit)).all()
    return [{"id": item.id, "name": item.name, "created_at": item.created_at,
             "versions": [{"id": version.id, "version": version.version, "original_filename": version.original_filename,
                           "created_at": version.created_at, "warnings": version.parsed.get("warnings", [])} for version in item.versions]}
            for item in templates]


@router.get("/versions/{version_id}")
def get_version(version_id: UUID, db: DB):
    version = db.get(TemplateVersion, version_id)
    if not version:
        raise HTTPException(404, "Template version not found")
    return version_payload(version)


@router.get("/versions/{version_id}/file")
def get_original(version_id: UUID, db: DB):
    version = db.get(TemplateVersion, version_id)
    if not version:
        raise HTTPException(404, "Template version not found")
    root = storage_root()
    path = (root / version.stored_filename).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "Original DOCX not found")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=version.original_filename)


@router.patch("/versions/{version_id}/protection")
def confirm_protection(version_id: UUID, body: ProtectionUpdate, db: DB):
    version = db.scalars(select(TemplateVersion).where(TemplateVersion.id == version_id).with_for_update()).first()
    if not version:
        raise HTTPException(404, "Template version not found")
    allowed = candidate_ids(version.parsed)
    if len({item.run_id for item in body.decisions}) != len(body.decisions) or any(item.run_id not in allowed for item in body.decisions):
        raise HTTPException(422, "Decisions must reference distinct protected-text candidates")
    version.protection_decisions = {**version.protection_decisions, **{item.run_id: item.protected for item in body.decisions}}
    db.commit()
    db.refresh(version)
    return version_payload(version)
