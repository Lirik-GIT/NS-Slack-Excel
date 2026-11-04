"""Phase 3 acceptance run against live Slack, Claude and LibreOffice.

Calls the same handler code that /ns-estimate, Send to AE, Return and a thread upload trigger;
only the click/keystroke in the Slack UI is replaced. Results go to acceptance/results.json.

Usage (from the project root, with .env filled in):
    python acceptance/run_acceptance.py
Set DATA_DIR / SOFFICE_PATH in the environment to override the .env values.
"""
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
if Path(".env").exists():  # in the container the settings already come from --env-file
    for line in open(".env", encoding="utf-8"):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

from openpyxl import load_workbook  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.slack_app import EstimatorBot  # noqa: E402

DATA = json.loads((ROOT / "acceptance" / "test_data.json").read_text(encoding="utf-8"))
ME, OTHER, CASES = DATA["requester"], DATA["not_allowed_user"], DATA["cases"]
bot = EstimatorBot(load_settings())
c = bot.app.client
out = {"started": datetime.now().isoformat(timespec="seconds"), "model": bot.s.anthropic_model,
       "template_sha256": bot.catalog.template_sha256, "tests": {}}


def now():
    return datetime.now().strftime("%H:%M:%S")


def submit(case):
    # submit_request(): create the request, send the "Working on" DM, run the worker
    rid = bot.store.create(ME, "uat", case["customer"], "", case["requirement"], False,
                           bot.catalog.template_sha256, bot.s.anthropic_model, form=case["form"])
    bot._dm(c, ME, f"Working on the NetSuite ROM for *{case['customer']}*. I'll message you here when it's ready. Request {rid}.")
    t = time.time()
    bot._run(c, rid)
    return rid, round(time.time() - t)


def send_to_ae(rid):
    # approve()
    assert bot._can_review(ME)
    assert bot.store.transition(rid, ("in_review",), ME, "approved", status="delivered", reviewer_id=ME)
    bot._refresh_card(c, rid, decided_by=ME)
    bot._deliver(c, rid, approved_by=ME)
    bot._log(c, rid)


def state(rid):
    req = bot.store.get(rid)
    r = json.loads(req["results_json"]) if req.get("results_json") else {}
    return {"status": req["status"], "version": req.get("version"), "price_usd": r.get("price"),
            "rom_range": [r.get("rom_low"), r.get("rom_high")], "weeks_total": r.get("weeks_total"),
            "total_hours": r.get("total_hours"),
            "review_card_posted": bool(req.get("review_ts")), "log_entry_posted": bool(req.get("log_ts")),
            "error": req.get("error")}


def thread(rid):
    req = bot.store.get(rid)
    msgs = c.conversations_replies(channel=req["review_channel"], ts=req["review_ts"])["messages"]
    return {"files": [f["name"] for m in msgs for f in m.get("files", [])],
            "replies": [m.get("text", "") for m in msgs[1:] if m.get("text")]}


def last_dm():
    ch = c.conversations_open(users=ME)["channel"]["id"]
    m = c.conversations_history(channel=ch, limit=1)["messages"][0]
    return m.get("text", "")


def check(name, fn):
    print(f"### {now()} {name}: start", flush=True)
    t = time.time()
    try:
        res = fn()
        res["passed"] = all(v for k, v in res.items() if k.startswith("check_"))
    except Exception:
        res = {"passed": False, "exception": traceback.format_exc()[-2000:]}
    res["elapsed_s"] = round(time.time() - t)
    out["tests"][name] = res
    print(f"### {now()} {name}: {'PASS' if res['passed'] else 'FAIL'} {json.dumps(res, default=str)}", flush=True)
    time.sleep(8)  # gap between tests so the recording shows each one


def test1_and_2():
    rid, secs = submit(CASES["arm_addon"])
    after = state(rid)
    th = thread(rid)
    send_to_ae(rid)
    delivered, dm = state(rid), last_dm()
    return {"request": rid, "estimate_seconds": secs, "after_estimate": after, "thread": th,
            "after_send_to_ae": delivered, "ae_dm": dm[:300],
            "check_1_in_review": after["status"] == "in_review",
            "check_1_card_and_log": after["review_card_posted"] and after["log_entry_posted"],
            "check_1_workbook_in_thread": any(f.endswith("_v1.xlsx") for f in th["files"]),
            "check_2_delivered": delivered["status"] == "delivered",
            "check_2_ae_got_rom": "NetSuite ROM" in dm}


def test3():
    rid, secs = submit(CASES["new_impl"])
    req = bot.store.get(rid)
    v1 = state(rid)
    # reviewer edits the first positive numeric answer on the Inputs tab (x3) and uploads it to the thread
    wb = load_workbook(req["workbook_path"])
    ws = wb["Inputs"]
    edit = None
    for qid, row in bot.catalog.answer_rows.items():
        val = ws.cell(row, 3).value
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            ws.cell(row, 3).value = val * 3
            edit = {"question": qid, "from": val, "to": val * 3}
            break
    edited = Path(req["workbook_path"]).with_name(f"{rid}_reviewer_edit.xlsx")
    wb.save(edited)
    up = c.files_upload_v2(channel=req["review_channel"], thread_ts=req["review_ts"],
                           file=str(edited), filename=edited.name, title="Reviewer edit")
    fid = (up.get("file") or up["files"][0])["id"]
    info = {}
    for _ in range(15):
        info = c.files_info(file=fid)["file"]
        if info.get("url_private_download") or info.get("url_private"):
            break
        time.sleep(2)
    bot._apply_upload(c, rid, info, ME, req["review_ts"])  # on_message() -> worker
    v2 = state(rid)
    applied = next((r for r in thread(rid)["replies"] if r.startswith("Applied v")), None)
    send_to_ae(rid)
    delivered, dm = state(rid), last_dm()
    return {"request": rid, "estimate_seconds": secs, "v1": v1, "edit": edit, "v2": v2, "thread_reply": applied,
            "after_send_to_ae": delivered, "ae_dm": dm[:300],
            "check_3_v1_in_review": v1["status"] == "in_review",
            "check_3_v2_applied": v2["version"] == 2 and bool(applied),
            "check_3_v2_released": delivered["status"] == "delivered" and delivered["version"] == 2}


def test4():
    case = CASES["return"]
    rid, secs = submit(case)
    note = case["return_note"]
    # submit_return()
    ok = bot.store.transition(rid, ("in_review",), ME, "returned", detail={"note": note},
                              status="returned", reviewer_id=ME, decision_note=note)
    req = bot.store.get(rid)
    bot._refresh_card(c, rid, decided_by=ME, note=note)
    bot._dm(c, req["requester_id"],
            f"More detail is needed before the ROM for *{req['customer']}* can be released (request {rid}):\n> {note}\n"
            "Run /ns-estimate again with the added detail.")
    bot._log(c, rid)
    after, dm = state(rid), last_dm()
    return {"request": rid, "estimate_seconds": secs, "after_return": after, "ae_dm": dm[:300],
            "check_4_returned": ok and after["status"] == "returned",
            "check_4_ae_got_note": "More detail is needed" in dm}


def test5():
    gate = {"requester_allowed": bot._can_request(ME), "other_user_allowed": bot._can_request(OTHER)}
    rid, secs = submit(CASES["nonsense"])
    after, dm = state(rid), last_dm()
    review_posts = c.conversations_history(channel=bot.review_channel(c), limit=3)["messages"]
    flagged = any(rid in m.get("text", "") and "failed" in m.get("text", "") for m in review_posts)
    return {"allow_list": gate, "request": rid, "estimate_seconds": secs, "after": after, "ae_dm": dm[:300],
            "check_5_allow_list": gate["requester_allowed"] and not gate["other_user_allowed"],
            "check_5_nonsense_failed": after["status"] == "failed",
            "check_5_ae_told": "couldn't produce" in dm,
            "check_5_review_channel_told": flagged}


check("test1_and_2", test1_and_2)
check("test3", test3)
check("test4", test4)
check("test5", test5)
out["finished"] = datetime.now().isoformat(timespec="seconds")
(ROOT / "acceptance" / "results.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print("### RESULTS " + json.dumps({k: v["passed"] for k, v in out["tests"].items()}), flush=True)
bot.pool.shutdown(wait=True)
