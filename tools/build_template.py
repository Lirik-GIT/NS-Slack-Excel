import argparse
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog import (MILESTONE_LABEL, PRICING, PRICING_INPUTS, PRICING_OUTPUTS, STEP1, STEP2,  # noqa: E402
                         STEP3, priced_lines)
from app.questions import (APPROVAL_ROWS, CUSTOM_ROWS, CUSTOM_TYPES, LIST_APPROVALS,  # noqa: E402
                           LIST_CUSTOM, LIST_MIGRATION, MIGRATION_ROWS, MIGRATION_TYPE, NEW,
                           QUESTIONS, SIZES)

INPUTS = "Inputs"
ITEM_SIZES = "Item sizes"

ITEM_SIZE_ROWS = (
    ("Report / saved search", "Small", "One saved search or standard report tweak, no formulas", 4),
    ("Report / saved search", "Medium", "Joins, summary types, formula columns or a custom filter set", 8),
    ("Report / saved search", "Large", "SuiteAnalytics workbook, multi-source dataset, or scheduled distribution", 16),
    ("Workflow", "Small", "Single record, up to 3 states, no custom actions", 8),
    ("Workflow", "Medium", "Multiple states and conditions, email or field actions", 16),
    ("Workflow", "Large", "Cross-record, custom actions or scripted steps", 32),
    ("Script", "Small", "One user event or client script, one record, simple logic", 8),
    ("Script", "Medium", "Scheduled or map/reduce, or logic across 2-3 record types", 24),
    ("Script", "Large", "Suitelet UI, RESTlet, or complex multi-record processing", 40),
    ("Custom record", "Small", "One custom record, up to 10 fields", 4),
    ("Custom record", "Medium", "Parent-child records, or 10-30 fields with sourcing", 8),
    ("Custom record", "Large", "Record set with forms, roles and scripted behaviour", 16),
    ("Custom integration", "Small", "One-way, one object, file or simple API", 40),
    ("Custom integration", "Medium", "Two-way or 2-4 objects, through middleware", 80),
    ("Custom integration", "Large", "Real-time, many objects, error handling and reconciliation", 160),
    (MIGRATION_TYPE, "Small", "Under 1,000 records, one load", 8),
    (MIGRATION_TYPE, "Medium", "1,000-10,000 records, or with transformation", 16),
    (MIGRATION_TYPE, "Large", "Over 10,000 records, multiple loads or reconciliation", 40),
)


def a(qid):
    return f"ans_{qid}"


def yes(qid):
    return f'{a(qid)}="Yes"'


def base_if(cond):
    return lambda r: f"=IF({cond},F{r},0)"


ITEMS = f"OR({yes('Q07')},{yes('Q08')})"
NEW_IMPL = f'{a("Q01")}="{NEW}"'

RULES = {}
for row in (5, 6, 7, 9, 10, 11, 12, 13):
    RULES[row] = base_if(NEW_IMPL)
RULES[14] = lambda r: f"=IF({NEW_IMPL},MAX(1,ROUNDUP({a('Q03')}/5,0))*F{r},0)"
RULES[18] = base_if(yes("Q04"))
RULES[19] = lambda r: f'=IF({a("Q05")}="P2P and O2C",31,IF(OR({a("Q05")}="P2P",{a("Q05")}="O2C"),16,0))'
RULES[21] = lambda r: f"=IF({a('Q06')}<=0,0,IF({a('Q06')}<=4,10,IF({a('Q06')}<=20,16,IF({a('Q06')}<=40,24,30))))"
RULES[23] = lambda r: f"=IF({ITEMS},F{r}+IF(OR({yes('Q10')},{yes('Q25')}),8,0)+IF({yes('Q11')},8,0),0)"
RULES[24] = base_if(yes("Q13"))
RULES[25] = base_if(yes("Q14"))
RULES[26] = lambda r: f'=IF({ITEMS},F{r}+IF({a("Q15")}="Standard",40,0),0)'
RULES[27] = base_if(ITEMS)
RULES[29] = base_if(ITEMS)
RULES[30] = base_if(yes("Q09"))
RULES[31] = base_if(yes("Q09"))
for row in (33, 34, 35, 36, 37, 38):
    RULES[row] = base_if(yes("Q07"))
RULES[39] = lambda r: f"=IF({a('Q16')}<=0,0,8+6*({a('Q16')}-1))"
RULES[40] = lambda r: "=SUMPRODUCT((appr_levels=1)*24+(appr_levels>=2)*40)"
RULES[41] = lambda r: (f'=IF({a("Q18")}="2-way",24,IF({a("Q18")}="3-way",40,'
                       f'IF({a("Q18")}="3-way with tolerances or multiple receipts",64,0)))')
for row in (44, 45, 46, 47, 48):
    RULES[row] = base_if(yes("Q08"))
RULES[49] = lambda r: f"=IF({a('Q20')}<=0,0,IF({a('Q20')}<=2,16,IF({a('Q20')}<=4,24,24+8*({a('Q20')}-4))))"
for row in (52, 53, 54, 55, 56, 58, 59):
    RULES[row] = base_if(yes("Q21"))
RULES[57] = lambda r: f"=IF({yes('Q21')},8*{a('Q22')},0)"
for row in range(62, 71):
    RULES[row] = base_if(yes("Q23"))
for row in range(72, 78):
    RULES[row] = base_if(yes("Q24"))
for row in (80, 82, 83, 84):
    RULES[row] = base_if(yes("Q25"))
for row in range(86, 91):
    RULES[row] = base_if(yes("Q26"))
for row in (92, 93, 94):
    RULES[row] = base_if(yes("Q27"))
for row in (101, 103, 104):
    RULES[row] = base_if(yes("Q28"))
RULES[102] = lambda r: f'=IF({yes("Q28")},IF({a("Q29")}="Billing and revenue",16,8),0)'
for row in (105, 106, 163, 164, 165):
    RULES[row] = lambda r: "=0"
for row in range(108, 113):
    RULES[row] = base_if(yes("Q30"))
for row in (114, 115, 117, 118):
    RULES[row] = base_if(yes("Q31"))
for row in list(range(122, 128)) + list(range(129, 133)) + list(range(134, 139)):
    RULES[row] = base_if(yes("Q32"))
for row, qid in ((147, "Q33"), (148, "Q34"), (152, "Q38"), (153, "Q39"), (154, "Q40"),
                 (155, "Q41"), (156, "Q42"), (158, "Q43"), (150, "Q36")):
    RULES[row] = base_if(yes(qid))
RULES[149] = base_if(f'AND({yes("Q35")},{a("Q36")}<>"Yes")')
RULES[151] = lambda r: (f'=IF({a("Q37")}="In-house app",80,IF({a("Q37")}="Standard dunning setup",40,0))')
RULES[157] = lambda r: f'=IF({a("Q19")}="Full setup",80,IF({a("Q19")}="Enablement and demos",16,0))'
RULES[168] = lambda r: "=SUM(ci_hours)"
RULES[169] = lambda r: f"=F{r}*{a('Q46')}"
RULES[170] = lambda r: f"=F{r}*{a('Q47')}"
for row, qid in ((171, "Q48"), (172, "Q49"), (173, "Q50"), (174, "Q51")):
    RULES[row] = base_if(yes(qid))
RULES[177] = lambda r: f"=F{r}*ROUNDUP(MAX({a('Q02')},1)/3,0)*{a('Q53')}"
RULES[178] = lambda r: f"=IF({a('Q54')}<=0,0,F{r}*ROUNDUP({a('Q54')}/1000,0))"
RULES[179] = lambda r: f"=IF({a('Q55')}<=0,0,F{r}*ROUNDUP({a('Q55')}/10000,0))"
RULES[180] = lambda r: f"=F{r}*{a('Q56')}"
RULES[181] = lambda r: "=SUM(mo_hours)"

HEAD = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
INPUT_FILL = PatternFill("solid", fgColor="FFF8DC")
THIN = Side(style="thin", color="C9D1D9")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")


def _header(ws, row, labels):
    for col, label in enumerate(labels, start=1):
        c = ws.cell(row, col, label)
        c.font, c.fill, c.border = HEAD, HEAD_FILL, BOX
        c.alignment = Alignment(wrap_text=True, vertical="center")


def _name(wb, name, ref):
    wb.defined_names[name] = DefinedName(name, attr_text=ref)


PRICING_DEFAULTS = {
    "onsite_rate": 120, "offshore_rate": 50, "onsite_share": 0.2, "rom_low": 0.10, "rom_high": 0.25,
    "usd_to_cad": 1.4, "weeks_requirements": 2, "weeks_uat_single": 1, "weeks_uat_multi": 2, "weeks_golive": 2,
}
PERCENT_KEYS = {"onsite_share", "rom_low", "rom_high", "offshore_share"}


def _pricing_tab(wb):
    s2 = f"'{STEP2}'"
    s3 = f"'{STEP3}'"
    ws = wb.create_sheet(PRICING, 1)
    ws["A1"] = "Pricing"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Yellow cells are the pricing inputs. Everything else is calculated."
    _header(ws, 3, ("Input", "Value"))
    row = 4
    cells = {}
    for key, label in PRICING_INPUTS.items():
        ws.cell(row, 1, label)
        c = ws.cell(row, 2, PRICING_DEFAULTS[key])
        c.fill = INPUT_FILL
        if key in PERCENT_KEYS:
            c.number_format = "0%"
        for col in (1, 2):
            ws.cell(row, col).border = BOX
        cells[key] = f"B{row}"
        row += 1
    row += 1
    _header(ws, row, ("Result", "Value"))
    row += 1
    i = cells
    formulas = {
        "offshore_share": f"=1-{i['onsite_share']}",
        "blended_rate": f"={i['onsite_rate']}*{i['onsite_share']}+{i['offshore_rate']}*(1-{i['onsite_share']})",
        "total_hours": f"=({s2}!D37+{s2}!D44)*8",
        "price": None,
        "rounding": None,
        "rom_low_usd": None,
        "rom_high_usd": None,
        "price_cad": None,
        "weeks_build": f"=ROUNDUP({s2}!D23/5,0)",
        "weeks_uat": f"=IF(ans_Q02>1,{i['weeks_uat_multi']},{i['weeks_uat_single']})",
        "weeks_total": None,
    }
    out = {}
    for key, label in PRICING_OUTPUTS.items():
        out[key] = f"B{row}"
        ws.cell(row, 1, label)
        row += 1
    formulas["price"] = f"={out['total_hours']}*{out['blended_rate']}"
    formulas["rounding"] = f"=IF({out['price']}>100000,5000,1000)"
    formulas["rom_low_usd"] = f"=ROUNDDOWN({out['price']}*(1-{i['rom_low']})/{out['rounding']},0)*{out['rounding']}"
    formulas["rom_high_usd"] = f"=ROUNDUP({out['price']}*(1+{i['rom_high']})/{out['rounding']},0)*{out['rounding']}"
    formulas["price_cad"] = f"={out['price']}*{i['usd_to_cad']}"
    formulas["weeks_total"] = f"={i['weeks_requirements']}+{out['weeks_build']}+{out['weeks_uat']}+{i['weeks_golive']}"
    for key, ref in out.items():
        c = ws[ref]
        c.value = formulas[key]
        c.number_format = "0%" if key in PERCENT_KEYS else ("#,##0" if "weeks" not in key else "0")
        for col in ("A", "B"):
            ws[f"{col}{ref[1:]}"].border = BOX
    row += 1
    _header(ws, row, ("Payment milestone", "Share", "Amount (USD)"))
    row += 1
    for n, src in enumerate((33, 34, 35, 36), start=1):
        ws.cell(row, 1, f"{MILESTONE_LABEL} {n}")
        ws.cell(row, 2, f"={s3}!C{src}").number_format = "0%"
        ws.cell(row, 3, f"={out['price']}*B{row}").number_format = "#,##0"
        for col in (1, 2, 3):
            ws.cell(row, col).border = BOX
        row += 1
    for col, width in zip("ABC", (42, 16, 16)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"


def build(source, dest):
    wb = load_workbook(source)
    for name in list(wb.defined_names):
        value = str(wb.defined_names[name].value or "")
        if "#REF!" in value or "[" in value:
            del wb.defined_names[name]
    for title in (INPUTS, ITEM_SIZES, PRICING):
        if title in wb.sheetnames:
            raise SystemExit(f"{source} already has a '{title}' sheet")

    s1, s2 = wb[STEP1], wb[STEP2]
    leaf = {line.row for line in priced_lines(s1, s2)}
    missing = sorted(leaf - set(RULES))
    extra = sorted(set(RULES) - leaf)
    if missing or extra:
        raise SystemExit(f"Template lines and rules differ. Lines without a rule: {missing}. Rules without a line: {extra}")

    sz = wb.create_sheet(ITEM_SIZES)
    _header(sz, 1, ("Item type", "Size", "Criteria (pick the first that fits)", "Hours"))
    for i, row in enumerate(ITEM_SIZE_ROWS, start=2):
        for col, v in enumerate(row, start=1):
            c = sz.cell(i, col, v)
            c.border, c.alignment = BOX, WRAP
        sz.cell(i, 4).fill = INPUT_FILL
    last_size = len(ITEM_SIZE_ROWS) + 1
    for col, width in zip("ABCD", (24, 10, 70, 8)):
        sz.column_dimensions[col].width = width
    sz.freeze_panes = "A2"
    _name(wb, "size_type", f"'{ITEM_SIZES}'!$A$2:$A${last_size}")
    _name(wb, "size_size", f"'{ITEM_SIZES}'!$B$2:$B${last_size}")
    _name(wb, "size_hours", f"'{ITEM_SIZES}'!$D$2:$D${last_size}")

    ws = wb.create_sheet(INPUTS, 0)
    ws["A1"] = "Estimate inputs"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "Yellow cells are the answers. Step 1 hours are calculated from them."
    _header(ws, 3, ("ID", "Question", "Answer", "Source", "Evidence", "Allowed values"))
    dv_cache = {}
    row = 4
    for q in QUESTIONS:
        ws.cell(row, 1, q.qid)
        ws.cell(row, 2, q.text)
        ws.cell(row, 6, q.allowed_text)
        ans = ws.cell(row, 3)
        ans.fill = INPUT_FILL
        if q.kind == "number":
            ans.value = q.minimum
            key = ("num", q.minimum)
            if key not in dv_cache:
                dv = DataValidation(type="whole", operator="greaterThanOrEqual", formula1=str(q.minimum), allow_blank=False)
                ws.add_data_validation(dv)
                dv_cache[key] = dv
        else:
            ans.value = q.allowed[0]
            key = ("list",) + q.allowed
            if key not in dv_cache:
                dv = DataValidation(type="list", formula1='"' + ",".join(q.allowed) + '"', allow_blank=False)
                ws.add_data_validation(dv)
                dv_cache[key] = dv
        dv_cache[key].add(ans.coordinate)
        for col in range(1, 7):
            ws.cell(row, col).border = BOX
            ws.cell(row, col).alignment = WRAP
        _name(wb, f"ans_{q.qid}", f"{INPUTS}!$C${row}")
        row += 1

    row += 1
    ws.cell(row, 1, f"Approval processes ({LIST_APPROVALS})").font = Font(bold=True)
    row += 1
    _header(ws, row, ("#", "Process", "Levels", "Source", "Evidence"))
    first = row + 1
    for i in range(APPROVAL_ROWS):
        r = first + i
        ws.cell(r, 1, i + 1)
        for col in (2, 3):
            ws.cell(r, col).fill = INPUT_FILL
        for col in range(1, 6):
            ws.cell(r, col).border = BOX
    dv = DataValidation(type="whole", operator="greaterThanOrEqual", formula1="1", allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"C{first}:C{first + APPROVAL_ROWS - 1}")
    _name(wb, "appr_levels", f"{INPUTS}!$C${first}:$C${first + APPROVAL_ROWS - 1}")
    row = first + APPROVAL_ROWS + 1

    ws.cell(row, 1, f"Custom items ({LIST_CUSTOM})").font = Font(bold=True)
    row += 1
    _header(ws, row, ("#", "Item", "Type", "Size", "Hours", "Source", "Evidence"))
    first = row + 1
    type_dv = DataValidation(type="list", formula1='"' + ",".join(CUSTOM_TYPES) + '"', allow_blank=True)
    size_dv = DataValidation(type="list", formula1='"' + ",".join(SIZES) + '"', allow_blank=True)
    ws.add_data_validation(type_dv)
    ws.add_data_validation(size_dv)
    for i in range(CUSTOM_ROWS):
        r = first + i
        ws.cell(r, 1, i + 1)
        ws.cell(r, 5, f'=IF(OR(C{r}="",D{r}=""),0,SUMIFS(size_hours,size_type,C{r},size_size,D{r}))')
        for col in (2, 3, 4):
            ws.cell(r, col).fill = INPUT_FILL
        for col in range(1, 8):
            ws.cell(r, col).border = BOX
    type_dv.add(f"C{first}:C{first + CUSTOM_ROWS - 1}")
    size_dv.add(f"D{first}:D{first + CUSTOM_ROWS - 1}")
    _name(wb, "ci_hours", f"{INPUTS}!$E${first}:$E${first + CUSTOM_ROWS - 1}")
    row = first + CUSTOM_ROWS + 1

    ws.cell(row, 1, f"Other migration objects ({LIST_MIGRATION})").font = Font(bold=True)
    row += 1
    _header(ws, row, ("#", "Object", "Size", "Hours", "Source", "Evidence"))
    first = row + 1
    for i in range(MIGRATION_ROWS):
        r = first + i
        ws.cell(r, 1, i + 1)
        ws.cell(r, 4, f'=IF(C{r}="",0,SUMIFS(size_hours,size_type,"{MIGRATION_TYPE}",size_size,C{r}))')
        for col in (2, 3):
            ws.cell(r, col).fill = INPUT_FILL
        for col in range(1, 7):
            ws.cell(r, col).border = BOX
    size_dv.add(f"C{first}:C{first + MIGRATION_ROWS - 1}")
    _name(wb, "mo_hours", f"{INPUTS}!$D${first}:$D${first + MIGRATION_ROWS - 1}")

    for col, width in zip("ABCDEFG", (7, 58, 30, 14, 50, 40, 40)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"

    for r, rule in RULES.items():
        s1.cell(r, 9).value = rule(r)

    _pricing_tab(wb)

    wb.active = 0
    wb.save(dest)
    return sorted(RULES)


def main():
    parser = argparse.ArgumentParser(description="Build the bot-ready estimator master from the blank master sheet")
    parser.add_argument("source")
    parser.add_argument("dest")
    args = parser.parse_args()
    rows = build(args.source, args.dest)
    print(f"Wrote {args.dest} with rules on {len(rows)} Step 1 lines")


if __name__ == "__main__":
    main()
