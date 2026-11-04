from openpyxl import load_workbook

from app.catalog import INPUTS, PRICING, QUESTIONS_TAB, load_catalog
from app.estimator import Estimator
from tests.conftest import TEMPLATE, payload

REQ = ("RiverBed already runs NetSuite. They need Advanced Revenue Management set up. "
       "About 2,500 open contracts must be migrated. A scheduled script should recalculate revenue plans across two record types. "
       "Go-live is planned for March.")

READ = payload(
    answers=[("Q39", "Yes", 2, "Advanced Revenue Management"), ("Q54", 2500, 3, "2,500 open contracts")],
    custom=[("Revenue plan recalculation", "Script", "Medium", 4, "scheduled script")],
    sentence_map=[{"sentence": 1, "covered_by": ["Q01"]}, {"sentence": 2, "covered_by": ["Q39"]},
                  {"sentence": 3, "covered_by": ["Q54"]}, {"sentence": 4, "covered_by": ["custom"]},
                  {"sentence": 5, "covered_by": []}],
    summary="ARM on an existing NetSuite account with contract migration and one script.",
)
FORM = {"Q01": "Add-on to existing account", "Q39": "Yes"}


def build(tmp_path, catalog, policy, recalculator, make_interpreter, *reads):
    return Estimator(catalog, policy, make_interpreter(*reads), recalculator, TEMPLATE, tmp_path)


def test_requirement_to_rom(tmp_path, catalog, policy, recalculator, make_interpreter):
    est = build(tmp_path, catalog, policy, recalculator, make_interpreter, READ).estimate("REQ1", "RiverBed", REQ, FORM, False)
    hours = {k: v["hours"] for k, v in est.line_hours.items()}
    assert hours == {"L153": 24, "L178": 48, "L168": 24}
    r = est.results
    assert r.dev_days == 12 and r.total_hours == 264 and r.blended_rate == 64
    assert r.price == 16896 and (r.rom_low, r.rom_high) == (15000, 22000)
    assert (r.weeks_requirements, r.weeks_build, r.weeks_uat, r.weeks_golive, r.weeks_total) == (2, 2, 1, 2, 7)
    assert [p for p, _ in r.milestones] == [0.5, 0.2, 0.2, 0.1]
    assert est.scope.not_covered == [{"n": 5, "text": "Go-live is planned for March."}]
    assert est.scope.conflicts == []
    ids = [q["id"] for q in est.scope.questions]
    assert ids == ["Q02", "GOL-01"]
    assert est.scope.assumptions == ["Priced for one subsidiary.", "Go-live date set after discovery",
                                     "Country localization outside US and Canada excluded"]
    assert est.reasons == ["Every estimate is reviewed before it goes to the AE",
                           "1 sentence(s) of the requirement are not priced",
                           "Includes custom items sized from the Item sizes table"]
    wb = load_workbook(est.workbook_path)
    inp = wb[INPUTS]
    assert inp.cell(catalog.answer_rows["Q39"], 4).value == "Form"
    assert inp.cell(catalog.answer_rows["Q54"], 5).value == "2,500 open contracts"
    assert inp.cell(catalog.custom_rows[0], 3).value == "Script"
    qt = wb[QUESTIONS_TAB]
    assert qt["A2"].value.startswith("Question bank version")
    assert [qt.cell(r, 2).value for r in (5, 6)] == ["Q02", "GOL-01"]
    assert wb[PRICING]["B4"].value == 120


def test_same_inputs_same_estimate(tmp_path, catalog, policy, recalculator, make_interpreter):
    one = build(tmp_path / "a", catalog, policy, recalculator, make_interpreter, READ).estimate("A", "RiverBed", REQ, FORM, False)
    two = build(tmp_path / "b", catalog, policy, recalculator, make_interpreter, READ).estimate("B", "RiverBed", REQ, FORM, False)
    assert one.results == two.results and one.line_hours == two.line_hours
    assert one.scope.questions == two.scope.questions and one.scope.assumptions == two.scope.assumptions


def test_reviewer_changes_an_answer(tmp_path, catalog, policy, recalculator, make_interpreter):
    estimator = build(tmp_path, catalog, policy, recalculator, make_interpreter, READ)
    est = estimator.estimate("REQ4", "RiverBed", REQ, FORM, False)
    wb = load_workbook(est.workbook_path)
    wb[INPUTS].cell(catalog.answer_rows["Q54"], 3).value = 900
    edited = tmp_path / "edited.xlsx"
    wb.save(edited)
    results, lines, answers, approvals, custom, migration, path = estimator.recalculate_upload("REQ4", edited, 2)
    assert lines["L178"]["hours"] == 16
    assert results.dev_days == 8 and results.price == 8 * 2.75 * 8 * 64
    assert answers["Q54"]["value"] == 900 and custom[0]["type"] == "Script"
    assert load_catalog(path).template_sha256
    assert path.name == "REQ4_v2.xlsx"


def test_repricing(tmp_path, catalog, policy, recalculator, make_interpreter):
    estimator = build(tmp_path, catalog, policy, recalculator, make_interpreter, READ)
    est = estimator.estimate("REQ5", "RiverBed", REQ, FORM, False)
    results, *_, path = estimator.reprice("REQ5", est.workbook_path, 2, 150, 45, 0.3)
    assert results.blended_rate == 76.5 and results.price == 264 * 76.5
    assert path.name == "REQ5_v2.xlsx" and est.workbook_path.exists()


def test_empty_scope_is_refused(tmp_path, catalog, policy, recalculator, make_interpreter):
    import pytest
    blank = payload(n=5)
    with pytest.raises(ValueError):
        build(tmp_path, catalog, policy, recalculator, make_interpreter, blank).estimate(
            "E", "X", REQ, {"Q01": "Add-on to existing account"}, False)
