"""One document per networkless, non-root container. Only preview bytes go to stdout."""

import json
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

MAX_INPUT = 32 * 1024 * 1024
MAX_PDF = 32 * 1024 * 1024
MAX_SHEET = 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
INVALID, TOO_LARGE, LOCKED, CONFIG, TIMEOUT = 40, 41, 42, 44, 45
PACKAGES = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
    "ods": "content.xml",
    "odt": "content.xml",
    "odp": "content.xml",
}
ODF = {"ods": "spreadsheet", "odt": "text", "odp": "presentation"}
OLE = bytes.fromhex("d0cf11e0a1b11ae1")


class InvalidDocument(Exception):
    pass


def check_source(path, ext):
    with path.open("rb") as source:
        magic = source.read(16)
    if ext == "rtf" or (ext == "doc" and magic.startswith(b"{\\rtf")):
        if not magic.startswith(b"{\\rtf"):
            raise InvalidDocument
        return
    if ext in {"doc", "xls", "ppt"}:
        if not magic.startswith(OLE):
            raise InvalidDocument
        return
    if ext not in PACKAGES:
        raise InvalidDocument
    if magic.startswith(OLE):
        sys.exit(LOCKED)  # Encrypted OOXML is wrapped in an OLE container.
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 4096:
            raise OverflowError
        total, seen = 0, set()
        for item in entries:
            name = item.filename
            total += item.file_size
            if (
                total > MAX_EXPANDED
                or item.file_size > 64 * 1024 * 1024
                or (
                    item.file_size > 2 * 1024 * 1024
                    and item.file_size > max(1, item.compress_size) * 500
                )
            ):
                raise OverflowError
            if item.flag_bits & 1:
                sys.exit(LOCKED)
            if (
                len(name) > 1024
                or name.startswith("/")
                or "\\" in name
                or ".." in name.split("/")
                or name in seen
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise InvalidDocument
            if name.lower().endswith("/vbaproject.bin"):
                raise InvalidDocument
            seen.add(name)
        if PACKAGES[ext] not in seen:
            raise InvalidDocument
        if ext in ODF:
            if (
                "mimetype" not in seen
                or archive.getinfo("mimetype").file_size > 128
                or archive.read("mimetype")
                != ("application/vnd.oasis.opendocument." + ODF[ext]).encode()
            ):
                raise InvalidDocument
        elif "[Content_Types].xml" not in seen:
            raise InvalidDocument


def export_pdf(source, output):
    import uno
    import unohelper
    from com.sun.star.beans import PropertyValue
    from com.sun.star.connection import NoConnectException
    from com.sun.star.task import XInteractionHandler

    class AbortPrompts(unohelper.Base, XInteractionHandler):
        def handle(self, interaction):
            abort_type = uno.getTypeByName("com.sun.star.task.XInteractionAbort")
            for item in interaction.getContinuations():
                refusal = item.queryInterface(abort_type)
                if refusal is not None:
                    refusal.select()
                    return

    def prop(key, value):
        p = PropertyValue()
        p.Name, p.Value = key, value
        return p

    pipe = "pipe,name=preview;urp;StarOffice.ComponentContext"
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local
    )
    server = subprocess.Popen(
        [
            "/usr/bin/soffice",
            "-env:UserInstallation=file:///tmp/profile",
            "--headless",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--nofirststartwizard",
            "--accept=" + pipe,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(150):
            try:
                context = resolver.resolve("uno:" + pipe)
                break
            except NoConnectException:
                if server.poll() is not None:
                    sys.exit(CONFIG)
                time.sleep(0.1)
        else:
            sys.exit(TIMEOUT)
        desktop = context.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", context
        )
        options = (
            prop("Hidden", True),
            prop("ReadOnly", True),
            prop("InteractionHandler", AbortPrompts()),
            prop(
                "MacroExecutionMode",
                uno.Any(
                    "short",
                    uno.getConstantByName(
                        "com.sun.star.document.MacroExecMode.NEVER_EXECUTE"
                    ),
                ),
            ),
            prop(
                "UpdateDocMode",
                uno.Any(
                    "short",
                    uno.getConstantByName(
                        "com.sun.star.document.UpdateDocMode.NO_UPDATE"
                    ),
                ),
            ),
        )
        doc = desktop.loadComponentFromURL(source.as_uri(), "_blank", 0, options)
        if doc is None:
            raise InvalidDocument
        try:
            ext = source.suffix[1:]
            if ext in {"doc", "docx", "odt", "rtf"}:
                kind, exporter = "text.TextDocument", "writer_pdf_Export"
            elif ext in {"xls", "xlsx", "ods"}:
                kind, exporter = "sheet.SpreadsheetDocument", "calc_pdf_Export"
            else:
                kind, exporter = (
                    "presentation.PresentationDocument",
                    "impress_pdf_Export",
                )
            if not doc.supportsService("com.sun.star." + kind):
                raise InvalidDocument
            doc.storeToURL(output.as_uri(), (prop("FilterName", exporter),))
        finally:
            doc.dispose()
    finally:
        server.terminate()
        try:
            server.wait(timeout=2)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def main(args):
    ext, view, sheet, offset = args
    if ext not in set(PACKAGES) | {"doc", "xls", "ppt", "rtf"} or view not in {
        "layout",
        "sheet",
    }:
        return INVALID
    Path(os.environ["HOME"]).mkdir(mode=0o700, exist_ok=True)
    source = Path("/tmp/input." + ext)
    content = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(content) > MAX_INPUT:
        return TOO_LARGE
    source.write_bytes(content)
    del content
    check_source(source, ext)
    output = Path("/tmp/result")
    if view == "sheet":
        with output.open("wb") as stream:
            result = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "/opt/famsun/sheet.py",
                    str(source),
                    sheet,
                    offset,
                ],
                stdout=stream,
                stderr=subprocess.DEVNULL,
                timeout=40,
                check=False,
            )
        if result.returncode:
            return TOO_LARGE if result.returncode == 3 else INVALID
        if output.stat().st_size > MAX_SHEET:
            return TOO_LARGE
        if not isinstance(json.loads(output.read_bytes()), dict):
            return INVALID
    else:
        result = subprocess.run(
            ["/usr/bin/python3", "-I", __file__, "--pdf", str(source), str(output)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=55,
            check=False,
        )
        if result.returncode:
            return (
                result.returncode if result.returncode in {CONFIG, TIMEOUT} else INVALID
            )
        if output.stat().st_size > MAX_PDF:
            return TOO_LARGE
        with output.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                return INVALID
    with output.open("rb") as stream:
        shutil.copyfileobj(stream, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--pdf"]:
            export_pdf(Path(sys.argv[2]), Path(sys.argv[3]))
        else:
            sys.exit(main(sys.argv[1:]))
    except (InvalidDocument, ValueError, OSError, zipfile.BadZipFile, KeyError):
        sys.exit(INVALID)
    except OverflowError:
        sys.exit(TOO_LARGE)
    except subprocess.TimeoutExpired:
        sys.exit(TIMEOUT)
