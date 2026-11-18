"""Read-only DOCX inspection. The source ZIP is never saved or rewritten by python-docx."""
import re
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree
from docx import Document
from docx.document import Document as DocumentType
from docx.table import Table
from docx.text.paragraph import Paragraph

THEME_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PART = re.compile(r"\b(?:ielts\s+speaking\s+)?part\s*([123])\b", re.I)
QUESTION = re.compile(r"(?:\?|\b(?:describe|tell me|talk about|why|how|what|where|when|who|do you|would you)\b)", re.I)


def _theme_colors(archive):
    if "word/theme/theme1.xml" not in archive.namelist():
        return {}
    root = ElementTree.fromstring(archive.read("word/theme/theme1.xml"))
    scheme = root.find(f".//{{{THEME_NS}}}clrScheme")
    colors = {}
    if scheme is not None:
        for entry in scheme:
            value = next(iter(entry), None)
            if value is not None:
                colors[entry.tag.split("}")[-1]] = value.attrib.get("val") or value.attrib.get("lastClr")
    return colors


def _color(font, theme):
    color = font.color
    if color.theme_color is not None:
        name = str(color.theme_color).split(" (")[0].lower().replace(" ", "").replace("_", "")
        alias = {"dark1": "dk1", "light1": "lt1", "dark2": "dk2", "light2": "lt2"}
        return theme.get(alias.get(name, name)), "theme" if name in theme or alias.get(name) in theme else "unresolved_theme"
    if color.rgb is not None:
        return str(color.rgb).upper(), "rgb"
    return None, None


def _red(rgb):
    if not rgb or not re.fullmatch(r"[0-9A-F]{6}", rgb):
        return False
    r, g, b = (int(rgb[i:i+2], 16) for i in (0, 2, 4))
    return r >= 150 and r >= 1.5 * max(g, 1) and r >= 1.5 * max(b, 1)


def _effective(run, paragraph, theme, warnings):
    sources = [run.font]
    if run.style is not None:
        style = run.style
        while style is not None:
            sources.append(style.font)
            style = style.base_style
    style = paragraph.style
    while style is not None:
        sources.append(style.font)
        style = style.base_style
    result = {"color": None, "color_source": None, "highlight": None, "bold": None, "italic": None}
    for font in sources:
        if result["color_source"] is None:
            color, source = _color(font, theme)
            if source:
                result["color"], result["color_source"] = color, source
        for key in ("highlight", "bold", "italic"):
            value = font.highlight_color if key == "highlight" else getattr(font, key)
            if value is not None and result[key] is None:
                result[key] = str(value) if key == "highlight" else value
    if result["color_source"] == "unresolved_theme":
        warnings.add("A theme font color could not be resolved; review its appearance in Word.")
    result["red"] = _red(result["color"])
    return result


def _paragraph(paragraph, path, theme, warnings):
    text = paragraph.text
    heading = paragraph.style.name if paragraph.style and paragraph.style.name.lower().startswith("heading") else None
    part = PART.search(text)
    question = bool(QUESTION.search(text))
    runs = []
    for index, run in enumerate(paragraph.runs):
        if not run.text:
            continue
        appearance = _effective(run, paragraph, theme, warnings)
        runs.append({"id": f"{path}/r{index}", "text": run.text, **appearance,
                     "protected_candidate": bool(appearance["red"] or heading or part or question)})
    if paragraph._p.xpath(".//w:hyperlink"):
        warnings.add("Hyperlink text is included in paragraphs but is not represented as individual runs in the preview.")
    if paragraph._p.xpath(".//w:drawing"):
        warnings.add("Images/drawings remain in the original DOCX but are omitted from the preview.")
    if paragraph._p.xpath(".//w:sdt|.//w:ins|.//w:del|.//w:fldChar"):
        warnings.add("Content controls, tracked changes or fields need review in Word.")
    return {"type": "paragraph", "id": path, "text": text, "style": paragraph.style.name if paragraph.style else None,
            "heading": heading, "part": int(part.group(1)) if part else None, "question": question, "runs": runs}


def _blocks(container, prefix, theme, warnings):
    result = []
    for index, block in enumerate(container.iter_inner_content()):
        path = f"{prefix}/{index}"
        if isinstance(block, Paragraph):
            result.append(_paragraph(block, path, theme, warnings))
        elif isinstance(block, Table):
            rows = []
            for row_index, row in enumerate(block.rows):
                rows.append([_blocks(cell, f"{path}/row{row_index}/cell{cell_index}", theme, warnings)
                             for cell_index, cell in enumerate(row.cells)])
            result.append({"type": "table", "id": path, "rows": rows})
    return result


def inspect_docx(path: Path) -> dict:
    warnings = set()
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 2000 or sum(item.file_size for item in infos) > 100 * 1024 * 1024:
                raise ValueError("DOCX archive is too large to inspect safely")
            names = set(archive.namelist())
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise ValueError("Invalid DOCX document")
            if any(name.endswith("vbaProject.bin") for name in names):
                warnings.add("Macro content is not inspected; open the original document in a trusted editor.")
            if any(name.startswith("word/embeddings/") for name in names):
                warnings.add("Embedded objects are not displayed in the preview.")
            for name in names:
                if name.endswith(".rels") and b'TargetMode="External"' in archive.read(name):
                    warnings.add("External document relationships are not loaded in the preview.")
            theme = _theme_colors(archive)
        document: DocumentType = Document(path)
        blocks = _blocks(document, "body", theme, warnings)
        sections = []
        for index, section in enumerate(document.sections):
            sections.append({"index": index, "start_type": str(section.start_type),
                "header": _blocks(section.header, f"section{index}/header", theme, warnings),
                "footer": _blocks(section.footer, f"section{index}/footer", theme, warnings)})
        return {"blocks": blocks, "sections": sections, "warnings": sorted(warnings),
                "image_count": sum(1 for relation in document.part.rels.values() if relation.reltype.endswith("/image"))}
    except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError("Invalid or unsupported DOCX document") from exc
