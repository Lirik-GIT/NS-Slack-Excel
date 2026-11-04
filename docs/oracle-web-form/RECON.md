# Phase 0 – Recon: Oracle AE web form

Read-only recon of the repo against `MASTER_PROMPT.md` and `CLAUDE.md`. No application code was changed.

## 1. Repo map (verified by reading)
| Area | Facts |
|---|---|
| Entry point | `app/slack_app.py:358` `main()` -> `EstimatorBot(load_settings()).start()` (Socket Mode, outbound only). Docker `CMD python -m app.slack_app`. |
| Bot | `app/slack_app.py` `EstimatorBot`: owns Store, Estimator, ThreadPoolExecutor (`WORKER_THREADS`), Slack App. Handlers: `/ns-estimate`, `submit_request`, `approve`, `open_return`/`submit_return`, `open_pricing`/`submit_pricing`, `on_message` (reviewer .xlsx upload in thread). |
| Pipeline | `submit_request` -> `store.create(...)` -> `pool.submit(_run, client, rid)` -> `estimator.estimate()` -> `_route()` -> review card (`review_reasons`; `review_all=true` in `config/policy.json`, so every estimate goes to review). Approve -> `_deliver()`. |
| Estimating | `app/estimator.py` (Interpreter reads twice, `resolve`, `write_inputs`, LibreOffice `recalc`, `read_results`). Untouched by this feature. |
| Request model | `app/store.py` SQLAlchemy table `estimate_requests` (SQLite at `DATA_DIR/estimator.db`; `.env.example` also shows a Postgres `DATABASE_URL`). `requester_id String(32) NOT NULL`. `transition()` is the atomic state guard. Events table `estimate_events`. |
| Slack requester today | A Slack user id (`U...`). Every user-facing message is `_dm(client, req["requester_id"], ...)` -> `conversations_open(users=...)`. |
| Pricing | Workbook cells -> `read_results` -> `Results` dataclass (price, price_cad, rom_low/high, blended_rate, rates, total_hours, weeks_*, milestones). |
| Requester view today | `slack_ui.ae_blocks(req, interp, results, policy, title)` (`app/slack_ui.py:245`): ROM range, weeks, milestone split, scope summary, included, assumptions, not-priced, questions, disclaimer. No price, rates or hours. An existing test asserts `16,896`, `/hr` and `rate` are absent. This is the template for the PDF content. |
| Reviewer view | `slack_ui.review_blocks` (exact price, blended rate, hours, hours by line, reasons). Never to Oracle. |
| Config | `app/config.py` frozen `Settings` from env vars only (no YAML, no SSM). `CONFIG_DIR` points at policy/guide/bank. |
| Container | `Dockerfile`: python:3.12-slim + `libreoffice-calc-nogui` (Calc only), non-root uid 10001, `/srv/data` volume. |
| Tests | pytest, plain functions, hand-rolled fakes (`FakeClaude` in `tests/conftest.py`, `FakeSlack` in `tests/test_bot_flow.py`), real LibreOffice for end-to-end. No mock library, no moto. |
| IaC / CI / lint | **None found**: no `.tf`, `template.yaml`, `samconfig`, `cdk.json`, compose, `.github/`, ruff/mypy/pytest config. |
| Git | Branch `feature/oracle-ae-web-form` (no commits beyond `20b3ec0`). `CLAUDE.md`, `MASTER_PROMPT.md`, `README.md` were untracked. |

## 2. IaC decision
No IaC in use -> **default to AWS SAM** for `infra/oracle-web-form/` (REST API + WAF + Lambda + SQS + DLQ + SES + alarms). Validate with `sam validate` / `cfn-lint` only. Never deploy.

## 3. Insertion points
| Change | Where |
|---|---|
| `app/intake.py` (new) | Start the poller thread from `EstimatorBot.start()` next to `SocketModeHandler`. Create the request with `store.create(...)` + `pool.submit(self._run, client, rid)` (same path as `submit_request`). |
| Source and requester | Add `source` column (default `slack`) and `submission_id` (unique, for idempotency) to `estimate_requests`; widen `requester_id` (`String(32)` is too short for `email:<address>`). |
| Email instead of DM | All requester messages go through `_dm`. Route by `source`: `_run` failure (`slack_app.py:224`), routing failure (`:246`), `_route` "with presales" (`:260`), direct delivery (`_deliver`, `:352`), return (`:159`). A small `_notify(client, req, ...)` wrapper that branches on `source` keeps the change in one place. |
| Generic failure text | `slack_app.py:225` sends `Reason: {str(exc)[:300]}` to the requester. Fine for internal Slack users, **a leak for external AEs**. The web branch sends the generic "reviewing manually" email; the detail goes only to the review channel (`:228`, already there). |
| Review card | `slack_ui.review_blocks:272` ("Requested by <@id>") and `log_blocks:331`: show `Web form: name@oracle.com` (masked in logs). `return_modal`: add the external-recipient warning. |
| Return | `submit_return` (`:145`): email the note + signed resubmit link instead of "Run /ns-estimate again". |
| Excluded localization | `review_reasons` (`app/review.py`) + `policy.standard_exclusions`: add a reason when `customer_country` is not US/CA. Country must be carried in `form_json`. |
| Reviewer reminder | No scheduler exists. New timer thread in the bot; the business-hours calculation is new code. |
| PDF | `app/pdf.py` (new); input = `interp` + `results` + `policy["ae_message"]`, the same inputs as `ae_blocks`. |
| Attachments | Reviewer-only: download from S3 in intake, `files_upload_v2` into the review thread (same call as `_route:257`). The AI never reads them. |
| Secrets | `load_settings` reads env only. Add SSM loading (boto3) for Slack/Anthropic keys and the HMAC key. |

## 4. Conflicts between MASTER_PROMPT.md and the repo (assumptions, change on request)
1. **Path**: the prompt says `docs/oracle-web-form/RECON.md`, but `MASTER_PROMPT.md` itself sits at repo root and `docs/` did not exist. Assumption: create `docs/oracle-web-form/` and leave the prompt files where they are.
2. **Button names**: the prompt says Approve/Return; the repo has "Send to AE", "Adjust pricing", "Ask AE for detail". Assumption: keep existing labels (CLAUDE.md: reviewer buttons unchanged).
3. **PDF via LibreOffice**: the image has Calc only. HTML/DOCX -> PDF needs Writer (`libreoffice-writer-nogui`, about +100 MB image). Alternative: render a print-formatted sheet via Calc (no new package, plainer layout). Assumption: add `libreoffice-writer-nogui` to the Dockerfile; "no new dependency" is read as no new Python dependency.
4. **New Python dependencies are unavoidable**: `boto3` (SQS, SES, S3, SSM) and PyYAML for `web_form.yaml`. Dev: `moto`, `ruff`. Assumption: add them.
5. **"One S3 bucket" is not enough** for the Lambda side: the daily limit of 10 per AE, single-use tokens, duplicate detection and the 24 h pending store need state. Assumption: one DynamoDB on-demand table (with TTL) in addition to the bucket; the AWS admin permission list grows by `dynamodb`.
6. **CAD**: `Results` holds one `rom_low/high` (USD) and `price_cad` (exact). A CAD range for AEs needs the workbook's FX ratio. Assumption: CAD range = USD range x (`price_cad`/`price`), rounded the same way; flagged for confirmation.
7. **Retries**: "3 automatic retries" in the app plus "DLQ after 3 receives" can double-count. Assumption: the app retries estimating 3 times inside `_run`; SQS `maxReceiveCount=3` covers crashes and restarts only.
8. **Message deletion**: delete the SQS message only after the request row exists (dedupe by `submission_id`); a startup sweep re-runs requests stuck in `received`/`estimating` after a crash.
9. **Reminder timezone**: "4 business hours" needs a calendar. Assumption: Mon-Fri 09:00-17:00 `America/New_York`, configurable.

## 5. Risks
- **Leak risk (CLAUDE.md rule 2)**: the existing raw-error DM (above). Reusing `ae_blocks` fields is safe today, but a PDF adds a new surface -> canary-value leak test in CI (Phase 2).
- **Schema change**: `metadata.create_all` never alters existing tables; the live EC2 SQLite DB needs an `ALTER TABLE` migration for the new columns (no Alembic in the repo). Needs a runbook step and a rollback.
- **Baseline not verified green**: `DEV_ISSUES.md` item 3 says 5 staffing tests fail (`app/staffing.py` and `tests/test_staffing.py` are stale leftovers). Tests were not run in Phase 0. "Tests green before each commit" needs those deleted or fixed first (recommendation: delete, per DEV_ISSUES).
- **Definition of done asks for linter/type-check**: none is configured. Assumption: add a minimal `ruff` config, no mypy.
- **Prompt injection**: requirement text already flows to Claude as data with quote validation (`interpreter.validate`); the web path reuses it. The new risk is free-text fields in email headers (header injection in SES raw email) -> validate and strip CR/LF.
- **Open DEV_ISSUES that matter here**: #5 (thin requirements not flagged; matters more for anonymous web input) and #6 (`DATA_DIR` mkdir).
- **Outstanding from earlier work**: DLM snapshot permission is pending; keys are being rotated by the owner; `Oracle_Plan.pdf` is committed in the repo.
- **SES sandbox** until production access (about 1 day): only verified addresses can be emailed before then.

## 6. Task list
- **P1**: `app/intake.py`; `store.py` columns (`source`, `submission_id` unique, wider `requester_id`) + migration SQL; `_notify` email branch in `slack_app.py`; card/log/return-modal text in `slack_ui.py`; excluded-localization reason; reminder timer; SSM settings; tests with fakes (no regression in `tests/test_bot_flow.py`).
- **P2**: `app/pdf.py`; `app/email.py` (SES raw + bounce handling); Dockerfile Writer; PDF leak test with canaries.
- **P3**: `infra/oracle-web-form/template.yaml` (SAM), `lambda/form/` (about 150 lines); `sam validate`, `cfn-lint`; no deploy.
- **P4**: moto end-to-end, duplicate, injection, bad attachments, forged/expired tokens, rate limit, bounce, AI-failure path; coverage.
- **P5**: `DEPLOY_RUNBOOK.md`, `PILOT_CHECKLIST.md`, `COSTS.md`, open-decisions table.
