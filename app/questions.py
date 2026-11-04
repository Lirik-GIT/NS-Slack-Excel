from dataclasses import dataclass

YES_NO = ("No", "Yes")
NEW = "New implementation"
ADDON = "Add-on to existing account"


@dataclass(frozen=True)
class Question:
    qid: str
    group: str
    text: str
    kind: str
    allowed: tuple = ()
    minimum: int = 0

    @property
    def allowed_text(self):
        if self.kind == "number":
            return f"Whole number, {self.minimum} or more"
        return " / ".join(self.allowed)


def _q(qid, group, text, kind, allowed=(), minimum=0):
    if kind == "yesno":
        allowed = YES_NO
    return Question(qid, group, text, kind, tuple(allowed), minimum)


QUESTIONS = (
    _q("Q01", "Deal", "Is this a new NetSuite implementation or an add-on to an existing NetSuite account?", "choice", (ADDON, NEW)),
    _q("Q02", "Deal", "How many subsidiaries?", "number", minimum=1),
    _q("Q03", "Record to report", "How many custom roles beyond standard SuiteSuccess roles?", "number"),
    _q("Q04", "Record to report", "Are bank feeds needed?", "yesno"),
    _q("Q05", "Record to report", "Is ACH needed, and for which side?", "choice", ("None", "P2P", "O2C", "P2P and O2C")),
    _q("Q06", "Record to report", "In how many US/Canada states or provinces is sales tax collected?", "number"),
    _q("Q07", "Areas", "Procure to Pay in scope?", "yesno"),
    _q("Q08", "Areas", "Order to Cash in scope?", "yesno"),
    _q("Q09", "Items", "Is inventory tracked?", "yesno"),
    _q("Q10", "Items", "Assembly items used?", "yesno"),
    _q("Q11", "Items", "Matrix items used?", "yesno"),
    _q("Q12", "Items", "Number of SKUs", "number"),
    _q("Q13", "Items", "Multiple units of measure?", "yesno"),
    _q("Q14", "Items", "Bin management?", "yesno"),
    _q("Q15", "Items", "Costing method", "choice", ("Average", "FIFO", "LIFO", "Standard")),
    _q("Q16", "Procure to pay", "Number of P2P PDF templates (PO, etc.)", "number"),
    _q("Q18", "Procure to pay", "Vendor bill matching", "choice", ("None", "2-way", "3-way", "3-way with tolerances or multiple receipts")),
    _q("Q19", "Procure to pay", "Advanced Procurement module", "choice", ("None", "Enablement and demos", "Full setup")),
    _q("Q20", "Order to cash", "Number of O2C PDF templates (SO, invoice, pick/pack, etc.)", "number"),
    _q("Q21", "Areas", "Lead to Quote (CRM leads, quotes, forecasts) in scope?", "yesno"),
    _q("Q22", "Lead to quote", "Number of quote PDF templates", "number"),
    _q("Q23", "Areas", "Marketing (campaigns, email templates, promotions) in scope?", "yesno"),
    _q("Q24", "Areas", "Case management in scope?", "yesno"),
    _q("Q25", "Areas", "Work orders and assemblies in scope?", "yesno"),
    _q("Q26", "Areas", "WIP and routing in scope?", "yesno"),
    _q("Q27", "Areas", "Advanced manufacturing (shop floor, batch travelers) in scope?", "yesno"),
    _q("Q28", "Areas", "Project to Cash in scope?", "yesno"),
    _q("Q29", "Project to cash", "Project billing type", "choice", ("Billing only", "Billing and revenue")),
    _q("Q30", "Areas", "Resource to Project (time, expenses, resourcing) in scope?", "yesno"),
    _q("Q31", "Areas", "Advanced projects (WBS, GAAP P&L, allocation chart) in scope?", "yesno"),
    _q("Q32", "Areas", "SuiteCommerce website in scope?", "yesno"),
    _q("Q33", "Add-ons", "Amortization", "yesno"),
    _q("Q34", "Add-ons", "Expense allocation", "yesno"),
    _q("Q35", "Add-ons", "Demand planning", "yesno"),
    _q("Q36", "Add-ons", "MRP / MPS", "yesno"),
    _q("Q37", "Add-ons", "Dunning approach", "choice", ("None", "NetSuite Dunning app", "Standard dunning setup", "In-house app")),
    _q("Q38", "Add-ons", "Fixed Assets", "yesno"),
    _q("Q39", "Add-ons", "Advanced Revenue Management (ARM)", "yesno"),
    _q("Q40", "Add-ons", "SuiteApprovals", "yesno"),
    _q("Q41", "Add-ons", "SuiteTax", "yesno"),
    _q("Q42", "Add-ons", "Commissions / incentive compensation", "yesno"),
    _q("Q43", "Add-ons", "SuiteBilling", "yesno"),
    _q("Q44", "Add-ons", "Contract renewals", "yesno"),
    _q("Q45", "Add-ons", "Warranty and support", "yesno"),
    _q("Q46", "Integrations", "Number of third-party NetSuite apps (AvaTax, TaxJar, CyberSource, Expensify, etc.)", "number"),
    _q("Q47", "Integrations", "Number of EDI / e-commerce connectors (Shopify, SPS, CommerceHub, FarApp, etc.)", "number"),
    _q("Q48", "Customizations", "Serial number upload solution?", "yesno"),
    _q("Q49", "Customizations", "Commercial invoice PDF?", "yesno"),
    _q("Q50", "Customizations", "Custom transaction body / line fields?", "yesno"),
    _q("Q51", "Customizations", "Script to allocate expenses across subsidiaries?", "yesno"),
    _q("Q53", "Data migration", "Years of trial balance to migrate", "number"),
    _q("Q54", "Data migration", "Number of open transactions (open AR, AP, POs, SOs)", "number"),
    _q("Q55", "Data migration", "Number of historical closed SO/PO records", "number"),
    _q("Q56", "Data migration", "Years of historical JE-only financial data", "number"),
)

BY_ID = {q.qid: q for q in QUESTIONS}

LIST_APPROVALS = "Q17"
LIST_CUSTOM = "Q52"
LIST_MIGRATION = "Q57"

CUSTOM_TYPES = ("Report / saved search", "Workflow", "Script", "Custom record", "Custom integration")
MIGRATION_TYPE = "Other migration object"
SIZES = ("Small", "Medium", "Large")

APPROVAL_ROWS = 20
CUSTOM_ROWS = 60
MIGRATION_ROWS = 20

AREA_OPTIONS = (
    ("Q07", "Procure to Pay"),
    ("Q08", "Order to Cash"),
    ("Q09", "Inventory"),
    ("Q21", "Lead to Quote (CRM)"),
    ("Q23", "Marketing"),
    ("Q24", "Case management"),
    ("Q25", "Work orders and assemblies"),
    ("Q26", "WIP and routing"),
    ("Q27", "Advanced manufacturing"),
    ("Q28", "Project to Cash"),
    ("Q30", "Resource to Project"),
    ("Q31", "Advanced projects"),
    ("Q32", "SuiteCommerce website"),
)

ADDON_OPTIONS = (
    ("Q33", "Amortization"),
    ("Q34", "Expense allocation"),
    ("Q35", "Demand planning"),
    ("Q36", "MRP / MPS"),
    ("Q38", "Fixed Assets"),
    ("Q39", "Advanced Revenue Management (ARM)"),
    ("Q40", "SuiteApprovals"),
    ("Q41", "SuiteTax"),
    ("Q42", "Commissions"),
    ("Q43", "SuiteBilling"),
    ("Q44", "Contract renewals"),
    ("Q45", "Warranty and support"),
)


def default_for(qid, answers):
    """Value, whether to ask, and the assumption shown on the ROM when nobody stated this fact."""
    new = answers.get("Q01") == NEW
    p2p = answers.get("Q07") == "Yes"
    o2c = answers.get("Q08") == "Yes"
    inventory = answers.get("Q09") == "Yes"
    table = {
        "Q01": (NEW, False, None),
        "Q02": (1, True, "Priced for one subsidiary."),
        "Q03": (5, False, "Includes up to 5 custom roles." if new else None),
        "Q04": ("Yes" if new else "No", False, "Includes bank feed setup." if new else None),
        "Q05": ("P2P" if new and p2p else "None", False, "Includes ACH for vendor payments." if new and p2p else None),
        "Q06": (4 if o2c else 0, o2c, "Includes standard sales tax setup for up to 4 states." if o2c else None),
        "Q07": ("Yes" if new else "No", False, None),
        "Q08": ("Yes" if new else "No", False, None),
        "Q09": ("No", p2p or o2c, "Inventory tracking is not included." if p2p or o2c else None),
        "Q10": ("Yes" if answers.get("Q25") == "Yes" else "No", False,
                "Includes assembly item setup." if answers.get("Q25") == "Yes" else None),
        "Q12": (0, False, None),
        "Q15": ("Average", False, "Priced for average costing." if inventory else None),
        "Q16": (1 if p2p else 0, False, "Includes 1 purchase order PDF template." if p2p else None),
        "Q18": ("None", False, None),
        "Q19": ("None", False, None),
        "Q20": (2 if o2c else 0, False, "Includes sales order and invoice PDF templates." if o2c else None),
        "Q22": (1 if answers.get("Q21") == "Yes" else 0, False,
                "Includes 1 quote PDF template." if answers.get("Q21") == "Yes" else None),
        "Q29": ("Billing only", False, "Priced for project billing only." if answers.get("Q28") == "Yes" else None),
        "Q37": ("None", False, None),
        "Q46": (0, False, None),
        "Q47": (0, False, None),
        "Q50": ("Yes" if new else "No", False, "Includes custom transaction fields." if new else None),
        "Q53": (1 if new else 0, False, "Includes 1 year of trial balance migration." if new else None),
        "Q54": (1000 if new else 0, new, "Includes migration of up to 1,000 open transactions." if new else None),
        "Q55": (0, False, None),
        "Q56": (0, False, None),
    }
    if qid in table:
        return table[qid]
    q = BY_ID[qid]
    if q.kind == "yesno":
        return ("No", False, None)
    if q.kind == "number":
        return (q.minimum, False, None)
    return (q.allowed[0], False, None)


DEFAULT_APPROVAL = {"process": "Standard approval workflow", "levels": 1}
DEFAULT_APPROVAL_ASSUMPTION = "Includes one single-level approval workflow."


_FIRST = ("Q01", "Q07", "Q08", "Q09", "Q21", "Q25", "Q28")
DEFAULT_ORDER = _FIRST + tuple(q.qid for q in QUESTIONS if q.qid not in _FIRST)


def coerce(qid, value):
    q = BY_ID[qid]
    if q.kind == "number":
        if isinstance(value, bool):
            return None
        try:
            f = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None
        if f != f or f < q.minimum or not f.is_integer():
            return None
        return int(f)
    text = " ".join(str(value or "").split())
    for option in q.allowed:
        if option.lower() == text.lower():
            return option
    return None


def rank(qid, value):
    q = BY_ID[qid]
    if q.kind == "number":
        return value
    return q.allowed.index(value)
