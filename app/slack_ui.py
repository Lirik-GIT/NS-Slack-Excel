from .intake import address_of, mask_email
from .questions import ADDON_OPTIONS, ADDON, AREA_OPTIONS, BY_ID, NEW, QUESTIONS

REQUEST_VIEW = "ns_estimate_request"
RETURN_VIEW = "ns_estimate_return"
APPROVE_ACTION = "ns_estimate_approve"
RETURN_ACTION = "ns_estimate_return_open"
PRICING_ACTION = "ns_estimate_pricing_open"
PRICING_VIEW = "ns_estimate_pricing"

STATUS_LABEL = {
    "received": "Received",
    "estimating": "Estimating",
    "in_review": "Waiting for review",
    "delivered": "Sent to AE",
    "returned": "Sent back to AE for detail",
    "failed": "Failed",
}

SCOPE_GROUPS = {"Areas", "Add-ons"}
FACT_SKIP = {"Q01"} | {q.qid for q in QUESTIONS if q.group in SCOPE_GROUPS}


def _cut(text, limit=2900):
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _money(v, cur="$"):
    return f"{cur}{v:,.0f}"


def _num(v):
    return f"{v:,.1f}".rstrip("0").rstrip(".")


def _val(v):
    return f"{v:,}" if isinstance(v, int) else str(v)


def _sections(title, lines, limit=2800):
    blocks, chunk = [], f"*{title}*"
    for line in lines:
        if len(chunk) + len(line) + 1 > limit:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
            chunk = ""
        chunk = f"{chunk}\n{line}" if chunk else line
    if chunk:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    return blocks


def _opt(text, value):
    return {"text": {"type": "plain_text", "text": text[:75]}, "value": value}


def request_modal(prefill=""):
    return {
        "type": "modal",
        "callback_id": REQUEST_VIEW,
        "title": {"type": "plain_text", "text": "NetSuite ROM estimate"},
        "submit": {"type": "plain_text", "text": "Get estimate"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "input", "block_id": "customer", "label": {"type": "plain_text", "text": "Customer"},
             "element": {"type": "plain_text_input", "action_id": "value", "max_length": 200}},
            {"type": "input", "block_id": "opportunity", "optional": True,
             "label": {"type": "plain_text", "text": "Opportunity link or ID"},
             "element": {"type": "plain_text_input", "action_id": "value", "max_length": 400}},
            {"type": "input", "block_id": "deal_type", "label": {"type": "plain_text", "text": "Deal type"},
             "element": {"type": "radio_buttons", "action_id": "value",
                         "options": [_opt("New NetSuite implementation", NEW), _opt("Add-on to an existing NetSuite account", ADDON)]}},
            {"type": "input", "block_id": "subsidiaries", "optional": True,
             "label": {"type": "plain_text", "text": "Number of subsidiaries"},
             "element": {"type": "number_input", "action_id": "value", "is_decimal_allowed": False, "min_value": "1", "max_value": "500"}},
            {"type": "input", "block_id": "areas", "optional": True,
             "label": {"type": "plain_text", "text": "Areas in scope"},
             "hint": {"type": "plain_text", "text": "Anything not selected here is taken from the requirement text."},
             "element": {"type": "multi_static_select", "action_id": "value",
                         "placeholder": {"type": "plain_text", "text": "Select areas"},
                         "options": [_opt(label, qid) for qid, label in AREA_OPTIONS]}},
            {"type": "input", "block_id": "addons", "optional": True,
             "label": {"type": "plain_text", "text": "Add-on modules"},
             "element": {"type": "multi_static_select", "action_id": "value",
                         "placeholder": {"type": "plain_text", "text": "Select modules"},
                         "options": [_opt(label, qid) for qid, label in ADDON_OPTIONS]}},
            {"type": "input", "block_id": "requirement", "label": {"type": "plain_text", "text": "Requirement"},
             "hint": {"type": "plain_text", "text": "Describe the scope: processes, volumes, tax states, PDFs, approvals, reports, customizations, integrations and data to migrate."},
             "element": {"type": "plain_text_input", "action_id": "value", "multiline": True, "min_length": 20, "max_length": 3000,
                         **({"initial_value": prefill[:3000]} if prefill else {})}},
            {"type": "input", "block_id": "strategic", "optional": True, "label": {"type": "plain_text", "text": "Priority"},
             "element": {"type": "checkboxes", "action_id": "value",
                         "options": [_opt("Strategic deal", "strategic")]}},
        ],
    }


def parse_request(values):
    form = {}
    deal = (values["deal_type"]["value"].get("selected_option") or {}).get("value")
    if deal in (NEW, ADDON):
        form["Q01"] = deal
    subs = values["subsidiaries"]["value"].get("value")
    if subs not in (None, ""):
        form["Q02"] = int(subs)
    for block in ("areas", "addons"):
        for option in values[block]["value"].get("selected_options") or []:
            if option["value"] in BY_ID:
                form[option["value"]] = "Yes"
    return {
        "customer": (values["customer"]["value"].get("value") or "").strip(),
        "opportunity": (values["opportunity"]["value"].get("value") or "").strip(),
        "requirement": (values["requirement"]["value"].get("value") or "").strip(),
        "strategic": bool(values["strategic"]["value"].get("selected_options")),
        "form": form,
    }


def requester_label(req, mask=False):
    """Who asked: a Slack mention, or the web-form address (masked for the log channel)."""
    if req.get("source") != "web":
        return f"<@{req['requester_id']}>"
    address = address_of(req)
    return f"Web form: {mask_email(address) if mask else address}"


def return_modal(request_id, channel, ts, external=False):
    blocks = [{"type": "input", "block_id": "note",
               "label": {"type": "plain_text", "text": "What does the AE need to clarify?"},
               "element": {"type": "plain_text_input", "action_id": "value", "multiline": True, "max_length": 2000}}]
    if external:
        blocks.insert(0, {"type": "context", "elements": [{"type": "mrkdwn", "text":
            ":warning: This note is emailed to an external Oracle AE. Do not include prices, rates, hours or internal comments."}]})
    return {
        "type": "modal",
        "callback_id": RETURN_VIEW,
        "private_metadata": f"{request_id}|{channel}|{ts}",
        "title": {"type": "plain_text", "text": "Ask AE for detail"},
        "submit": {"type": "plain_text", "text": "Send"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def pricing_modal(request_id, current):
    def num(block, label, value, hint, decimal=True, lo="0", hi="100000"):
        return {"type": "input", "block_id": block, "label": {"type": "plain_text", "text": label},
                "hint": {"type": "plain_text", "text": hint},
                "element": {"type": "number_input", "action_id": "value", "is_decimal_allowed": decimal,
                            "min_value": lo, "max_value": hi, "initial_value": _plainnum(value)}}
    return {
        "type": "modal",
        "callback_id": PRICING_VIEW,
        "private_metadata": request_id,
        "title": {"type": "plain_text", "text": "Adjust pricing"},
        "submit": {"type": "plain_text", "text": "Recalculate"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            num("onsite_rate", "Onsite rate (USD per hour)", current["onsite_rate"], "Rate for onsite hours"),
            num("offshore_rate", "Offshore rate (USD per hour)", current["offshore_rate"], "Rate for offshore hours"),
            num("onsite_share", "Onsite share of hours (%)", round(current["onsite_share"] * 100, 2),
                "The rest of the hours are offshore", hi="100"),
        ],
    }


def parse_pricing(values):
    def get(block):
        return float(values[block]["value"]["value"])
    onsite, offshore, share = get("onsite_rate"), get("offshore_rate"), get("onsite_share")
    errors = {}
    if onsite <= 0:
        errors["onsite_rate"] = "Enter a rate above zero."
    if offshore <= 0:
        errors["offshore_rate"] = "Enter a rate above zero."
    if not 0 <= share <= 100:
        errors["onsite_share"] = "Enter a share from 0 to 100."
    return {"onsite_rate": onsite, "offshore_rate": offshore, "onsite_share": round(share / 100, 4)}, errors


def _plainnum(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def _in_scope(answers):
    labels = dict(AREA_OPTIONS + ADDON_OPTIONS)
    out = []
    if answers.get("Q01", {}).get("value"):
        out.append(str(answers["Q01"]["value"]))
    for qid, label in AREA_OPTIONS + ADDON_OPTIONS:
        if answers.get(qid, {}).get("value") == "Yes":
            out.append(labels[qid])
    return out


def _facts(answers, with_source):
    lines = []
    for q in QUESTIONS:
        if q.qid in FACT_SKIP or q.qid not in answers:
            continue
        a = answers[q.qid]
        v = a.get("value")
        if v in (None, "", 0, "No", "None") and a.get("source") != "Form":
            continue
        if not with_source and a.get("source") == "Default":
            continue
        line = f"• {q.text} *{_val(v)}*"
        if with_source:
            src = a.get("source") or ""
            if a.get("evidence") and src.startswith("Requirement"):
                line += f"  _“{_cut(a['evidence'], 140)}”_"
            elif src:
                line += f"  _({src.lower()})_"
        lines.append(line)
    return lines


def _items(interp):
    lines = []
    for a in interp.get("approvals") or []:
        lines.append(f"• Approval: {a.get('process')} ({a.get('levels')} level{'s' if a.get('levels') != 1 else ''})")
    for c in interp.get("custom_items") or []:
        lines.append(f"• {c.get('type')}: {c.get('name')} ({str(c.get('size')).lower()})")
    for m in interp.get("migration_objects") or []:
        lines.append(f"• Migration: {m.get('name')} ({str(m.get('size')).lower()})")
    return lines


def _range(results):
    return f"{_money(results['rom_low'])} – {_money(results['rom_high'])}"


def _duration(results):
    return (f"{results['weeks_total']} weeks: {results['weeks_requirements']} requirements, "
            f"{results['weeks_build']} build, {results['weeks_uat']} UAT, {results['weeks_golive']} go-live")


def _milestone_split(results):
    return " / ".join(f"{int(round(p * 100))}%" for p, _ in results["milestones"] if p)


def _question_lines(questions, limit=None, with_trigger=True):
    shown = questions if limit is None else questions[:limit]
    lines = []
    for q in shown:
        line = f"• {q['question']}"
        t = q.get("trigger")
        if with_trigger and t:
            line += (f"  _(keyword “{t['detail']}”, S{t['sentence']})_" if t["how"] == "keyword"
                     else f"  _(S{t['sentence']}: “{_cut(t['detail'], 100)}”)_")
        lines.append(line)
    if limit is not None and len(questions) > limit:
        lines.append(f"• …and {len(questions) - limit} more")
    return lines


def ae_blocks(req, interp, results, policy, title):
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": _cut(title, 150)}},
              {"type": "section", "fields": [
                  {"type": "mrkdwn", "text": f"*ROM price range*\n{_range(results)} USD"},
                  {"type": "mrkdwn", "text": f"*Duration*\n{results['weeks_total']} weeks"},
              ]},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": _cut(
                  f"{_duration(results)}  ·  Payment milestones {_milestone_split(results)}")}]}]
    if interp.get("scope_summary"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _cut(f"*Scope as understood*\n{interp['scope_summary']}")}})
    included = [f"• {s}" for s in _in_scope(interp["answers"])] + _facts(interp["answers"], False) + \
        [i for i in _items(interp) if "Standard approval workflow" not in i]
    blocks += _sections("What is included", included)
    if interp.get("assumptions"):
        blocks += _sections("Assumptions and exclusions", [f"• {a}" for a in interp["assumptions"]])
    if interp.get("not_covered"):
        blocks += _sections("Not priced in this ROM", [f"• {n['text']}" for n in interp["not_covered"]])
    if interp.get("questions"):
        blocks += _sections("Questions to confirm with the customer",
                            _question_lines(interp["questions"], policy["ae_message"].get("max_questions_shown", 10), False))
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _cut(f"{policy['ae_message']['disclaimer']}  ·  Request {req['id']}")}]})
    return blocks[:50]


def review_blocks(req, interp, results, reasons, status, decided_by=None, note=None):
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": _cut(f"Review: {req['customer']}", 150)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Requested by* {requester_label(req)}  ·  *Status* {STATUS_LABEL[status]}  ·  Request {req['id']} v{req.get('version') or 1}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*ROM range (AE sees)*\n{_range(results)} USD"},
            {"type": "mrkdwn", "text": f"*Exact price*\n{_money(results['price'])} USD  ·  {_money(results['price_cad'])} CAD"},
            {"type": "mrkdwn", "text": f"*Blended rate*\n${results['blended_rate']:,.2f}/hr  ·  {int(round(results['onsite_share'] * 100))}% onsite at ${results['onsite_rate']:,.0f}, rest offshore at ${results['offshore_rate']:,.0f}"},
            {"type": "mrkdwn", "text": f"*Effort*\n{_num(results['total_hours'])} hours  ·  build {_num(results['dev_days'])} person-days"},
            {"type": "mrkdwn", "text": f"*Duration*\n{_duration(results)}"},
        ]},
    ]
    if reasons:
        blocks += _sections("Why this needs review", [f"• {r}" for r in reasons])
    if interp.get("scope_summary"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _cut(f"*Scope as understood*\n{interp['scope_summary']}")}})
    blocks += _sections("In scope", [f"• {s}" for s in _in_scope(interp["answers"])])
    facts = _facts(interp["answers"], True)
    if facts:
        blocks += _sections("Facts used", facts)
    items = _items(interp)
    if items:
        blocks += _sections("Approvals, custom items and extra migration", items)
    if interp.get("assumptions"):
        blocks += _sections("Assumptions and exclusions", [f"• {a}" for a in interp["assumptions"]])
    if interp.get("questions"):
        blocks += _sections("Open questions", _question_lines(interp["questions"]))
    if interp.get("conflicts"):
        blocks += _sections("Reads differed (higher-hours value used)", [f"• {c}" for c in interp["conflicts"]])
    if interp.get("not_covered"):
        blocks += _sections("Not priced", [f"• S{n['n']}: {n['text']}" for n in interp["not_covered"]])
    if interp.get("form_flags"):
        blocks += _sections("Form vs requirement", [f"• {f}" for f in interp["form_flags"]])
    hours = sorted((interp.get("line_hours") or {}).values(), key=lambda h: -h["hours"])
    if hours:
        blocks += _sections("Hours by line", [f"• {h['section'].title()} · {h['name'][:70]}: {_num(h['hours'])}" for h in hours[:30]]
                            + ([f"• …and {len(hours) - 30} more lines"] if len(hours) > 30 else []))
    if interp.get("issues"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _cut("Checks: " + "; ".join(interp["issues"]))}]})
    if status == "in_review":
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "To change answers, edit the Inputs tab of the workbook in this thread and upload it back here. Adjust pricing changes the rates for this deal only. Send to AE sends the latest version."}]})
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "style": "primary", "action_id": APPROVE_ACTION, "value": req["id"], "text": {"type": "plain_text", "text": "Send to AE"}},
            {"type": "button", "action_id": PRICING_ACTION, "value": req["id"], "text": {"type": "plain_text", "text": "Adjust pricing"}},
            {"type": "button", "action_id": RETURN_ACTION, "value": req["id"], "text": {"type": "plain_text", "text": "Ask AE for detail"}},
        ]})
    else:
        outcome = STATUS_LABEL[status] + (f" by <@{decided_by}>" if decided_by else "")
        if note:
            outcome += f"\n> {_cut(note, 1500)}"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": outcome}})
    if len(blocks) > 50:
        tail = blocks[-2:] if status == "in_review" else blocks[-1:]
        blocks = blocks[: 50 - len(tail)] + tail
    return blocks


def log_blocks(req, results, status):
    fee = _range(results) if results else "n/a"
    weeks = f"{results['weeks_total']} wks" if results else ""
    opp = f"  ·  {req['opportunity']}" if req.get("opportunity") else ""
    text = (f"*{req['customer']}*  ·  {fee}  {weeks}  ·  {STATUS_LABEL[status]}\n"
            f"Requested by {requester_label(req, mask=True)}{opp}  ·  Request {req['id']}")
    if req.get("deal_type"):
        text += f"  ·  {req['deal_type']}"
    return [{"type": "section", "text": {"type": "mrkdwn", "text": _cut(text)}}]
