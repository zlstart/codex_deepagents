"""Read actual Office exports and verify their rendered pages independently.

The small fixture archive holds two-page synthetic Word, PowerPoint and Excel
files. The OOXML originals came from python-docx, python-pptx and openpyxl.
The three OLE files were exported using LibreOffice's Office 97 filters.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

from famsun.config import Settings
from famsun.preview import convert

FIXTURES = Path(__file__).parent / "fixtures" / "office-formats.zip"
PAGE_MARKERS = {
    "word": ("FAMSUN document 1847", "Document two 第二页"),
    "slides": ("FAMSUN slide 1 技术审阅", "FAMSUN slide 2 技术审阅"),
    "workbook": ("FAMSUN worksheet 1847", "Second worksheet"),
}


@pytest.fixture(scope="module")
def office_settings():
    required = os.getenv("FAMSUN_REQUIRE_DOCKER_TESTS") == "true"
    settings = Settings(allow_unenforced_cgroups=True)
    missing = [
        name for name in ("docker", "pdftotext", "pdfinfo") if not shutil.which(name)
    ]
    if (
        not missing
        and subprocess.run(
            ["docker", "image", "inspect", settings.preview_image],
            capture_output=True,
            timeout=20,
            check=False,
        ).returncode
    ):
        missing.append(settings.preview_image)
    if missing:
        reason = "Office rendering tests require " + ", ".join(missing)
        if required:
            pytest.fail(reason)
        pytest.skip(reason)
    return settings


def office_bytes(ext):
    with ZipFile(FIXTURES) as archive:
        data = archive.read("report." + ext)
    if ext in {"doc", "ppt", "xls"}:
        assert data.startswith(bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1"))
    else:
        assert data.startswith(b"PK\x03\x04")
    return data


def poppler(command, pdf):
    result = subprocess.run(
        command, input=pdf, capture_output=True, timeout=20, check=True
    )
    return result.stdout.decode("utf-8")


@pytest.mark.parametrize(
    ("ext", "document"),
    [
        ("doc", "word"),
        ("docx", "word"),
        ("ppt", "slides"),
        ("pptx", "slides"),
        ("xls", "workbook"),
        ("xlsx", "workbook"),
    ],
)
def test_layout_renders_each_page_in_order(office_settings, ext, document):
    pdf = convert(office_settings, office_bytes(ext), ext, "layout", 0, 0)
    assert pdf.startswith(b"%PDF-")
    info = poppler(["pdfinfo", "-"], pdf)
    assert re.search(r"^Pages:\s+2$", info, re.MULTILINE), info
    text = poppler(["pdftotext", "-enc", "UTF-8", "-layout", "-", "-"], pdf)
    pages = text.rstrip("\f\n").split("\f")
    assert len(pages) == 2
    for page, marker in zip(pages, PAGE_MARKERS[document], strict=True):
        # Mixed Latin/CJK runs can contain positioning spaces in the PDF.
        assert "".join(marker.split()) in "".join(page.split()), page


@pytest.mark.parametrize("ext", ["xls", "xlsx"])
def test_workbook_preserves_visible_sheets_offsets_and_numbers(office_settings, ext):
    data = office_bytes(ext)
    first = json.loads(convert(office_settings, data, ext, "sheet", 0, 0))
    assert first["sheets"] == ["生产记录", "调试"]
    assert first["sheet"] == first["offset"] == 0
    assert first["rows"][0][0] == "FAMSUN worksheet 1847"
    assert float(first["rows"][0][1]) == 4.75

    second = json.loads(convert(office_settings, data, ext, "sheet", 1, 1))
    assert second["sheet"] == second["offset"] == 1
    assert (second["totalRows"], second["totalColumns"]) == (2, 2)
    assert len(second["rows"]) == 1
    assert second["rows"][0][0] == "验证"
    assert float(second["rows"][0][1]) == 12


def test_excel_97_sheet_preserves_typed_values_and_cached_formula(office_settings):
    # Genuine BIFF8 date, boolean, error, and numeric SUM formula records.
    data = (FIXTURES.parent / "typed-cells.xls").read_bytes()
    grid = json.loads(convert(office_settings, data, "xls", "sheet", 0, 0))
    assert grid["sheets"] == ["Typed cells"]
    assert (grid["totalRows"], grid["totalColumns"]) == (2, 5)
    assert grid["rows"] == [
        ["Timestamp", "Enabled", "Disabled", "Division error", "Cached total"],
        ["2024-02-29T06:15:30", "TRUE", "FALSE", "#DIV/0!", "10.5"],
    ]
