"""The AE-facing PDF and emails must never carry internal detail. Canary values are planted in every internal field of the
fixture, a real PDF is rendered through LibreOffice, and the extracted text is searched for them."""
import io
import json

import pytest
from pypdf import PdfReader

from app import pdf
from app.questions import ADDON

CANARIES = ["CANARY_LINE_ITEM", "CANARY_REASON", "CANARY_NOTE", "CANARY_WORKBOOK", "CANARY_ERROR", "CANARY_ISSUE",
            "CANARY_CONFLICT", "CANARY_FORMFLAG", "CANARY_CELL_LABEL", "7,777,001", "7777001", "10,888,201", "1,234.56",
            "$988", "$346", "987.65", "345.67", "9,876.5", "9876.5", "4321.5"]

RESULTS = {"price": 7777001, "price_cad": 10888201, "rom_low": 15000, "rom_high": 22000, "blended_rate": 1234.56,
           "onsite_rate": 987.65, "offshore_rate": 345.67, "onsite_share": 0.2, "total_hours": 9876.5, "dev_days": 4321.5,
           "weeks_requirements": 2, "weeks_build": 2, "weeks_uat": 1, "weeks_golive": 2, "weeks_total": 7,
           "milestones": [[0.5, 1], [0.2, 1], [0.2, 1], [0.1, 1]]}

INTERP = {
    "scope_summary": "ARM on an existing NetSuite account with contract migration.",
    "answers": {"Q01": {"value": ADDON, "source": "Form"}, "Q39": {"value": "Yes", "source": "Form"},
                "Q02": {"value": 3, "source": "Requirement", "evidence": "3 subsidiaries"}},
    "approvals": [], "custom_items": [{"name": "Revenue plan recalculation", "type": "Script", "size": "Medium"}],
    "migration_objects": [], "assumptions": ["Priced for one subsidiary.", "Country localization outside US and Canada excluded"],
    "not_covered": [{"n": 5, "text": "Go-live is planned for March."}],
    "questions": [{"type": "Gap", "id": "Q02", "question": "How many subsidiaries?", "assumes": "1"}],
    "conflicts": ["CANARY_CONFLICT differed"], "form_flags": ["CANARY_FORMFLAG"], "issues": ["CANARY_ISSUE"],
    "line_hours": {"L1": {"hours": 9876.5, "name": "CANARY_LINE_ITEM", "section": "CANARY_CELL_LABEL"}},
}

REQ = {"id": "REQ1234567", "customer": "RiverBed", "requester_id": "email:ae@oracle.com", "source": "web",
       "review_reasons_json": json.dumps(["CANARY_REASON"]), "decision_note": "CANARY_NOTE",
       "workbook_path": "/srv/data/files/CANARY_WORKBOOK_v1.xlsx", "error": "CANARY_ERROR"}

POLICY = {"ae_message": {"disclaimer": "Rough order of magnitude for early budgeting only. Not a quote.", "max_questions_shown": 10}}


@pytest.fixture(scope="module")
def renderer(tmp_path_factory):
    return pdf.PdfRenderer("soffice", tmp_path_factory.mktemp("lopdf"), 120)


def text_of(data):
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(p.extract_text() for p in reader.pages) + json.dumps(dict(reader.metadata or {}), default=str)


@pytest.mark.parametrize("currency, expected", [("USD", "$15,000 – $22,000 USD"), ("CAD", "C$21,000 – C$31,000 CAD")])
def test_pdf_has_allowed_content_and_no_canaries(renderer, currency, expected):
    data = renderer.render(pdf.content(REQ, INTERP, RESULTS, POLICY, currency), RESULTS)
    assert data.startswith(b"%PDF")
    text = text_of(data)
    for allowed in (expected, "7 weeks", "50% / 20% / 20% / 10%", "Advanced Revenue Management", "Priced for one subsidiary.",
                    "Go-live is planned for March.", "How many subsidiaries?", "Not a quote", "REQ1234567"):
        assert allowed in text.replace("\n", " "), allowed
    leaked = [c for c in CANARIES if c in text]
    assert not leaked, leaked
    assert "/hr" not in text and "blended" not in text.lower() and "rate" not in text.lower().replace("migration", "")


def test_text_starting_with_equals_is_not_evaluated(renderer):
    hostile = {**INTERP, "scope_summary": "=7777+1", "assumptions": ['=HYPERLINK("http://x","click")', "+1+1", "@SUM(1,2)"]}
    req = {**REQ, "customer": "=1+1"}
    text = text_of(renderer.render(pdf.content(req, hostile, RESULTS, POLICY), RESULTS))
    assert "=7777+1" in text and "7778" not in text and "=1+1" in text and 'HYPERLINK("http://x"' in text


def test_guard_refuses_internal_figures():
    doc = pdf.content(REQ, INTERP, RESULTS, POLICY)
    for bad in ("Total $7,777,001", "billed at $1,234.56 blended", "$988 onsite", "64 /hr"):
        tainted = {**doc, "sections": doc["sections"] + [("Extra", [bad])]}
        with pytest.raises(pdf.LeakError):
            pdf.check_clean(tainted, RESULTS)
    pdf.check_clean(doc, RESULTS)


def test_content_ignores_internal_fields():
    doc = pdf.content(REQ, INTERP, RESULTS, POLICY)
    flat = json.dumps(doc)
    assert not [c for c in CANARIES if c in flat]
