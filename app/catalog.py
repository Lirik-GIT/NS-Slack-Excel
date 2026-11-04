import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

STEP1 = "Step 1 WBS & Estimate-NetSuite"
STEP2 = "Step 2 (Effort Summary) - NS"
STEP3 = "Step 3 Timeline Offshore - NS"
INPUTS = "Inputs"
ITEM_SIZES = "Item sizes"
PRICING = "Pricing"
QUESTIONS_TAB = "Questions and assumptions"
APPROVALS_LABEL = "Approval processes (Q17)"
CUSTOM_LABEL = "Custom items (Q52)"
MIGRATION_LABEL = "Other migration objects (Q57)"

HOURS_COL = 9
DAYS_COL = 10
CUSTOM_LINE_NAME = "custom solutions"

PRICING_INPUTS = {
    "onsite_rate": "Onsite rate (USD per hour)",
    "offshore_rate": "Offshore rate (USD per hour)",
    "onsite_share": "Onsite share of hours",
    "rom_low": "ROM low (below price)",
    "rom_high": "ROM high (above price)",
    "usd_to_cad": "USD to CAD",
    "weeks_requirements": "Requirements (weeks)",
    "weeks_uat_single": "UAT, one subsidiary (weeks)",
    "weeks_uat_multi": "UAT, more than one subsidiary (weeks)",
    "weeks_golive": "Go-live (weeks)",
}
PRICING_OUTPUTS = {
    "offshore_share": "Offshore share of hours",
    "blended_rate": "Blended rate (USD per hour)",
    "total_hours": "Total effort (hours)",
    "price": "Price (USD)",
    "rounding": "Rounding unit (USD)",
    "rom_low_usd": "ROM low (USD)",
    "rom_high_usd": "ROM high (USD)",
    "price_cad": "Price (CAD)",
    "weeks_build": "Build (weeks)",
    "weeks_uat": "UAT (weeks)",
    "weeks_total": "Total duration (weeks)",
}
MILESTONE_LABEL = "Milestone"

_SUM_RE = re.compile(r"^=SUM\((.*)\)$", re.IGNORECASE)
_REF_RE = re.compile(r"!J(\d+)$")


class TemplateError(Exception):
    pass


@dataclass(frozen=True)
class Line:
    line_id: str
    row: int
    section: str
    wbs: str
    name: str
    tasks: str
    assumptions: str
    baseline: str


@dataclass(frozen=True)
class Catalog:
    template_sha256: str
    lines: tuple
    custom_line: Line
    dev_days_cell: str
    answer_rows: dict
    approval_rows: tuple
    custom_rows: tuple
    migration_rows: tuple
    item_hours: dict
    item_criteria: dict
    pricing_cells: dict
    milestone_rows: tuple

    def line(self, line_id):
        for ln in self.lines:
            if ln.line_id == line_id:
                return ln
        return None


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, float):
        value = round(value, 2)
    return " ".join(str(value).split())


def _sum_rows(formula):
    m = _SUM_RE.match(str(formula).replace(" ", ""))
    if not m:
        return None
    rows = set()
    for part in m.group(1).split(","):
        nums = [int(n) for n in re.findall(r"\d+", part)]
        if len(nums) == 2:
            rows.update(range(nums[0], nums[1] + 1))
        elif len(nums) == 1:
            rows.add(nums[0])
    return rows


def _priced_rows(s1, s2):
    sections = {}
    for r in range(1, s1.max_row + 1):
        rows = _sum_rows(s1.cell(r, DAYS_COL).value) if s1.cell(r, DAYS_COL).value else None
        if rows is not None:
            sections[r] = rows
    roots = set()
    for r in range(1, s2.max_row + 1):
        m = _REF_RE.search(str(s2.cell(r, 3).value or ""))
        if m:
            roots.add(int(m.group(1)))
    priced = set()
    stack = list(roots)
    while stack:
        r = stack.pop()
        if r in priced:
            continue
        priced.add(r)
        stack.extend(sections.get(r, ()))
    return priced, set(sections)


def _section_name(s1, row, section_rows):
    for r in range(row - 1, 0, -1):
        if r in section_rows:
            name = _clean(s1.cell(r, 3).value) or _clean(s1.cell(r, 1).value)
            if name:
                return name
    return ""


def priced_lines(s1, s2):
    priced, section_rows = _priced_rows(s1, s2)
    out = []
    for r in sorted(priced):
        if r in section_rows or not _clean(s1.cell(r, 3).value):
            continue
        if str(s1.cell(r, DAYS_COL).value or "").replace(" ", "").upper() != f"=I{r}/8":
            continue
        out.append(Line(
            line_id=f"L{r}", row=r, section=_section_name(s1, r, section_rows), wbs=_clean(s1.cell(r, 2).value),
            name=_clean(s1.cell(r, 3).value), tasks=_clean(s1.cell(r, 4).value),
            assumptions=_clean(s1.cell(r, 5).value), baseline=_clean(s1.cell(r, 6).value),
        ))
    return out


def load_catalog(template_path):
    path = Path(template_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    wb = load_workbook(path)
    for name in (INPUTS, ITEM_SIZES, PRICING, STEP1, STEP2, STEP3):
        if name not in wb.sheetnames:
            raise TemplateError(f"Template is missing sheet '{name}'")
    s1, s2 = wb[STEP1], wb[STEP2]

    lines, custom = [], None
    for line in priced_lines(s1, s2):
        if line.name.lower() == CUSTOM_LINE_NAME:
            custom = line
        else:
            lines.append(line)
    if custom is None or not lines:
        raise TemplateError("Template Step 1 has no priced lines or no 'Custom solutions' line")

    dev_days_cell = None
    for r in range(1, s2.max_row + 1):
        if _clean(s2.cell(r, 2).value).lower() == "total" and str(s2.cell(r, 3).value or "").upper().startswith("=SUM(C"):
            dev_days_cell = f"C{r}"
            break
    if dev_days_cell is None:
        raise TemplateError("Template Step 2 has no build effort total")

    inp = wb[INPUTS]
    answer_rows, tables = {}, {}
    for r in range(1, inp.max_row + 1):
        v = _clean(inp.cell(r, 1).value)
        if re.fullmatch(r"Q\d\d", v):
            answer_rows[v] = r
        elif v in (APPROVALS_LABEL, CUSTOM_LABEL, MIGRATION_LABEL):
            tables[v] = r + 2
    for label in (APPROVALS_LABEL, CUSTOM_LABEL, MIGRATION_LABEL):
        if label not in tables:
            raise TemplateError(f"Inputs tab is missing the '{label}' table")

    def table_rows(start):
        rows, r = [], start
        while isinstance(inp.cell(r, 1).value, int):
            rows.append(r)
            r += 1
        return tuple(rows)

    sizes = wb[ITEM_SIZES]
    item_hours, item_criteria = {}, {}
    for r in range(2, sizes.max_row + 1):
        t, z, h = sizes.cell(r, 1).value, sizes.cell(r, 2).value, sizes.cell(r, 4).value
        if t and z and isinstance(h, (int, float)):
            item_hours[(_clean(t), _clean(z))] = float(h)
            item_criteria[(_clean(t), _clean(z))] = _clean(sizes.cell(r, 3).value)

    pr = wb[PRICING]
    labels = {}
    milestones = []
    for r in range(1, pr.max_row + 1):
        label = _clean(pr.cell(r, 1).value)
        if label:
            labels[label] = f"B{r}"
        if label.startswith(MILESTONE_LABEL):
            milestones.append(r)
    pricing_cells = {}
    for key, label in {**PRICING_INPUTS, **PRICING_OUTPUTS}.items():
        if label not in labels:
            raise TemplateError(f"Pricing tab is missing '{label}'")
        pricing_cells[key] = labels[label]
    if not milestones:
        raise TemplateError("Pricing tab has no milestone rows")

    return Catalog(
        template_sha256=digest,
        lines=tuple(lines),
        custom_line=custom,
        dev_days_cell=dev_days_cell,
        answer_rows=answer_rows,
        approval_rows=table_rows(tables[APPROVALS_LABEL]),
        custom_rows=table_rows(tables[CUSTOM_LABEL]),
        migration_rows=table_rows(tables[MIGRATION_LABEL]),
        item_hours=item_hours,
        item_criteria=item_criteria,
        pricing_cells=pricing_cells,
        milestone_rows=tuple(milestones),
    )
