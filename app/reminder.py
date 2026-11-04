"""Nudge the review channel when a web request has waited too many business hours for a reviewer."""
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("ns_estimator.reminder")

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def business_hours_between(start, end, cfg):
    """Hours between two aware datetimes that fall inside the configured business days and hours."""
    tz = ZoneInfo(cfg["timezone"])
    opens, closes = time.fromisoformat(cfg["start"]), time.fromisoformat(cfg["end"])
    days = {DAYS.index(d) for d in cfg["days"]}
    start, end = start.astimezone(tz), end.astimezone(tz)
    total, day = timedelta(), start.date()
    while day <= end.date():
        if day.weekday() in days:
            lo = max(start, datetime.combine(day, opens, tz))
            hi = min(end, datetime.combine(day, closes, tz))
            if hi > lo:
                total += hi - lo
        day += timedelta(days=1)
    return total.total_seconds() / 3600


def due(requests, now, cfg, hours):
    return [r for r in requests if business_hours_between(r["review_at"], now, cfg) >= hours]


def remind_once(bot, now=None):
    """Post one reminder per waiting web request; `reminded_at` makes it fire once."""
    now = now or datetime.now(timezone.utc)
    cfg = bot.web["sla"]
    waiting = [r for r in bot.store.by_source_status("web", ("in_review",)) if r.get("review_at") and not r.get("reminded_at")]
    for req in due(_aware(waiting), now, cfg["business_hours"], cfg["reviewer_reminder_business_hours"]):
        try:
            bot.app.client.chat_postMessage(
                channel=req["review_channel"], thread_ts=req["review_ts"],
                text=f"Reminder: the web-form estimate for {req['customer']} (request {req['id']}) has waited "
                     f"{cfg['reviewer_reminder_business_hours']}+ business hours for review. The AE is expecting a reply within 1 business day.")
            bot.store.update(req["id"], reminded_at=now)
        except Exception:
            log.exception("Reminder for %s failed", req["id"])


def _aware(rows):  # SQLite hands back naive datetimes; they were written as UTC
    return [{**r, "review_at": r["review_at"].replace(tzinfo=timezone.utc) if r["review_at"].tzinfo is None else r["review_at"]} for r in rows]


def run(bot, stop, every=300):
    while not stop.wait(every):
        try:
            remind_once(bot)
        except Exception:
            log.exception("Reminder pass failed")
