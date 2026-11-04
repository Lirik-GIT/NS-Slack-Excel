from app.interpreter import UNMAPPED, validate
from app.questions import coerce
from app.question_bank import keyword_hits
from app.resolve import resolve
from app.sentences import quote_in, split
from tests.conftest import MAX_ITEMS, payload

REQ = "We are a US distributor with 3 subsidiaries. We collect tax in 12 states.\n- Need 4 PDF templates for POs\nThanks!"


def test_split_and_quotes():
    s = split(REQ)
    assert s == ["We are a US distributor with 3 subsidiaries.", "We collect tax in 12 states.", "Need 4 PDF templates for POs", "Thanks!"]
    assert quote_in("collect tax in 12 states", s, [2])
    assert quote_in("COLLECT TAX  in 12 states.", s, [2])
    assert not quote_in("collect tax in 12 states", s, [1])
    assert not quote_in("tax in 15 states", s, [2])
    assert not quote_in("a", s, [2])


def test_coerce():
    assert coerce("Q06", "12") == 12 and coerce("Q06", "1,200") == 1200
    assert coerce("Q06", "-1") is None and coerce("Q06", "2.5") is None and coerce("Q02", 0) is None
    assert coerce("Q07", "yes") == "Yes" and coerce("Q07", "maybe") is None
    assert coerce("Q18", "3-WAY") == "3-way"


def test_validation_keeps_only_evidenced_answers():
    s = split(REQ)
    raw = payload(answers=[("Q02", 3, 1, "3 subsidiaries"), ("Q06", 12, 2, "made up quote"), ("Q16", "four", 3, "4 PDF"),
                           ("Q99", 1, 1, "3 subsidiaries"), ("Q16", 4, 3, "4 PDF templates")],
                  custom=[("AR report", "Report / saved search", "Small", 1, "not there")],
                  sentence_map=[{"sentence": 1, "covered_by": ["Q02"]}, {"sentence": 2, "covered_by": ["Q06"]},
                                {"sentence": 3, "covered_by": ["Q16"]}, {"sentence": 4, "covered_by": []}])
    r = validate(raw, s, MAX_ITEMS)
    assert r.answers == {"Q02": (3, [1], "3 subsidiaries"), "Q16": (4, [3], "4 PDF templates")}
    assert r.custom_items == []
    assert r.not_covered == {4}
    assert len(r.issues) == 4


def test_unmapped_sentences_trigger_one_retry(catalog, make_interpreter):
    s = split(REQ)
    bad, good = payload(n=2), payload(n=4)
    interp = make_interpreter(bad, good)
    r = interp.read(catalog, "Acme", s, {})
    assert len(interp._client.calls) == 2 and not any(i.startswith(UNMAPPED) for i in r.issues)
    interp = make_interpreter(bad)
    assert interp.read(catalog, "Acme", s, {}).not_covered == {3, 4}


def test_prompt_contents(catalog, make_interpreter):
    interp = make_interpreter(payload(n=4))
    interp.read_twice(catalog, "Acme", split(REQ), {"Q01": "New implementation"})
    first, second = (c["messages"][0]["content"] for c in interp._client.calls)
    assert first.index("Q01 |") < first.index("Q56 |") and second.index("Q56 |") < second.index("Q01 |")
    assert first.index("GEN-01 |") < first.index("GOL-04 |") and second.index("GOL-04 |") < second.index("GEN-01 |")
    assert "Q01 = New implementation" in first and "S2 We collect tax in 12 states." in first
    call = interp._client.calls[0]
    assert "Lirik mapping decisions" in call["system"]
    assert "bank_matches" in call["tools"][0]["input_schema"]["properties"]
    assert "open_questions" not in call["tools"][0]["input_schema"]["properties"]
    assert call["tool_choice"] == {"type": "auto"} and "temperature" not in call


def _reads(a, b, s, bank=None):
    ids = frozenset(q.qid for q in bank.questions) if bank else frozenset()
    return validate(a, s, MAX_ITEMS, ids), validate(b, s, MAX_ITEMS, ids)


def _resolve(form, ra, rb, s, catalog, bank):
    return resolve(form, ra, rb, s, catalog.item_hours, bank, keyword_hits(bank, s), ["Country localization outside US and Canada excluded"])


def test_resolution_rules(catalog, bank):
    s = split(REQ)
    a = payload(answers=[("Q02", 3, 1, "3 subsidiaries"), ("Q06", 12, 2, "12 states"), ("Q16", 4, 3, "4 PDF"),
                         ("Q39", "Yes", 1, "US distributor")], n=4)
    b = payload(answers=[("Q02", 3, 1, "3 subsidiaries"), ("Q06", 21, 2, "12 states"), ("Q08", "No", 1, "distributor")], n=4)
    ra, rb = _reads(a, b, s)
    sc = _resolve({"Q01": "New implementation", "Q07": "Yes"}, ra, rb, s, catalog, bank)
    assert sc.answers["Q01"]["source"] == "Form" and sc.value("Q07") == "Yes"
    assert sc.value("Q02") == 3 and sc.answers["Q02"]["source"] == "Requirement"
    assert sc.value("Q06") == 21 and sc.answers["Q06"].get("conflict")
    assert sc.value("Q16") == 4
    assert sc.value("Q08") == "Yes"
    assert sc.value("Q04") == "Yes" and sc.answers["Q04"]["source"] == "Default"
    assert sc.value("Q54") == 1000 and any(q["id"] == "Q54" and q["type"] == "Gap" for q in sc.questions)
    assert "Includes migration of up to 1,000 open transactions." in sc.assumptions
    assert sc.approvals[0]["process"] == "Standard approval workflow" and sc.approvals[0]["source"] == "Default"
    assert "Includes one single-level approval workflow." in sc.assumptions
    assert sc.value("Q05") == "P2P" and "Includes ACH for vendor payments." in sc.assumptions
    assert sc.assumptions[-1] == "Country localization outside US and Canada excluded"
    assert sc.value("Q39") == "Yes" and any("ARM" in f for f in sc.form_flags)
    assert [c.split()[0] for c in sc.conflicts] == ["Q08", "Q06", "Q16", "Q39"]


def test_add_on_defaults_turn_core_off(catalog, bank):
    s = split(REQ)
    ra, rb = _reads(payload(n=4), payload(n=4), s)
    sc = _resolve({"Q01": "Add-on to existing account"}, ra, rb, s, catalog, bank)
    for qid in ("Q04", "Q07", "Q08", "Q50"):
        assert sc.value(qid) == "No"
    assert sc.value("Q53") == 0 and sc.value("Q54") == 0 and sc.approvals == []


def test_list_conflict_takes_larger_set(catalog, bank):
    s = split(REQ)
    a = payload(custom=[("Sync", "Script", "Small", 3, "PDF templates")], n=4)
    b = payload(custom=[("Sync", "Script", "Large", 3, "PDF templates")], n=4)
    ra, rb = _reads(a, b, s)
    sc = _resolve({"Q01": "New implementation"}, ra, rb, s, catalog, bank)
    assert sc.custom_items[0]["size"] == "Large" and sc.conflicts


BANK_REQ = ("We are opening a Tokyo entity next year. POs need approval. "
            "Some reports are needed. We sell in several US states. Our supplier is in Germany.")


def test_bank_questions_grounded(catalog, bank):
    s = split(BANK_REQ)
    a = payload(n=5, bank=[("LOC-01", 1, "opening a Tokyo entity"), ("REP-01", 3, "Some reports are needed"),
                           ("P2P-02", 2, "POs need approval"), ("CRM-01", 4, "several US states")])
    b = payload(n=5, bank=[("LOC-01", 1, "Tokyo entity"), ("REP-01", 3, "reports are needed"), ("P2P-02", 2, "made up")])
    ra, rb = _reads(a, b, s, bank)
    sc = _resolve({"Q01": "New implementation", "Q07": "Yes", "Q08": "Yes"}, ra, rb, s, catalog, bank)
    by_id = {q["id"]: q for q in sc.questions}
    assert by_id["LOC-01"]["trigger"]["how"] == "keyword"
    assert by_id["REP-01"]["trigger"] == {"how": "keyword", "detail": "reports", "sentence": 3}
    assert by_id["P2P-02"]["trigger"]["how"] == "keyword"
    assert "CRM-01" not in by_id
    assert "LOC-01" in [q["id"] for q in sc.questions]
    assert "Q06" in by_id and by_id["Q06"]["type"] == "Gap"
    assert "Country localization outside US and Canada excluded" in sc.assumptions
    assert sc.assumptions.count("Country localization outside US and Canada excluded") == 1


def test_claude_only_match_needs_both_reads(catalog, bank):
    s = split("We are setting up our Nippon operations soon.")
    one = payload(n=1, bank=[("LOC-01", 1, "Nippon operations")])
    none = payload(n=1)
    ra, rb = _reads(one, none, s, bank)
    assert "LOC-01" not in [q["id"] for q in _resolve({"Q01": "New implementation"}, ra, rb, s, catalog, bank).questions]
    ra, rb = _reads(one, one, s, bank)
    q = [q for q in _resolve({"Q01": "New implementation"}, ra, rb, s, catalog, bank).questions if q["id"] == "LOC-01"][0]
    assert q["trigger"] == {"how": "matched", "detail": "Nippon operations", "sentence": 1}


def test_skip_and_gap_overlap(catalog, bank):
    s = split("We collect sales tax with Avalara. POs over $5,000 go to the CFO.")
    a = payload(n=2, approvals=[("PO approval", 1, 2, "POs over $5,000 go to the CFO")])
    ra, rb = _reads(a, a, s, bank)
    sc = _resolve({"Q01": "New implementation", "Q07": "Yes", "Q08": "Yes"}, ra, rb, s, catalog, bank)
    ids = [q["id"] for q in sc.questions]
    assert "LOC-03" in ids and "Q06" not in ids
    assert "P2P-02" not in ids
