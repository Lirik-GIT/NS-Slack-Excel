import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

from .catalog import HOURS_COL, INPUTS, PRICING, QUESTIONS_TAB, STEP1, STEP2, TemplateError

_MACRO = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script" script:name="Module1" script:language="StarBasic">
Sub RecalcFile(path As String)
  Dim args(0) As New com.sun.star.beans.PropertyValue
  args(0).Name = "Hidden"
  args(0).Value = True
  Dim doc As Object
  doc = StarDesktop.loadComponentFromURL(ConvertToURL(path), "_blank", 0, args())
  doc.calculateAll()
  Dim out(0) As New com.sun.star.beans.PropertyValue
  out(0).Name = "FilterName"
  out(0).Value = "Calc MS Excel 2007 XML"
  doc.storeToURL(ConvertToURL(path), out())
  doc.close(True)
End Sub
</script:module>
"""

_XLB = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:library PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "library.dtd">
<library:library xmlns:library="http://openoffice.org/2000/library" library:name="Standard" library:readonly="false" library:passwordprotected="false">
 <library:element library:name="Module1"/>
</library:library>
"""


class RecalcError(Exception):
    pass


@dataclass(frozen=True)
class Results:
    dev_days: float
    total_hours: float
    onsite_rate: float
    offshore_rate: float
    onsite_share: float
    blended_rate: float
    price: float
    rom_low: float
    rom_high: float
    price_cad: float
    weeks_requirements: int
    weeks_build: int
    weeks_uat: int
    weeks_golive: int
    weeks_total: int
    milestones: tuple


class Recalculator:
    def __init__(self, soffice_path, profile_dir, timeout):
        self._soffice = soffice_path
        self._profile = Path(profile_dir)
        self._timeout = timeout
        self._lock = threading.Lock()
        self._ready = False

    def _prepare(self):
        if self._ready:
            return
        self._profile.mkdir(parents=True, exist_ok=True)
        url = self._profile.resolve().as_uri()
        basic = self._profile / "user" / "basic" / "Standard"
        if not (basic / "Module1.xba").exists():
            try:
                subprocess.run(
                    [self._soffice, "--headless", "--norestore", "--terminate_after_init", f"-env:UserInstallation={url}"],
                    capture_output=True, timeout=self._timeout, check=False,
                )
            except FileNotFoundError as exc:
                raise RecalcError("LibreOffice (soffice) is not installed") from exc
            basic.mkdir(parents=True, exist_ok=True)
            (basic / "Module1.xba").write_text(_MACRO, encoding="utf-8")
            if not (basic / "script.xlb").exists():
                (basic / "script.xlb").write_text(_XLB, encoding="utf-8")
        self._ready = True

    def recalc(self, path):
        path = Path(path).resolve()
        with self._lock:
            self._prepare()
            url = self._profile.resolve().as_uri()
            before = path.stat().st_mtime_ns
            macro = f'macro:///Standard.Module1.RecalcFile("{path}")'
            try:
                proc = subprocess.run(
                    [self._soffice, "--headless", "--norestore", "--nologo", f"-env:UserInstallation={url}", macro],
                    capture_output=True, timeout=self._timeout, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RecalcError("Excel recalculation timed out") from exc
            if path.stat().st_mtime_ns == before:
                raise RecalcError(f"Excel recalculation did not run: {proc.stderr.decode(errors='ignore')[:300]}")


def _working_copy(template_path, dest):
    shutil.copyfile(template_path, dest)
    wb = load_workbook(dest)
    for name in list(wb.defined_names):
        value = str(wb.defined_names[name].value or "")
        if "#REF!" in value or "[" in value:
            del wb.defined_names[name]
    return wb


def write_inputs(template_path, dest, catalog, scope, bank_version):
    wb = _working_copy(template_path, dest)
    ws = wb[INPUTS]
    for qid, row in catalog.answer_rows.items():
        entry = scope.answers[qid]
        ws.cell(row, 3).value = entry["value"]
        ws.cell(row, 4).value = entry["source"] + (" (reads differed)" if entry.get("conflict") else "")
        ws.cell(row, 5).value = entry.get("evidence") or None
    for row, item in zip(catalog.approval_rows, scope.approvals):
        ws.cell(row, 2).value = item["process"]
        ws.cell(row, 3).value = item["levels"]
        ws.cell(row, 4).value = item.get("source", "Requirement")
        ws.cell(row, 5).value = item.get("quote") or None
    for row, item in zip(catalog.custom_rows, scope.custom_items):
        ws.cell(row, 2).value = item["name"]
        ws.cell(row, 3).value = item["type"]
        ws.cell(row, 4).value = item["size"]
        ws.cell(row, 6).value = item.get("source", "Requirement")
        ws.cell(row, 7).value = item.get("quote")
    for row, item in zip(catalog.migration_rows, scope.migration_objects):
        ws.cell(row, 2).value = item["name"]
        ws.cell(row, 3).value = item["size"]
        ws.cell(row, 5).value = item.get("source", "Requirement")
        ws.cell(row, 6).value = item.get("quote")
    _questions_tab(wb, scope, bank_version)
    wb.save(dest)


def _questions_tab(wb, scope, bank_version):
    if QUESTIONS_TAB in wb.sheetnames:
        del wb[QUESTIONS_TAB]
    ws = wb.create_sheet(QUESTIONS_TAB, 2)
    ws["A1"] = "Questions and assumptions"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Question bank version {bank_version[:12]}"
    ws.append([])
    ws.append(["Type", "ID", "Question", "Triggered by", "ROM assumes"])
    for c in ws[4]:
        c.font = Font(bold=True)
    for q in scope.questions:
        t = q.get("trigger")
        trig = "" if not t else (f"keyword '{t['detail']}' in S{t['sentence']}" if t["how"] == "keyword"
                                 else f"S{t['sentence']}: \"{t['detail']}\"")
        ws.append([q["type"], q["id"], q["question"], trig, q.get("assumes", "")])
    ws.append([])
    ws.append(["Assumptions and exclusions"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for a in scope.assumptions:
        ws.append([a])
    for col, width in zip("ABCDE", (10, 10, 70, 40, 50)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=5):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")


def write_pricing(path, catalog, pricing):
    wb = load_workbook(path)
    ws = wb[PRICING]
    for key, value in pricing.items():
        if key not in ("onsite_rate", "offshore_rate", "onsite_share"):
            raise ValueError(f"Unknown pricing input {key}")
        ws[catalog.pricing_cells[key]].value = value
    wb.save(path)


def read_pricing_inputs(path, catalog):
    ws = load_workbook(path, data_only=True)[PRICING]
    return {k: ws[catalog.pricing_cells[k]].value for k in ("onsite_rate", "offshore_rate", "onsite_share")}


def read_results(path, catalog):
    wb = load_workbook(path, data_only=True)
    s2, pr = wb[STEP2], wb[PRICING]

    def num(ws, ref):
        v = ws[ref].value
        if not isinstance(v, (int, float)):
            raise RecalcError(f"{ws.title}!{ref} did not calculate")
        return float(v)

    p = {k: num(pr, ref) for k, ref in catalog.pricing_cells.items()}
    milestones = tuple((num(pr, f"B{r}"), num(pr, f"C{r}")) for r in catalog.milestone_rows)
    return Results(
        dev_days=num(s2, catalog.dev_days_cell),
        total_hours=p["total_hours"],
        onsite_rate=p["onsite_rate"],
        offshore_rate=p["offshore_rate"],
        onsite_share=p["onsite_share"],
        blended_rate=p["blended_rate"],
        price=p["price"],
        rom_low=p["rom_low_usd"],
        rom_high=p["rom_high_usd"],
        price_cad=p["price_cad"],
        weeks_requirements=int(p["weeks_requirements"]),
        weeks_build=int(p["weeks_build"]),
        weeks_uat=int(p["weeks_uat"]),
        weeks_golive=int(p["weeks_golive"]),
        weeks_total=int(p["weeks_total"]),
        milestones=milestones,
    )


def read_inputs(path, catalog):
    ws = load_workbook(path, data_only=True)[INPUTS]
    answers = {}
    for qid, row in catalog.answer_rows.items():
        answers[qid] = {"value": _plain(ws.cell(row, 3).value), "source": _text(ws.cell(row, 4).value),
                        "evidence": _text(ws.cell(row, 5).value)}
    approvals = [{"process": _text(ws.cell(r, 2).value), "levels": _plain(ws.cell(r, 3).value)}
                 for r in catalog.approval_rows if ws.cell(r, 2).value or ws.cell(r, 3).value]
    custom = [{"name": _text(ws.cell(r, 2).value), "type": _text(ws.cell(r, 3).value), "size": _text(ws.cell(r, 4).value)}
              for r in catalog.custom_rows if ws.cell(r, 2).value or ws.cell(r, 3).value]
    migration = [{"name": _text(ws.cell(r, 2).value), "size": _text(ws.cell(r, 3).value)}
                 for r in catalog.migration_rows if ws.cell(r, 2).value or ws.cell(r, 3).value]
    return answers, approvals, custom, migration


def read_line_hours(path, catalog):
    s1 = load_workbook(path, data_only=True)[STEP1]
    out = {}
    for line in catalog.lines + (catalog.custom_line,):
        v = s1.cell(line.row, HOURS_COL).value
        if isinstance(v, (int, float)) and v > 0:
            out[line.line_id] = {"hours": float(v), "name": line.name, "section": line.section}
    return out


def check_template(path):
    wb = load_workbook(path, read_only=True)
    for name in (INPUTS, PRICING, STEP1, STEP2):
        if name not in wb.sheetnames:
            raise TemplateError(f"Uploaded file is not the estimator workbook (missing '{name}')")


def _plain(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        return " ".join(v.split())
    return v


def _text(v):
    return " ".join(str(v).split()) if v is not None else ""


def temp_dir(base):
    Path(base).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base))
