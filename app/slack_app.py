import hashlib
import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

import anthropic
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from . import slack_ui as ui
from .catalog import load_catalog
from .config import load_guide, load_policy, load_settings
from .estimator import Estimator
from .interpreter import Interpreter
from .question_bank import load_bank
from .store import Store
from .workbook import Recalculator

log = logging.getLogger("ns_estimator")


class EstimatorBot:
    def __init__(self, settings):
        self.s = settings
        self.policy = load_policy(settings.policy_path)
        self.catalog = load_catalog(settings.template_path)
        self.bank = load_bank(settings.bank_path)
        self.guide = load_guide(settings.guide_path)
        self.versions = {
            "template": self.catalog.template_sha256,
            "question_bank": self.bank.sha256,
            "guide": hashlib.sha256(self.guide.encode("utf-8")).hexdigest(),
        }
        self.store = Store(settings.database_url)
        self.recalc = Recalculator(settings.soffice_path, settings.data_dir / "lo-profile", settings.recalc_timeout)
        interpreter = Interpreter(
            anthropic.Anthropic(api_key=settings.anthropic_api_key),
            settings.anthropic_model,
            {"approvals": len(self.catalog.approval_rows), "custom": len(self.catalog.custom_rows),
             "migration": len(self.catalog.migration_rows)},
            self.bank,
            self.guide,
        )
        self.estimator = Estimator(self.catalog, self.policy, interpreter, self.recalc,
                                   settings.template_path, settings.data_dir / "files")
        self.pool = ThreadPoolExecutor(max_workers=settings.worker_threads)
        self._review_channel = None
        self.app = App(token=settings.slack_bot_token)
        self._register()

    def review_channel(self, client):
        if self._review_channel is None:
            target = self.s.review_channel_id
            if target[:1] in ("U", "W"):
                target = client.conversations_open(users=target)["channel"]["id"]
            self._review_channel = target
        return self._review_channel

    def _can_request(self, user_id):
        return not self.s.allowed_requester_user_ids or user_id in self.s.allowed_requester_user_ids

    def _can_review(self, user_id):
        return not self.s.reviewer_user_ids or user_id in self.s.reviewer_user_ids

    def _dm(self, client, user_id, text, blocks=None):
        channel = client.conversations_open(users=user_id)["channel"]["id"]
        client.chat_postMessage(channel=channel, text=text, blocks=blocks)

    def _log(self, client, rid):
        req = self.store.get(rid)
        results = json.loads(req["results_json"]) if req.get("results_json") else None
        blocks = ui.log_blocks(req, results, req["status"])
        text = f"{req['customer']}: {ui.STATUS_LABEL[req['status']]}"
        try:
            if req.get("log_ts"):
                client.chat_update(channel=self.s.log_channel_id, ts=req["log_ts"], text=text, blocks=blocks)
            else:
                resp = client.chat_postMessage(channel=self.s.log_channel_id, text=text, blocks=blocks)
                self.store.update(rid, log_ts=resp["ts"])
        except Exception:
            log.exception("Could not write log channel entry for %s", rid)

    def _register(self):
        app = self.app

        @app.command("/ns-estimate")
        def open_request(ack, body, client):
            ack()
            if not self._can_request(body["user_id"]):
                client.chat_postEphemeral(channel=body["channel_id"], user=body["user_id"],
                                          text="You are not set up to request NetSuite estimates. Ask the presales team for access.")
                return
            client.views_open(trigger_id=body["trigger_id"], view=ui.request_modal(body.get("text", "").strip()))

        @app.view(ui.REQUEST_VIEW)
        def submit_request(ack, body, client, view):
            data = ui.parse_request(view["state"]["values"])
            customer, requirement = data["customer"], data["requirement"]
            errors = {}
            if not customer:
                errors["customer"] = "Enter the customer name."
            if len(requirement) < 20:
                errors["requirement"] = "Describe the requirement in a little more detail."
            if "Q01" not in data["form"]:
                errors["deal_type"] = "Choose the deal type."
            if errors:
                ack(response_action="errors", errors=errors)
                return
            ack()
            user = body["user"]
            rid = self.store.create(user["id"], user.get("name"), customer, data["opportunity"], requirement,
                                    data["strategic"], self.catalog.template_sha256, self.s.anthropic_model,
                                    form=data["form"])
            self._dm(client, user["id"], f"Working on the NetSuite ROM for *{customer}*. I'll message you here when it's ready. Request {rid}.")
            self.pool.submit(self._run, client, rid)

        @app.action(ui.APPROVE_ACTION)
        def approve(ack, body, client):
            ack()
            rid = body["actions"][0]["value"]
            user_id = body["user"]["id"]
            if not self._can_review(user_id):
                client.chat_postEphemeral(channel=body["channel"]["id"], user=user_id, text="Only the estimate reviewer can send estimates to AEs.")
                return
            if not self.store.transition(rid, ("in_review",), user_id, "approved", status="delivered", reviewer_id=user_id):
                client.chat_postEphemeral(channel=body["channel"]["id"], user=user_id, text="This estimate has already been decided.")
                return
            self._refresh_card(client, rid, decided_by=user_id)
            self._deliver(client, rid, approved_by=user_id)
            self._log(client, rid)

        @app.action(ui.RETURN_ACTION)
        def open_return(ack, body, client):
            ack()
            user_id = body["user"]["id"]
            if not self._can_review(user_id):
                client.chat_postEphemeral(channel=body["channel"]["id"], user=user_id, text="Only the estimate reviewer can send estimates back.")
                return
            client.views_open(trigger_id=body["trigger_id"],
                              view=ui.return_modal(body["actions"][0]["value"], body["channel"]["id"], body["message"]["ts"]))

        @app.view(ui.RETURN_VIEW)
        def submit_return(ack, body, client, view):
            note = (view["state"]["values"]["note"]["value"]["value"] or "").strip()
            if not note:
                ack(response_action="errors", errors={"note": "Tell the AE what to clarify."})
                return
            ack()
            rid = view["private_metadata"].split("|")[0]
            user_id = body["user"]["id"]
            if not self.store.transition(rid, ("in_review",), user_id, "returned", detail={"note": note},
                                         status="returned", reviewer_id=user_id, decision_note=note):
                return
            req = self.store.get(rid)
            self._refresh_card(client, rid, decided_by=user_id, note=note)
            self._dm(client, req["requester_id"],
                     f"More detail is needed before the ROM for *{req['customer']}* can be released (request {rid}):\n> {note}\nRun /ns-estimate again with the added detail.")
            self._log(client, rid)

        @app.action(ui.PRICING_ACTION)
        def open_pricing(ack, body, client):
            ack()
            user_id = body["user"]["id"]
            channel = body["channel"]["id"]
            if not self._can_review(user_id):
                client.chat_postEphemeral(channel=channel, user=user_id, text="Only the estimate reviewer can adjust pricing.")
                return
            req = self.store.get(body["actions"][0]["value"])
            if not req or req["status"] != "in_review":
                client.chat_postEphemeral(channel=channel, user=user_id, text="This estimate has already been decided.")
                return
            client.views_open(trigger_id=body["trigger_id"], view=ui.pricing_modal(req["id"], json.loads(req["results_json"])))

        @app.view(ui.PRICING_VIEW)
        def submit_pricing(ack, body, client, view):
            pricing, errors = ui.parse_pricing(view["state"]["values"])
            if errors:
                ack(response_action="errors", errors=errors)
                return
            ack()
            user_id = body["user"]["id"]
            if not self._can_review(user_id):
                return
            self.pool.submit(self._reprice, client, view["private_metadata"], user_id, pricing)

        @app.event("message")
        def on_message(event, client, context):
            if event.get("subtype") != "file_share" or event.get("channel") != self.review_channel(client):
                return
            if event.get("bot_id") or event.get("user") == context.get("bot_user_id"):
                return
            thread_ts = event.get("thread_ts")
            if not thread_ts or thread_ts == event.get("ts"):
                return
            req = self.store.by_review_ts(self.review_channel(client), thread_ts)
            if not req:
                return
            user_id = event.get("user")
            files = [f for f in event.get("files", []) if str(f.get("name", "")).lower().endswith(".xlsx")]
            if not files:
                return
            if req["status"] != "in_review":
                client.chat_postMessage(channel=self.review_channel(client), thread_ts=thread_ts,
                                        text="This estimate has already been decided, so the upload was not applied.")
                return
            if not self._can_review(user_id):
                client.chat_postMessage(channel=self.review_channel(client), thread_ts=thread_ts,
                                        text=f"<@{user_id}> only the estimate reviewer can update this estimate.")
                return
            self.pool.submit(self._apply_upload, client, req["id"], files[-1], user_id, thread_ts)

    def _run(self, client, rid):
        req = self.store.get(rid)
        self.store.update(rid, actor="bot", event="estimating", status="estimating")
        try:
            form = json.loads(req["form_json"] or "{}")
            est = self.estimator.estimate(rid, req["customer"], req["requirement"], form, req["strategic"])
        except Exception as exc:
            log.exception("Estimate %s failed", rid)
            self.store.update(rid, actor="bot", event="failed", detail={"error": str(exc)}, status="failed", error=str(exc)[:2000])
            self._dm(client, req["requester_id"],
                     f"I couldn't produce the ROM for *{req['customer']}* (request {rid}). Presales has been notified.\nReason: {str(exc)[:300]}")
            try:
                client.chat_postMessage(channel=self.review_channel(client),
                                        text=f"Estimate {rid} for {req['customer']} by <@{req['requester_id']}> failed: {str(exc)[:500]}")
            except Exception:
                log.exception("Could not notify review channel for %s", rid)
            self._log(client, rid)
            return

        interp = {**est.scope.to_dict(), "line_hours": est.line_hours, "versions": self.versions}
        results = asdict(est.results)
        self.store.update(rid, actor="bot", event="estimated",
                          detail={"workbook": str(est.workbook_path), "reasons": est.reasons, "reads": est.scope.reads},
                          interpretation_json=interp, results_json=results, review_reasons_json=est.reasons,
                          deal_type=est.scope.value("Q01"), workbook_path=str(est.workbook_path), version=1)

        try:
            self._route(client, rid, est, interp, results)
        except Exception as exc:
            log.exception("Routing %s failed", rid)
            self.store.update(rid, actor="bot", event="failed", detail={"error": str(exc)}, status="failed", error=str(exc)[:2000])
            self._dm(client, req["requester_id"],
                     f"Your ROM for *{req['customer']}* was calculated but could not be delivered (request {rid}). Presales has been notified.")
        self._log(client, rid)

    def _route(self, client, rid, est, interp, results):
        if est.reasons:
            self.store.update(rid, actor="bot", event="sent_to_review", status="in_review")
            req = self.store.get(rid)
            blocks = ui.review_blocks(req, interp, results, est.reasons, "in_review")
            card = client.chat_postMessage(channel=self.review_channel(client), text=f"Review needed: {req['customer']}", blocks=blocks)
            self.store.update(rid, review_channel=self.review_channel(client), review_ts=card["ts"])
            client.files_upload_v2(channel=self.review_channel(client), thread_ts=card["ts"],
                                   file=str(est.workbook_path), filename=Path(est.workbook_path).name,
                                   title=f"{req['customer']} estimator v1")
            self._dm(client, req["requester_id"],
                     f"The ROM for *{req['customer']}* is calculated and with presales for review. You'll get it here once it's released. Request {rid}.")
        else:
            self._deliver(client, rid)
            self.store.update(rid, actor="bot", event="delivered", status="delivered")

    def _apply_upload(self, client, rid, file_info, user_id, thread_ts):
        req = self.store.get(rid)
        version = int(req["version"] or 1) + 1
        tmp = self.s.data_dir / "files" / rid / f"upload_{version}.xlsx"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            url = file_info.get("url_private_download") or file_info["url_private"]
            if not url.startswith("https://files.slack.com/"):
                raise ValueError("file is not hosted by Slack")
            request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.s.slack_bot_token}"})
            with urllib.request.urlopen(request, timeout=60) as resp, open(tmp, "wb") as out:
                out.write(resp.read())
            results, lines, answers, approvals, custom, migration, path = self.estimator.recalculate_upload(rid, tmp, version)
        except Exception as exc:
            log.exception("Upload for %s could not be applied", rid)
            client.chat_postMessage(channel=self.review_channel(client), thread_ts=thread_ts,
                                    text=f"I couldn't use that file: {str(exc)[:400]}. Upload the estimator workbook (.xlsx) from this thread after editing it.")
            return
        finally:
            tmp.unlink(missing_ok=True)
        self._apply_version(client, req, user_id, version, (results, lines, answers, approvals, custom, migration, path),
                            "reviewer_upload", f"Applied v{version} from <@{user_id}>")

    def _reprice(self, client, rid, user_id, pricing):
        req = self.store.get(rid)
        if req["status"] != "in_review":
            return
        version = int(req["version"] or 1) + 1
        try:
            outcome = self.estimator.reprice(rid, req["workbook_path"], version, pricing["onsite_rate"],
                                             pricing["offshore_rate"], pricing["onsite_share"])
        except Exception as exc:
            log.exception("Repricing %s failed", rid)
            client.chat_postMessage(channel=req["review_channel"], thread_ts=req["review_ts"],
                                    text=f"Repricing failed: {str(exc)[:400]}")
            return
        self._apply_version(client, req, user_id, version, outcome, "repriced",
                            f"Repriced v{version} by <@{user_id}>", detail_extra={"pricing": pricing})
        path = outcome[-1]
        client.files_upload_v2(channel=req["review_channel"], thread_ts=req["review_ts"], file=str(path),
                               filename=Path(path).name, title=f"{req['customer']} estimator v{version}")

    def _apply_version(self, client, req, user_id, version, outcome, event, lead, detail_extra=None):
        rid = req["id"]
        results, lines, answers, approvals, custom, migration, path = outcome
        data = asdict(results)
        interp = json.loads(req["interpretation_json"])
        before = interp.get("answers", {})
        for qid, entry in answers.items():
            if qid in before and entry["value"] != before[qid].get("value"):
                entry["source"] = "Reviewer"
                entry["evidence"] = ""
            elif qid in before:
                entry["source"] = before[qid].get("source", entry["source"])
                entry["evidence"] = before[qid].get("evidence", entry["evidence"])
        interp.update(answers=answers, approvals=approvals, custom_items=custom, migration_objects=migration, line_hours=lines)
        if not self.store.transition(rid, ("in_review",), user_id, event, expect_version=version - 1,
                                     detail={"version": version, "workbook": str(path), **(detail_extra or {})},
                                     results_json=json.dumps(data), interpretation_json=json.dumps(interp),
                                     workbook_path=str(path), version=version):
            client.chat_postMessage(channel=req["review_channel"], thread_ts=req["review_ts"],
                                    text="The estimate changed while this was processing (decided, or another change landed first), so it was not applied.")
            return
        self._refresh_card(client, rid)
        client.chat_postMessage(channel=req["review_channel"], thread_ts=req["review_ts"],
                                text=f"{lead}: {ui._range(data)} USD (exact {ui._money(results.price)}), "
                                     f"{results.weeks_total} weeks, ${results.blended_rate:,.2f}/hr blended. Send to AE sends this version.")

    def _refresh_card(self, client, rid, decided_by=None, note=None):
        req = self.store.get(rid)
        if not req.get("review_ts"):
            return
        interp = json.loads(req["interpretation_json"])
        results = json.loads(req["results_json"])
        reasons = json.loads(req["review_reasons_json"] or "[]")
        blocks = ui.review_blocks(req, interp, results, reasons, req["status"], decided_by, note)
        client.chat_update(channel=req["review_channel"], ts=req["review_ts"], text=f"Review: {req['customer']}", blocks=blocks)

    def _deliver(self, client, rid, approved_by=None):
        req = self.store.get(rid)
        interp = json.loads(req["interpretation_json"])
        results = json.loads(req["results_json"])
        title = f"NetSuite ROM: {req['customer']}"
        blocks = ui.ae_blocks(req, interp, results, self.policy, title)
        if approved_by:
            blocks.insert(1, {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Reviewed and released by <@{approved_by}>"}]})
        self._dm(client, req["requester_id"], f"{title}: {ui._range(results)} USD, {results['weeks_total']} weeks", blocks)

    def start(self):
        SocketModeHandler(self.app, self.s.slack_app_token).start()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    EstimatorBot(load_settings()).start()


if __name__ == "__main__":
    main()
