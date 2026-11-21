"""Worksheet paging limits, including the serialized XLSX reader boundary."""

import hashlib
import io
import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from famsun.config import Settings
from famsun.preview import convert

ROOT = Path(__file__).resolve().parents[1]
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def wide_workbook():
    # Reuse a valid OOXML workbook, changing only the first worksheet. This
    # avoids requiring Excel authoring packages in the application or test host.
    with ZipFile(ROOT / "tests/fixtures/office-formats.zip") as fixtures:
        original = fixtures.read("report.xlsx")

    def string(row, reference, value):
        cell = ET.SubElement(row, NS + "c", r=reference, t="inlineStr")
        ET.SubElement(ET.SubElement(cell, NS + "is"), NS + "t").text = value

    result = io.BytesIO()
    with (
        ZipFile(io.BytesIO(original)) as source,
        ZipFile(result, "w", ZIP_DEFLATED) as dest,
    ):
        for name in source.namelist():
            data = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                root.find(NS + "dimension").set("ref", "A1:AW82")
                body = root.find(NS + "sheetData")
                body.clear()
                for n in range(1, 83):
                    row = ET.SubElement(body, NS + "row", r=str(n))

                    string(row, f"A{n}", f"row-{n:03}")
                    if n == 1:
                        string(row, "B1", "界" * 512)
                        string(row, "C1", "界" * 513)
                        cached = ET.SubElement(row, NS + "c", r="D1", t="n")
                        ET.SubElement(cached, NS + "f").text = "1+2"
                        ET.SubElement(cached, NS + "v").text = "3"
                        formula = ET.SubElement(row, NS + "c", r="E1")
                        ET.SubElement(formula, NS + "f").text = "SUM(1,2)"
                        for col, kind, val in [
                            ("F", "b", "0"),
                            ("G", "d", "2026-11-21T02:30:00"),
                            ("H", "e", "#DIV/0!"),
                        ]:
                            cell = ET.SubElement(row, NS + "c", r=col + "1", t=kind)
                            ET.SubElement(cell, NS + "v").text = val
                    string(row, f"AV{n}", "visible column 48")
                    string(row, f"AW{n}", "outside column limit")
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            dest.writestr(name, data)
    return result.getvalue()


def test_page_budget_includes_truncation_markers_and_shape():
    code = runpy.run_path(str(ROOT / "deploy/preview/sheet.py"))
    # Unique strings make it clear that the actual prefixes survive truncation.
    cells = [
        [hashlib.sha256(f"{i}:{j}".encode()).hexdigest() * 10 for j in range(50)]
        for i in range(85)
    ]
    page = code["pack"](["sheet"], 0, 0, 85, 50, cells)
    assert len(page["rows"]) == 80
    assert all(len(row) == 48 for row in page["rows"])
    values = [val for row in page["rows"] for val in row]
    assert all(len(value) <= 512 for value in values)
    assert sum(map(len, values)) == 180_000
    assert values[0] == cells[0][0][:511] + "…"
    assert values[-1] == "" and page["textClipped"]
    untouched = code["pack"](["sheet"], 0, 0, 1, 1, [["界" * 512]])
    assert untouched["rows"] == [["界" * 512]] and not untouched["textClipped"]


def test_real_xlsx_page_and_column_boundaries_and_formula_cache():
    settings = Settings(allow_unenforced_cgroups=True)
    if (
        not shutil.which("docker")
        or subprocess.run(
            ["docker", "image", "inspect", settings.preview_image],
            capture_output=True,
            check=False,
        ).returncode
    ):
        if os.getenv("FAMSUN_REQUIRE_DOCKER_TESTS") == "true":
            pytest.fail("the Office image is required for the XLSX paging checks")
        pytest.skip("build the Office image to exercise the real Excel reader")
    data = wide_workbook()

    def page(offset):
        return json.loads(convert(settings, data, "xlsx", "sheet", 0, offset))

    start = page(0)
    assert (
        start["totalRows"],
        start["totalColumns"],
        start["pageSize"],
        start["columnLimit"],
    ) == (82, 49, 80, 48)
    assert len(start["rows"]) == 80 and start["rows"][-1][0] == "row-080"
    assert all(
        len(row) == 48 and row[-1] == "visible column 48" for row in start["rows"]
    )
    assert "outside column limit" not in str(start) and start["textClipped"]
    assert start["rows"][0][:8] == [
        "row-001",
        "界" * 512,
        "界" * 511 + "…",
        "3",
        "=SUM(1,2)",
        "FALSE",
        "2026-11-21T02:30:00",
        "#DIV/0!",
    ]
    last = page(80)
    assert [row[0] for row in last["rows"]] == ["row-081", "row-082"]
    assert last["offset"] == 80 and not last["textClipped"]
    assert page(82)["rows"] == []
