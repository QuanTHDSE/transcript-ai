from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import router
from app.assessments import router as assessment_router
from app.templates import router as template_router
from app.grading_rules import router as grading_router
from app.mapping import router as mapping_router

app = FastAPI(title="Transcripto AI API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type"])
app.include_router(router)
app.include_router(assessment_router)
app.include_router(template_router)
app.include_router(grading_router)
app.include_router(mapping_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
