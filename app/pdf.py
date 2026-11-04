"""The ROM PDF an Oracle AE receives.

Content comes from the same data and helpers as the Slack requester view (slack_ui.ae_blocks), so it can only hold what an
AE may see: the ROM range, duration, milestones, scope as understood, assumptions, exclusions and open questions. It never
reads line hours, rates, review reasons, reviewer notes or the estimator workbook. `check_clean` is a last guard.
The PDF is rendered by LibreOffice Calc (already in the image) from a small print-formatted workbook.
"""
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from . import slack_ui as ui


class LeakError(Exception):
    pass


class PdfError(Exception):
    pass


def rom_range(results, currency="USD"):
    """(low, high, symbol). CAD scales the USD range by the workbook's own CAD/USD ratio, rounded to 1,000."""
    if currency == "CAD" and results["price"]:
        ratio = results["price_cad"] / results["price"]
        return round(results["rom_low"] * ratio, -3), round(results["rom_high"] * ratio, -3), "C$"
    return results["rom_low"], results["rom_high"], "$"


def _plain(text):
    return re.sub(r"\s+", " ", str(text).replace("*", "")).strip()


def content(req, interp, results, policy, currency="USD"):
    """Everything that goes on the page: {"title", "headline", "meta", "sections": [(heading, [lines])], "footer"}."""
    low, high, sym = rom_range(results, currency)
    sections = []
    if interp.get("scope_summary"):
        sections.append(("Scope as understood", [interp["scope_summary"]]))
    included = [f"• {s}" for s in ui._in_scope(interp["answers"])] + ui._facts(interp["answers"], False) + \
        [i for i in ui._items(interp) if "Standard approval workflow" not in i]
    sections.append(("What is included", included))
    if interp.get("assumptions"):
        sections.append(("Assumptions and exclusions", [f"• {a}" for a in interp["assumptions"]]))
    if interp.get("not_covered"):
        sections.append(("Not priced in this ROM", [f"• {n['text']}" for n in interp["not_covered"]]))
    if interp.get("questions"):
        sections.append(("Questions to confirm with the customer",
                         ui._question_lines(interp["questions"], policy["ae_message"].get("max_questions_shown", 10), False)))
    return {
        "title": f"NetSuite ROM estimate: {req['customer']}",
        "headline": f"ROM price range: {sym}{low:,.0f} – {sym}{high:,.0f} {currency}",
        "meta": [f"Duration: {ui._duration(results)}", f"Payment milestones: {ui._milestone_split(results)}",
                 f"Reference: {req['id']}"],
        "sections": [(h, [_plain(x) for x in lines if _plain(x)]) for h, lines in sections],
        "footer": policy["ae_message"]["disclaimer"],
    }


def _all_text(doc):
    return "\n".join([doc["title"], doc["headline"], *doc["meta"], doc["footer"],
                      *(t for h, lines in doc["sections"] for t in (h, *lines))])


def check_clean(doc, results):
    """Refuse to render if an exact price, the blended rate or a rate figure slipped into the text."""
    text = _all_text(doc)
    banned = [f"{results['price']:,.0f}", f"{results['price']:.0f}", f"{results['price_cad']:,.0f}", f"{results['price_cad']:.0f}",
              f"{results['blended_rate']:,.2f}", f"${results['onsite_rate']:,.0f}", f"${results['offshore_rate']:,.0f}"]
    hit = [b for b in banned if b and b in text]
    if hit or re.search(r"/\s*hr\b|blended", text, re.IGNORECASE):
        raise LeakError("internal pricing detail found in the AE document")


def _workbook(doc, owner):
    wb = Workbook()
    ws = wb.active
    ws.title = "ROM"
    wb.properties.creator = owner
    wb.properties.title = doc["title"]
    ws.column_dimensions["A"].width = 105
    wrap = Alignment(wrap_text=True, vertical="top")
    row = 1

    def put(text, font=None, fill=None):
        nonlocal row
        cell = ws.cell(row, 1, text)
        cell.alignment = wrap
        if font:
            cell.font = font
        if fill:
            cell.fill = fill
        row += 1

    put(doc["title"], Font(name="Liberation Sans", size=16, bold=True))
    put(doc["headline"], Font(name="Liberation Sans", size=13, bold=True))
    for line in doc["meta"]:
        put(line, Font(name="Liberation Sans", size=10))
    for heading, lines in doc["sections"]:
        row += 1
        put(heading, Font(name="Liberation Sans", size=11, bold=True), PatternFill("solid", fgColor="E8EEF4"))
        for line in lines:
            put(line, Font(name="Liberation Sans", size=10))
    row += 1
    put(doc["footer"], Font(name="Liberation Sans", size=9, italic=True))
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.7
    ws.print_options.gridLines = False
    return wb


class PdfRenderer:
    def __init__(self, soffice_path, profile_dir, timeout=120, owner="Lirik"):
        self._soffice, self._profile, self._timeout, self._owner = soffice_path, Path(profile_dir), timeout, owner
        self._lock = threading.Lock()  # one LibreOffice process per profile

    def render(self, doc, results):
        """Return the PDF bytes for `doc`, after the leak guard."""
        check_clean(doc, results)
        with tempfile.TemporaryDirectory(prefix="rompdf") as tmp:
            src = Path(tmp) / "ROM.xlsx"
            _workbook(doc, self._owner).save(src)
            with self._lock:
                self._profile.mkdir(parents=True, exist_ok=True)
                try:
                    proc = subprocess.run(
                        [self._soffice, "--headless", "--norestore", "--nologo", f"-env:UserInstallation={self._profile.resolve().as_uri()}",
                         "--convert-to", "pdf", "--outdir", tmp, str(src)],
                        capture_output=True, timeout=self._timeout, check=False)
                except FileNotFoundError as exc:
                    raise PdfError("LibreOffice (soffice) is not installed") from exc
                except subprocess.TimeoutExpired as exc:
                    raise PdfError("PDF conversion timed out") from exc
            out = Path(tmp) / "ROM.pdf"
            if not out.exists():
                raise PdfError(f"PDF conversion failed: {proc.stderr.decode(errors='ignore')[:300]}")
            return out.read_bytes()
