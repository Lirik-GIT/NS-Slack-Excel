# MASTER PROMPT – paste into Claude Code (VS Code), plan mode first

You are a senior AWS/Python engineer extending the existing **NS Estimator** with a secure web form for external Oracle NetSuite AEs. Read `CLAUDE.md` first; its rules override anything below if they conflict.

## Goal
An Oracle AE fills one web form, confirms their email, and receives a **reviewed ROM estimate as a PDF by email in about 1 business day**. No Slack account, no login. A Lirik presales reviewer still approves every estimate in the existing Slack channel. Rates, hours, line items and pricing logic never leave Lirik.

## Operating mode
- Work in **6 phases**. At the end of each phase: run tests, show a diff summary and a checklist of what was verified, then **STOP and wait for me to say `continue`**.
- Phase 0 is read-only. Ask me at most one consolidated batch of questions, only if blocked. Otherwise state your assumption and proceed.
- If something in this prompt conflicts with the repo, tell me and propose a fix; don't silently choose.

## Target architecture (from the approved plan)
```
AE browser -> API Gateway (REST) + AWS WAF (rate limit, CAPTCHA)
          -> Lambda "form" (serves page, validates, uploads attachments, sends confirmation email via SES)
          -> confirmation link (signed, valid 24h) -> Lambda marks submission confirmed
          -> SQS queue (+ dead-letter queue)   <- nothing lost if EC2 is down; retain 14 days
EC2 (existing estimator, NO inbound connections) polls SQS
          -> app/intake.py creates request (source "web", requester "email:<address>") -> existing pipeline
          -> Slack review card (existing) shows "Web form: name@oracle.com"
          -> reviewer Approve / Return (existing buttons)
Approve -> app/pdf.py builds PDF (LibreOffice already in container) -> SES email from estimates@lirik.io, Lirik presales owner in cc
Return  -> email with reviewer note + link to resubmit with form pre-filled
Failure -> generic "we're reviewing it manually" email to AE; details to Lirik Slack only
Monitoring: CloudWatch alarms -> existing SNS topic
```
Use **API Gateway REST API** (not HTTP API) because AWS WAF attaches only to REST. Region **us-east-1**. All storage encrypted at rest and in transit.

## Form fields
customer, opportunity ID, deal type, number of subsidiaries, areas in scope, add-on modules, requirement (free text), customer country, currency (USD|CAD), optional attachments (PDF/Word/Excel, max 3 files, 10 MB each, type + size validated server-side, magic-byte check), checkbox "No personal data included". Show a privacy notice. Prefill support for resubmission via a signed link.

## Emails to the AE (SES, from estimates@lirik.io; SPF/DKIM/DMARC)
| When | Content |
|---|---|
| On submit | Confirmation link valid 24h |
| After confirm | "Received", reference number, expected reply time |
| Approved | ROM estimate PDF, Lirik presales owner in cc |
| Returned | Reviewer note + resubmit link (form pre-filled) |
| Could not process | Short "we're reviewing it manually" message; no details |

## What Oracle may see vs. never
Allowed in PDF/emails: ROM price range + currency; duration, phases, payment milestones; scope as understood/what's included; assumptions, exclusions, open questions.
**Never:** exact price, blended rate, hourly rates, hours, line items, estimator workbook, reasons for review, reviewer notes (except the "Return" note intended for the AE), error details.

## Reliability and security requirements
- Duplicate submission detection by submission ID; process once (idempotent consumer).
- Estimator down/restarting: requests wait in SQS up to 14 days; alarm if any message waits > 30 minutes.
- AI error/timeout: 3 automatic retries, then polite email to AE + Slack alert; after 3 failed attempts a request goes to the DLQ for manual handling, with alarm.
- Email bounces reported back and posted in the review thread.
- Abuse: WAF + CAPTCHA, email confirmation, approved-AE allowlist (approved `@oracle.com` addresses only; no Oracle SSO), max 10 requests per AE per day, API throttling.
- Reviewer slow to respond: reminder in review channel after 4 business hours.
- Retention: estimates 12 months, attachments 90 days (S3 lifecycle), unconfirmed submissions 24 hours.
- Server accepts no inbound connections; it only polls SQS. API keys in SSM Parameter Store, rotated as part of the build.
- Daily disk snapshots via DLM or AWS Backup (needs one AWS permission, flag as pending).
- Every AI answer must quote the requirement; a person approves each estimate.

## Phases

### Phase 0 – Recon (read-only)
1. Map the repo: entry points, `app/slack_app.py`, `app/slack_ui.py`, pipeline, request model, config loading, how Slack requesters are represented, how the workbook is priced, how the PDF-like "requester view" is produced today, tests, Dockerfile/compose, any IaC.
2. Detect the IaC tool in use. If none, **default to AWS SAM** for the new serverless pieces and say so.
3. Write `docs/oracle-web-form/RECON.md`: findings, the exact insertion points for the changes below, risks, assumptions, and a task list.
4. Create `config/web_form.example.yaml` with placeholders (no real data): approved AE emails/regions, reviewer + backup reviewer, SLA 1 business day, reminder 4 business hours, daily limit 10, country allowlist US/CA, currencies USD/CAD, retention values.
Stop.

### Phase 1 – Estimator-side changes
- New `app/intake.py`: poll SQS (long polling), validate message schema, deduplicate by submission ID, create a request with `source="web"`, requester `email:<address>`, then run the **existing** pipeline. Graceful shutdown, visibility timeout, DLQ after 3 receives.
- `app/slack_app.py`: delivery, return and failure messages go **by email** when the request came from the web; failure text for external users is generic.
- `app/slack_ui.py`: review card shows "Web form: name@oracle.com"; the Return dialog warns that the note goes to an external Oracle AE. Attachments passed to reviewer only.
- Non-US/CA country -> request flagged "excluded localization" for the reviewer.
- 4-business-hour reviewer reminder.
- Unit tests with fakes; do not break existing tests.
Stop.

### Phase 2 – PDF and email delivery
- New `app/pdf.py`: build the PDF from the **same data as the requester view**, rendered via LibreOffice (no new dependency). Contents limited to the "allowed" list above.
- SES sender module (raw email with PDF attachment, cc Lirik owner, bounce/complaint handling posted to the review thread).
- **PDF leak test**: generate a sample PDF in CI and fail if any rate, hour count, line item, blended rate or workbook cell label appears. Use canary values injected into the fixture so the test is meaningful.
Stop.

### Phase 3 – Serverless front door
Under `infra/oracle-web-form/` (IaC) and `lambda/form/` (about 150 lines of Python):
- Form page + submit endpoint; server-side validation of every field; attachment upload to a private encrypted S3 bucket with 90-day expiry; per-IP and per-AE throttling; generic error responses.
- Confirmation-link flow with signed, 24h tokens (HMAC key from SSM); only confirmed submissions enter SQS.
- Resubmit link with prefill; WAF (rate limit + CAPTCHA); SQS + DLQ; SES identity/DKIM config; CloudWatch alarms to the existing SNS topic (queue age > 30 min, DLQ depth, bounces, Lambda errors); log masking.
- Least-privilege IAM for Lambda and for the EC2 role additions: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `ses:SendRawEmail`, `s3:GetObject` (attachments bucket only), `ssm:GetParameter`.
- Validate templates (`cfn-lint` / `sam validate`); **do not deploy**.
Stop.

### Phase 4 – Tests
- End-to-end queue test (moto or LocalStack): form -> confirm -> SQS -> intake -> pipeline (stubbed AI) -> approve -> PDF email.
- Duplicate-submit test; prompt-injection test (hostile requirement text cannot change outputs or leak internals); bad-attachment tests; expired/forged token tests; rate-limit test; email-bounce path; AI failure -> 3 retries -> manual email.
- Coverage report for new modules.
Stop.

### Phase 5 – Runbook and handover
Write `docs/oracle-web-form/`:
- `DEPLOY_RUNBOOK.md`: exact ordered steps for me to run (permissions list for the AWS admin: SQS, SES, Lambda, API Gateway, WAF, one S3 bucket, DLM/Backup; SES domain verification DNS records for lirik.io; SES production-access request, about 1 day; instance role additions; parameter creation and API-key rotation; rollback).
- `PILOT_CHECKLIST.md`: Oracle mail-delivery test with one AE before launch; trial week with two AEs; go/no-go criteria on wider rollout.
- `COSTS.md`: expected monthly adds (WAF about $8-10, email/form/queue/storage under $5, AI per estimate with a monthly spend cap).
- Open decisions table (owner: Sales Director/CTO/both) and a final diff summary.
Stop.

## Start now
Begin Phase 0. Do not write code until RECON.md is approved.
