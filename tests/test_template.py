import hashlib

from openpyxl import load_workbook

from app.catalog import STEP1, load_catalog
from app.questions import QUESTIONS
from app.resolve import Scope
from app.workbook import read_line_hours, read_results, write_inputs, write_pricing
from tests.conftest import BLANK, TEMPLATE
from tools.build_template import RULES, build


def scope(values, approvals=(), custom=(), migration=()):
    base = {}
    for q in QUESTIONS:
        base[q.qid] = {"value": q.minimum if q.kind == "number" else q.allowed[0], "source": "Default", "evidence": ""}
    for k, v in values.items():
        base[k] = {"value": v, "source": "Requirement", "evidence": "x"}
    return Scope(answers=base, approvals=list(approvals), custom_items=list(custom), migration_objects=list(migration),
                 sentences=[], not_covered=[], conflicts=[], questions=[], assumptions=[], form_flags=[], flags=[],
                 issues=[], scope_summary="")


def test_shipped_template_is_built_from_blank(tmp_path):
    out = tmp_path / "rebuilt.xlsx"
    build(BLANK, out)
    a, b = load_workbook(out), load_workbook(TEMPLATE)
    for sheet in a.sheetnames:
        sa, sb = a[sheet], b[sheet]
        for row in sa.iter_rows():
            for c in row:
                assert c.value == sb[c.coordinate].value, f"{sheet}!{c.coordinate}"


def test_every_priced_line_has_a_rule(catalog):
    rows = {ln.row for ln in catalog.lines} | {catalog.custom_line.row}
    assert rows == set(RULES)
    s1 = load_workbook(TEMPLATE)[STEP1]
    for r in rows:
        assert str(s1.cell(r, 9).value).startswith("=")


def test_blank_template_is_unchanged_outside_column_i():
    blank, built = load_workbook(BLANK), load_workbook(TEMPLATE)
    for sheet in blank.sheetnames:
        for row in blank[sheet].iter_rows():
            for c in row:
                if sheet == STEP1 and c.column == 9 and c.row in RULES:
                    continue
                assert c.value == built[sheet][c.coordinate].value, f"{sheet}!{c.coordinate}"


def test_layout(catalog):
    assert len(catalog.answer_rows) == len(QUESTIONS) == 54
    assert (len(catalog.approval_rows), len(catalog.custom_rows), len(catalog.migration_rows)) == (20, 60, 20)
    assert catalog.item_hours[("Script", "Medium")] == 24
    assert catalog.item_hours[("Other migration object", "Large")] == 40
    assert len(catalog.lines) == 126 and catalog.custom_line.row == 168


def test_rules_calculate(tmp_path, catalog, recalculator):
    sc = scope(
        {"Q01": "New implementation", "Q02": 4, "Q03": 7, "Q04": "Yes", "Q05": "P2P and O2C", "Q06": 12,
         "Q07": "Yes", "Q08": "Yes", "Q09": "Yes", "Q10": "Yes", "Q15": "Standard", "Q16": 3, "Q18": "3-way",
         "Q20": 5, "Q35": "Yes", "Q36": "Yes", "Q37": "In-house app", "Q39": "Yes", "Q46": 2, "Q53": 2, "Q54": 2500},
        approvals=[{"process": "PO", "levels": 3}, {"process": "Bill", "levels": 1}],
        custom=[{"name": "AR aging", "type": "Report / saved search", "size": "Medium"},
                {"name": "Shopify", "type": "Custom integration", "size": "Large"}],
        migration=[{"name": "Contracts", "size": "Medium"}],
    )
    book = tmp_path / "b.xlsx"
    write_inputs(TEMPLATE, book, catalog, sc, "v")
    recalculator.recalc(book)
    hours = {k: v["hours"] for k, v in read_line_hours(book, catalog).items()}
    expected = {"L5": 0.5, "L14": 4, "L18": 1, "L19": 31, "L21": 16, "L23": 26, "L26": 44, "L30": 4, "L33": 4,
                "L39": 20, "L40": 64, "L41": 40, "L49": 32, "L150": 60, "L151": 80, "L153": 24, "L168": 168,
                "L169": 16, "L177": 32, "L178": 48, "L181": 16}
    for k, v in expected.items():
        assert hours.get(k) == v, k
    assert "L149" not in hours


def test_tiers(tmp_path, catalog, recalculator):
    cases = [({"Q06": 4}, "L21", 10), ({"Q06": 20}, "L21", 16), ({"Q06": 41}, "L21", 30),
             ({"Q16": 1}, "L39", 8), ({"Q20": 2}, "L49", 16), ({"Q20": 4}, "L49", 24),
             ({"Q28": "Yes", "Q29": "Billing and revenue"}, "L102", 16), ({"Q19": "Enablement and demos"}, "L157", 16),
             ({"Q35": "Yes"}, "L149", 24), ({"Q01": "New implementation", "Q03": 0}, "L14", 2),
             ({"Q02": 3, "Q53": 1}, "L177", 8), ({"Q54": 1000}, "L178", 16), ({"Q55": 10001}, "L179", 32),
             ({"Q07": "Yes", "Q25": "Yes"}, "L23", 26), ({"Q08": "Yes", "Q25": "Yes", "Q10": "Yes", "Q11": "Yes"}, "L23", 34)]
    for values, line, want in cases:
        book = tmp_path / f"{line}_{want}.xlsx"
        write_inputs(TEMPLATE, book, catalog, scope(values), "v")
        recalculator.recalc(book)
        got = read_line_hours(book, catalog).get(line, {}).get("hours", 0)
        assert got == want, (values, line, got)


def test_pricing_tab(tmp_path, catalog, recalculator):
    book = tmp_path / "p.xlsx"
    write_inputs(TEMPLATE, book, catalog, scope({"Q39": "Yes", "Q54": 2500, "Q02": 1}), "v")
    recalculator.recalc(book)
    r = read_results(book, catalog)
    assert r.dev_days == 9 and r.total_hours == 9 * 2.75 * 8 == 198
    assert (r.onsite_rate, r.offshore_rate, r.onsite_share, r.blended_rate) == (120, 50, 0.2, 64)
    assert r.price == 12672 and r.rom_low == 11000 and r.rom_high == 16000
    assert round(r.price_cad, 2) == 17740.8
    assert (r.weeks_requirements, r.weeks_build, r.weeks_uat, r.weeks_golive, r.weeks_total) == (2, 1, 1, 2, 6)
    assert [p for p, _ in r.milestones] == [0.5, 0.2, 0.2, 0.1] and round(sum(v for _, v in r.milestones), 2) == 12672

    write_pricing(book, catalog, {"onsite_rate": 150, "offshore_rate": 40, "onsite_share": 0.5})
    recalculator.recalc(book)
    r = read_results(book, catalog)
    assert r.blended_rate == 95 and r.price == 198 * 95


def test_big_deal_rounds_to_5k_and_multi_sub_uat(tmp_path, catalog, recalculator):
    book = tmp_path / "big.xlsx"
    custom = [{"name": f"i{i}", "type": "Custom integration", "size": "Large"} for i in range(6)]
    write_inputs(TEMPLATE, book, catalog, scope({"Q02": 3}, custom=custom), "v")
    recalculator.recalc(book)
    r = read_results(book, catalog)
    assert r.price > 100000
    assert r.rom_low % 5000 == 0 and r.rom_high % 5000 == 0
    assert r.rom_low <= r.price * 0.9 < r.rom_low + 5000 and r.rom_high - 5000 < r.price * 1.25 <= r.rom_high
    assert r.weeks_uat == 2


def test_template_hash_stable(catalog):
    assert load_catalog(TEMPLATE).template_sha256 == catalog.template_sha256 == hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
