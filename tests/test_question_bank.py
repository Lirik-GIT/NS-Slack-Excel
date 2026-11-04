import pytest
from openpyxl import Workbook

from app.question_bank import COLUMNS, SHEET, BankError, keyword_hits, load_bank


def test_bank_loads(bank):
    assert len(bank.questions) == 74
    assert bank.get("LOC-01").question.startswith("Which local tax")
    assert bank.get("P2P-02").skip == ("Q17",)
    assert bank.get("MFG-04").flags_reviewer and not bank.get("P2P-02").flags_reviewer
    assert len(bank.sha256) == 64


def test_keywords_match_whole_words_only(bank):
    hits = keyword_hits(bank, ["We run OneWorld with a Tokyo entity.", "Need 3-way match and Avalara.",
                               "Our Japanese supplier ships parts.", "Mexicoland is a brand name."])
    assert hits["LOC-01"] == {"sentence": 1, "keyword": "tokyo"}
    assert hits["LOC-03"]["keyword"] == "avalara" and hits["P2P-03"]["keyword"] == "3-way"
    assert all(h["sentence"] != 3 for h in hits.values())
    assert all(h["sentence"] != 4 for h in hits.values())


def _write(tmp_path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(list(COLUMNS.values()))
    for r in rows:
        ws.append(r)
    path = tmp_path / "bank.xlsx"
    wb.save(path)
    return path


@pytest.mark.parametrize("row,message", [
    (["bad id", "A", "when", "k", "q", "c", "l", "", "a"], "ID must look like"),
    (["ABC-01", "A", "", "k", "q", "c", "l", "", "a"], "When to ask"),
    (["ABC-01", "A", "when", "k", "", "c", "l", "", "a"], "question is empty"),
    (["ABC-01", "A", "when", "k", "q", "c", "l", "Q99", "a"], "unknown question IDs"),
])
def test_bad_rows_stop_startup(tmp_path, row, message):
    with pytest.raises(BankError, match=message):
        load_bank(_write(tmp_path, [row]))


def test_duplicate_ids_rejected(tmp_path):
    row = ["ABC-01", "A", "when", "k", "q", "c", "l", "", "a"]
    with pytest.raises(BankError, match="duplicate"):
        load_bank(_write(tmp_path, [row, row]))
