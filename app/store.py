import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, Integer, MetaData, String, Table, Text,
                        create_engine, insert, inspect, select, text, update)

metadata = MetaData()

requests = Table(
    "estimate_requests", metadata,
    Column("id", String(16), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("requester_id", String(320), nullable=False),  # Slack user id, or "email:<address>" for web requests
    Column("requester_name", String(200)),
    Column("customer", String(300), nullable=False),
    Column("opportunity", String(500)),
    Column("requirement", Text, nullable=False),
    Column("form_json", Text),
    Column("strategic", Boolean, nullable=False, default=False),
    Column("status", String(20), nullable=False),
    Column("deal_type", String(40)),
    Column("interpretation_json", Text),
    Column("results_json", Text),
    Column("review_reasons_json", Text),
    Column("review_channel", String(32)),
    Column("review_ts", String(32)),
    Column("log_ts", String(32)),
    Column("reviewer_id", String(32)),
    Column("decision_note", Text),
    Column("workbook_path", String(1000)),
    Column("version", Integer, nullable=False, default=0),
    Column("template_sha256", String(64)),
    Column("model", String(100)),
    Column("error", Text),
    Column("source", String(16), nullable=False, default="slack", server_default="slack"),
    Column("submission_id", String(64), unique=True),
    Column("meta_json", Text),
    Column("review_at", DateTime(timezone=True)),
    Column("reminded_at", DateTime(timezone=True)),
)

# columns added after the first release; create_all never alters an existing table
ADDED_COLUMNS = {"source": "VARCHAR(16) NOT NULL DEFAULT 'slack'", "submission_id": "VARCHAR(64)", "meta_json": "TEXT",
                 "review_at": "TIMESTAMP WITH TIME ZONE", "reminded_at": "TIMESTAMP WITH TIME ZONE"}

events = Table(
    "estimate_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", String(16), nullable=False, index=True),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("actor", String(32)),
    Column("kind", String(40), nullable=False),
    Column("detail_json", Text),
)

STATUSES = ("received", "estimating", "in_review", "delivered", "returned", "failed")


def _now():
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, url):
        self.engine = create_engine(url, future=True, pool_pre_ping=True)
        metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self):
        """Idempotent upgrade of a database created before the web form: add the new columns, widen requester_id."""
        insp = inspect(self.engine)
        have = {c["name"] for c in insp.get_columns("estimate_requests")}
        with self.engine.begin() as cx:
            for name, ddl in ADDED_COLUMNS.items():
                if name not in have:
                    cx.execute(text(f"ALTER TABLE estimate_requests ADD COLUMN {name} {ddl}"))
            cx.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_estimate_requests_submission ON estimate_requests (submission_id)"))
            if self.engine.dialect.name == "postgresql":  # SQLite does not enforce VARCHAR length
                cx.execute(text("ALTER TABLE estimate_requests ALTER COLUMN requester_id TYPE VARCHAR(320)"))

    def create(self, requester_id, requester_name, customer, opportunity, requirement, strategic, template_sha256, model,
               form=None, source="slack", submission_id=None, meta=None):
        rid = uuid.uuid4().hex[:10].upper()
        now = _now()
        with self.engine.begin() as cx:
            cx.execute(insert(requests).values(
                id=rid, created_at=now, updated_at=now, requester_id=requester_id,
                requester_name=requester_name, customer=customer, opportunity=opportunity,
                requirement=requirement, form_json=json.dumps(form or {}), strategic=strategic, status="received", version=0,
                template_sha256=template_sha256, model=model, source=source, submission_id=submission_id,
                meta_json=json.dumps(meta) if meta is not None else None,
            ))
            cx.execute(insert(events).values(request_id=rid, at=now, actor=requester_id, kind="received", detail_json=None))
        return rid

    def get(self, rid):
        with self.engine.connect() as cx:
            row = cx.execute(select(requests).where(requests.c.id == rid)).mappings().first()
        return dict(row) if row else None

    def by_submission(self, submission_id):
        with self.engine.connect() as cx:
            row = cx.execute(select(requests).where(requests.c.submission_id == submission_id)).mappings().first()
        return dict(row) if row else None

    def by_source_status(self, source, statuses):
        with self.engine.connect() as cx:
            rows = cx.execute(select(requests).where(requests.c.source == source, requests.c.status.in_(statuses))).mappings().all()
        return [dict(r) for r in rows]

    def by_review_ts(self, channel, ts):
        with self.engine.connect() as cx:
            row = cx.execute(select(requests).where(requests.c.review_channel == channel, requests.c.review_ts == ts)).mappings().first()
        return dict(row) if row else None

    def update(self, rid, actor=None, event=None, detail=None, **fields):
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"Unknown status {fields['status']}")
        for key in ("interpretation_json", "results_json", "review_reasons_json"):
            if key in fields and not isinstance(fields[key], (str, type(None))):
                fields[key] = json.dumps(fields[key], default=str)
        now = _now()
        with self.engine.begin() as cx:
            cx.execute(update(requests).where(requests.c.id == rid).values(updated_at=now, **fields))
            if event:
                cx.execute(insert(events).values(
                    request_id=rid, at=now, actor=actor, kind=event,
                    detail_json=json.dumps(detail, default=str) if detail is not None else None,
                ))

    def transition(self, rid, from_statuses, actor, event, detail=None, expect_version=None, **fields):
        now = _now()
        conditions = [requests.c.id == rid, requests.c.status.in_(from_statuses)]
        if expect_version is not None:
            conditions.append(requests.c.version == expect_version)
        with self.engine.begin() as cx:
            res = cx.execute(update(requests).where(*conditions).values(updated_at=now, **fields))
            if res.rowcount != 1:
                return False
            cx.execute(insert(events).values(
                request_id=rid, at=now, actor=actor, kind=event,
                detail_json=json.dumps(detail, default=str) if detail is not None else None,
            ))
        return True
