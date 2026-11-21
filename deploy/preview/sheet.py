"""Read one bounded page of a workbook inside the isolated preview container."""

import json
import sys
from datetime import date, datetime, time, timedelta
from itertools import islice
from pathlib import Path

ROWS = 80
COLUMNS = 48
MAX_ROWS = 1_048_576
MAX_SHEETS = 1024
CELL_CHARS = 512
PAGE_CHARS = 180_000


def format_value(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def pack(names, sheet_index, offset, nrows, ncols, values):
    if len(names) > MAX_SHEETS:
        raise OverflowError
    rows = []
    remaining = PAGE_CHARS
    clipped = False
    for row in islice(values, ROWS):
        cells = []
        for value in islice(row, COLUMNS):
            text = format_value(value)
            limit = min(remaining, CELL_CHARS)
            if len(text) > limit:
                text = text[: limit - 1] + "…" if limit else ""
                clipped = True
            remaining = max(0, remaining - len(text))
            cells.append(text)
        rows.append(cells)
    return {
        "sheets": names,
        "sheet": sheet_index,
        "offset": offset,
        "totalRows": nrows,
        "totalColumns": ncols,
        "rows": rows,
        "pageSize": ROWS,
        "columnLimit": COLUMNS,
        "textClipped": clipped,
    }


def open_xml(path, index, offset):
    from openpyxl import load_workbook

    cached = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        sheets = [s for s in cached.worksheets if s.sheet_state == "visible"]
        sheet = sheets[index]
        names = [s.title for s in sheets]
        if sheet.max_row is None or sheet.max_column is None:
            sheet.calculate_dimension(force=True)
        nrows, ncols = sheet.max_row, sheet.max_column
        area = {
            "min_row": offset + 1,
            "max_row": min(offset + ROWS, nrows),
            "max_col": min(ncols, COLUMNS),
            "values_only": True,
        }
        if offset >= nrows:
            return pack(names, index, offset, nrows, ncols, ())
        # Office documents created by Python frequently have no formula cache.
        # In that case show the formula as inert text, with no local evaluation.
        formulas = load_workbook(
            path, read_only=True, data_only=False, keep_links=False
        )
        try:
            paired = zip(
                sheet.iter_rows(**area), formulas[sheet.title].iter_rows(**area)
            )
            rows = (
                [a if a is not None else b for a, b in zip(x, y)] for x, y in paired
            )
            return pack(names, index, offset, nrows, ncols, rows)
        finally:
            formulas.close()
    finally:
        cached.close()


def legacy(path, index, offset):
    import xlrd

    workbook = xlrd.open_workbook(str(path), on_demand=True)
    try:
        # xlrd stores worksheet visibility in workbook order, without loading cells.
        visible = [i for i, flag in enumerate(workbook._sheet_visibility) if flag == 0]
        sheet = workbook.sheet_by_index(visible[index])
        all_names = workbook.sheet_names()

        def value(row, column):
            cell = sheet.cell(row, column)
            if cell.ctype == xlrd.XL_CELL_DATE:
                return xlrd.xldate_as_datetime(cell.value, workbook.datemode)
            if cell.ctype == xlrd.XL_CELL_BOOLEAN:
                return bool(cell.value)
            if cell.ctype == xlrd.XL_CELL_ERROR:
                return xlrd.error_text_from_code.get(cell.value, "#ERROR")
            return cell.value

        rows = (
            [value(r, c) for c in range(min(COLUMNS, sheet.ncols))]
            for r in range(offset, min(offset + ROWS, sheet.nrows))
        )
        return pack(
            [all_names[i] for i in visible],
            index,
            offset,
            sheet.nrows,
            sheet.ncols,
            rows,
        )
    finally:
        workbook.release_resources()


if __name__ == "__main__":
    try:
        path, index, offset = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
        if index < 0 or index >= MAX_SHEETS or offset < 0 or offset >= MAX_ROWS:
            raise ValueError
        reader = {".xlsx": open_xml, ".xls": legacy}[path.suffix.lower()]
        print(
            json.dumps(reader(path, index, offset), ensure_ascii=False, allow_nan=False)
        )
    except (MemoryError, OverflowError):
        sys.exit(3)
    except Exception:  # noqa: BLE001 - malformed workbook exceptions vary across both parsers.
        sys.exit(2)
