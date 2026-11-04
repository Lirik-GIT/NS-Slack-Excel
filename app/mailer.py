"""Email to Oracle AEs through SES: approved (PDF attached), returned (reviewer note + resubmit link), failed (generic).

Nothing here takes error text, reviewer reasons, rates or hours. Bounces and complaints come back through an SQS queue
(SES -> SNS -> SQS) and are posted in the review thread by `handle_ses_event`.
"""
import json
import logging
import re
from email.message import EmailMessage

from . import intake, pdf, tokens

log = logging.getLogger("ns_estimator.mailer")

FAILED_TEXT = ("Thank you for your request. We could not complete your NetSuite ROM estimate automatically, "
               "so our team is reviewing it manually and will reply to you directly.")


def one_line(text, limit=150):
    """Header-safe text: control characters (CR/LF included) become spaces, so a field cannot inject headers."""
    return re.sub(r"[\x00-\x1f\x7f\s]+", " ", str(text)).strip()[:limit]


def file_name(customer):
    return (re.sub(r"[^A-Za-z0-9]+", "_", customer).strip("_")[:40] or "Customer")


class Mailer:
    def __init__(self, store, ses, web_cfg, policy, renderer, hmac_key):
        self.store, self.ses, self.policy, self.renderer, self.key = store, ses, policy, renderer, hmac_key
        email = web_cfg.get("email", {})
        self.sender = email["sender"]
        self.form_url = (email.get("form_url") or "").rstrip("/")
        self.config_set = email.get("configuration_set")
        self.cc_owner = web_cfg.get("reviewers", {}).get("lirik_presales_owner_email")
        self.resubmit_days = web_cfg.get("limits", {}).get("resubmit_link_days", 14)

    def resubmit_link(self, req):
        if not (self.form_url and self.key):
            raise RuntimeError("form_url or HMAC key is not configured")
        token = tokens.sign(self.key, {"k": "resubmit", "sid": req["submission_id"]}, self.resubmit_days * 86400)
        return f"{self.form_url}/resubmit?t={token}"

    def build(self, kind, req, **ctx):
        to = intake.address_of(req)
        msg = EmailMessage()
        msg["From"], msg["To"] = self.sender, to
        meta = json.loads(req.get("meta_json") or "{}")
        if kind == "approved":
            doc = pdf.content(req, ctx["interp"], ctx["results"], self.policy, meta.get("currency", "USD"))
            msg["Subject"] = one_line(f"NetSuite ROM estimate: {req['customer']}")
            if self.cc_owner:
                msg["Cc"] = self.cc_owner
            msg.set_content(f"Hello,\n\nYour reviewed NetSuite ROM estimate for {one_line(req['customer'])} is attached.\n\n"
                            f"{doc['headline']}\n{doc['meta'][0]}\n\n{doc['footer']}\n\nReference: {req['id']}\n\nLirik Pre-Sales")
            msg.add_attachment(self.renderer.render(doc, ctx["results"]), maintype="application", subtype="pdf",
                               filename=f"NetSuite_ROM_{file_name(req['customer'])}.pdf")
        elif kind == "returned":
            msg["Subject"] = one_line(f"More detail needed for your NetSuite ROM request: {req['customer']}")
            msg.set_content(f"Hello,\n\nOur reviewer needs a little more detail before we can release the estimate for "
                            f"{one_line(req['customer'])}:\n\n{ctx['note'].strip()}\n\n"
                            f"Please resubmit with the added detail (your answers are pre-filled):\n{self.resubmit_link(req)}\n\n"
                            f"Reference: {req['id']}\n\nLirik Pre-Sales")
        elif kind == "failed":
            msg["Subject"] = "Your NetSuite ROM request"
            msg.set_content(f"Hello,\n\n{FAILED_TEXT}\n\nReference: {req['id']}\n\nLirik Pre-Sales")
        else:
            raise ValueError(f"unknown email kind {kind}")
        return msg

    def send(self, kind, req, **ctx):
        msg = self.build(kind, req, **ctx)
        recipients = [msg["To"]] + ([msg["Cc"]] if msg["Cc"] else [])
        extra = {"ConfigurationSetName": self.config_set} if self.config_set else {}
        mid = self.ses.send_raw_email(Source=self.sender, Destinations=recipients,
                                      RawMessage={"Data": msg.as_bytes()}, **extra)["MessageId"]
        self.store.update(req["id"], actor="bot", event="email_sent", detail={"kind": kind, "message_id": mid})
        return mid


def handle_ses_event(body, store, client, channel):
    """Post a bounce or complaint in the review thread of the request it belongs to. Returns True if it was handled."""
    data = json.loads(body)
    if "Message" in data:  # SNS envelope
        data = json.loads(data["Message"])
    kind = data.get("eventType") or data.get("notificationType")
    if kind not in ("Bounce", "Complaint"):
        return False
    req = store.by_message_id((data.get("mail") or {}).get("messageId", ""))
    if not req:
        log.warning("SES %s for an unknown message", kind)
        return False
    who = intake.mask_email(intake.address_of(req))
    detail = data.get("bounce") or data.get("complaint") or {}
    what = (f"bounced ({detail.get('bounceType', '?')}/{detail.get('bounceSubType', '?')})" if kind == "Bounce"
            else "was reported as spam")
    client.chat_postMessage(channel=req["review_channel"] or channel, thread_ts=req["review_ts"],
                            text=f":warning: The email to {who} for request {req['id']} {what}. The AE may not have received it.")
    return True


def poll_events(store, sqs, queue_url, client, channel, stop, wait=20):
    while not stop.is_set():
        try:
            for m in sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=5, WaitTimeSeconds=wait).get("Messages", []):
                try:
                    handle_ses_event(m["Body"], store, client, channel)
                except Exception:
                    log.exception("Could not handle SES event %s", m.get("MessageId"))
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
        except Exception:
            log.exception("SES event poll failed")
            stop.wait(5)
