import hashlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
from xml.etree import ElementTree

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_COLOR_INDEX
from docx.enum.dml import MSO_THEME_COLOR_INDEX
from docx.shared import RGBColor
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from uuid import uuid4

from app.main import app
from app.models import Base
from app.routes import session_dependency
from app.template_inspection import inspect_docx


def sample_docx():
    document = Document()
    document.add_heading("IELTS Speaking Part 2", 1)
    paragraph = document.add_paragraph("Describe a person? ")
    red = paragraph.add_run("Keep this prompt")
    red.font.color.rgb = RGBColor(255, 0, 0)
    red.font.bold = True
    red.font.highlight_color = WD_COLOR_INDEX.YELLOW
    style = document.styles.add_style("Mandatory", WD_STYLE_TYPE.CHARACTER)
    style.font.color.rgb = RGBColor(200, 10, 10)
    paragraph.add_run(" Styled red").style = style
    theme = paragraph.add_run(" Theme red")
    theme.font.color.theme_color = MSO_THEME_COLOR_INDEX.ACCENT_1
    document.add_paragraph("Ordinary sentence.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Prompt cell"
    table.cell(0, 1).text = "Answer cell"
    document.sections[0].header.paragraphs[0].text = "Header text"
    output = io.BytesIO()
    document.save(output)
    source = io.BytesIO(output.getvalue())
    changed = io.BytesIO()
    with ZipFile(source) as original, ZipFile(changed, "w") as target:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename == "word/theme/theme1.xml":
                root = ElementTree.fromstring(data)
                ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
                accent = root.find(f".//{{{ns}}}clrScheme/{{{ns}}}accent1/{{{ns}}}srgbClr")
                accent.set("val", "FF0000")
                data = ElementTree.tostring(root)
            target.writestr(item, data)
    return changed.getvalue()


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.schema = None
        if os.getenv("TEST_DATABASE_URL"):
            self.schema = "template_test_" + uuid4().hex
            self.admin = create_engine(os.environ["TEST_DATABASE_URL"])
            with self.admin.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            self.engine = create_engine(os.environ["TEST_DATABASE_URL"], connect_args={"options": f"-csearch_path={self.schema}"})
        else:
            self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        def session():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[session_dependency] = session
        self.client = TestClient(app)
        self.environ = patch.dict(os.environ, {"UPLOAD_DIR": self.temp.name})
        self.environ.start()

    def tearDown(self):
        self.environ.stop(); self.client.close(); app.dependency_overrides.clear(); self.engine.dispose()
        if self.schema:
            with self.admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()
        self.temp.cleanup()

    def test_upload_parse_confirm_version_and_immutable_original(self):
        original = sample_docx()
        response = self.client.post("/api/v1/templates", files={"file": ("prompt.docx", original)})
        self.assertEqual(response.status_code, 201, response.text)
        first = response.json()
        self.assertEqual(first["sha256"], hashlib.sha256(original).hexdigest())
        blocks = first["parsed"]["blocks"]
        self.assertEqual([block["type"] for block in blocks], ["paragraph", "paragraph", "paragraph", "table"])
        self.assertEqual(blocks[0]["part"], 2)
        self.assertEqual(blocks[3]["rows"][0][1][0]["text"], "Answer cell")
        self.assertEqual(first["parsed"]["sections"][0]["header"][0]["text"], "Header text")
        runs = blocks[1]["runs"]
        self.assertTrue(all(run["red"] for run in runs[1:]))
        self.assertEqual(runs[-1]["color_source"], "theme")
        self.assertTrue(runs[1]["bold"])
        self.assertEqual(self.client.get(f'/api/v1/templates/versions/{first["id"]}/file').content, original)
        decisions = [{"run_id": runs[0]["id"], "protected": True}, {"run_id": runs[1]["id"], "protected": False}]
        confirmed = self.client.patch(f'/api/v1/templates/versions/{first["id"]}/protection', json={"decisions": decisions})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["protection_decisions"], {runs[0]["id"]: True, runs[1]["id"]: False})
        self.assertEqual(self.client.get(f'/api/v1/templates/versions/{first["id"]}/file').content, original)
        self.assertEqual(self.client.patch(f'/api/v1/templates/versions/{first["id"]}/protection', json={"decisions": [{"run_id": "unknown", "protected": True}]}).status_code, 422)
        second = self.client.post("/api/v1/templates", data={"template_id": first["template_id"]}, files={"file": ("update.docx", original)}).json()
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["protection_decisions"], {})
        listing = self.client.get("/api/v1/templates").json()
        self.assertEqual(len(listing), 1)
        self.assertEqual([item["version"] for item in listing[0]["versions"]], [1, 2])

    def test_invalid_and_oversized_uploads_clean_files(self):
        for filename, content, env, expected in [("bad.docx", b"not a zip", {}, 422),
                                                   ("large.docx", b"x" * 16, {"MAX_TEMPLATE_BYTES": "8"}, 413),
                                                   ("bad.txt", b"x", {}, 422)]:
            with patch.dict(os.environ, env):
                response = self.client.post("/api/v1/templates", files={"file": (filename, content)})
                self.assertEqual(response.status_code, expected, response.text)
        self.assertFalse(list((Path(self.temp.name) / "templates").glob("*.docx")))
        self.assertEqual(self.client.get("/api/v1/templates").json(), [])
