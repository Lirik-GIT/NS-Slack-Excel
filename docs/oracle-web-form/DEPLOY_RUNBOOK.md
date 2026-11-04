# Deploy runbook – Oracle AE web form

Nothing here has been run. Every command below is for you or the AWS admin to run. Region: **us-east-1** (same as the estimator).
Order matters: SES approval takes about a day, so start it first.

## 0. Before you start
- [ ] Phase 4 branch `feature/oracle-ae-web-form` reviewed and merged (or deployed from the branch).
- [ ] Exposed Slack and Anthropic keys rotated (you said you would).
- [ ] You know who the pilot AEs are (2 addresses at oracle.com) and who the Lirik presales owner is (cc on approved emails).
- [ ] Decisions in `OPEN DECISIONS` (bottom of this file) answered, at least D1 to D4.

## 1. Permissions the AWS admin needs (to deploy the stack)
Give the admin (or a deploy role) these, scoped to us-east-1 and to resources named `ns-estimator*` where AWS allows:

| Service | Why |
|---|---|
| CloudFormation | create the stack (with `CAPABILITY_IAM`) and the SAM transform |
| Lambda, API Gateway | form function and REST API |
| WAFv2 | web ACL, CAPTCHA, association with the API stage |
| S3 | one bucket for attachments (SAM also needs a bucket for the Lambda zip) |
| DynamoDB | one on-demand table (counters, pending submissions) |
| SQS, SNS | submissions queue, DLQ, SES events queue and topic |
| SES | domain identity, DKIM, configuration set, event destination, production-access request |
| CloudWatch (alarms, logs) | 4 alarms, 90-day log group |
| IAM | create the Lambda role and the `EstimatorHostPolicy`, attach it to the EC2 role (`iam:AttachRolePolicy` on that role only) |
| SSM Parameter Store | create 2 parameters (step 4) |
| DLM or AWS Backup | daily EBS snapshot of the estimator host (the DLM permission is still pending from earlier work) |

## 2. SES: verify lirik.io and ask for production access (do this first)
1. Deploy the stack (step 5) or create the identity by hand. The stack output `DkimTokens` lists three names.
2. Ask whoever manages DNS for lirik.io to add:

| Type | Name | Value |
|---|---|---|
| CNAME ×3 | `<token>._domainkey.lirik.io` | `<token>.dkim.amazonses.com` (one per DkimTokens entry) |
| TXT | `lirik.io` | SPF: add `include:amazonses.com` to the existing SPF record (there must be only one SPF record) |
| TXT | `_dmarc.lirik.io` | start with `v=DMARC1; p=none; rua=mailto:<a Lirik mailbox>`; move to `p=quarantine` after the pilot week |

3. Optional but advised: set a custom MAIL FROM domain (`mail.lirik.io`, MX + SPF TXT) so SPF aligns for DMARC.
4. In the SES console choose **Request production access**: use case "transactional, an estimate PDF sent to a requester who asked for it", about 20 emails a day at most, bounces and complaints handled through the configuration set (stack creates it). Takes about 1 day. Until it is granted SES only sends to verified addresses, so the pilot AEs' addresses must be verified by hand for any pre-approval testing.
5. Check the DKIM status shows **Successful** before the first real send.

## 3. Estimator host: instance role
Pass the instance role name as `Ec2RoleName` in step 5 and the stack attaches `EstimatorHostPolicy`. It only allows: receive and delete on the two queues, `ses:SendRawEmail` from the one sender address, read of `attachments/*` in the bucket, and read of `/ns-estimator/*` parameters. No inbound port is opened; the bot only calls out.
If you prefer to attach it by hand: `EstimatorHostPolicyArn` is a stack output.

## 4. Parameters (SSM) and secrets
Create these with the AWS CLI or console. Never put values in git, chat or `.env`.

```bash
# link-signing key (one parameter, used by BOTH the form Lambda and the bot)
aws ssm put-parameter --name /ns-estimator/WEB_HMAC_KEY --type SecureString --value "$(openssl rand -base64 48)"

# the pilot AEs allowed to use the form (comma or newline separated)
aws ssm put-parameter --name /ns-estimator/web/approved_ae_emails --type String --value "updesh.singh@lirik.io,komaldeep@lirik.io"
```
- The last path segment of the key **must stay `WEB_HMAC_KEY`**: the bot starts with `SSM_PREFIX=/ns-estimator`, which loads direct children of that path as environment variables.
- Do **not** create a second copy of the key under `/ns-estimator/web/`. A different value means resubmit links (signed by the bot, checked by the Lambda) never verify. (Found and fixed in Phase 5: the stack default was a different path.)
- **Rotation:** put a new value with `--overwrite`, then restart the container. Links already emailed stop working (confirm links last 24 h, resubmit links 14 days).
- **API keys** (Slack, Anthropic): rotate in their consoles, update `.env` on the host, and restart the container. Keep `.env` out of S3 and out of git.
- **To change the approved list** later: `put-parameter --overwrite`. The Lambda re-reads parameters every 5 minutes; no deploy. The same delay applies to the key and to the rollback trick below.

## 5. Deploy the stack
Needs the SAM CLI (not installed on the dev machine, so `sam validate` was never run; `cfn-lint` passes).
```bash
sam validate --template infra/oracle-web-form/template.yaml --lint
sam build  --template infra/oracle-web-form/template.yaml
sam deploy --guided --stack-name ns-estimator-web-form \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides AlertTopicArn=<existing ns-estimator-alerts topic ARN> Ec2RoleName=<instance role name> \
                        FormBaseUrl=<custom form URL, see step 8, or leave empty first>
```
For the Lirik-staff pilot add `EmailDomains=oracle.com,lirik.io` (the form accepts only `oracle.com` by default). Keep the defaults for the rest (`SenderAddress=komaldeep@lirik.io`, `SenderDomain=lirik.io`, limits). Save the outputs: `FormUrl`, `SubmissionsQueueUrl`, `SesEventsQueueUrl`, `AttachmentsBucketName`, `DkimTokens`.

## 6. Manual step: WAF CAPTCHA keys
CloudFormation cannot create these.
1. WAF console → Web ACLs → `ns-estimator-form` → **Application integration** → CAPTCHA integration.
2. Create an API key for the form's domain. Copy the **JS API script URL** and the **API key**.
3. Redeploy with `CaptchaScriptUrl=<url> CaptchaApiKey=<key>` added to `--parameter-overrides`.
4. Test in a browser that the puzzle appears before submit. (This was never tested; it is the first thing to check.)

## 7. Estimator host: config, SQLite migration, restart
1. **Back up first**: snapshot the EBS volume, and `docker cp` or copy `/srv/data/estimator.db` somewhere safe.
2. Create `web_form.yaml` on the host from `config/web_form.example.yaml`; set `pilot.allowed_domains: [oracle.com, lirik.io]` to match the stack (real sender, reviewers, `form_url` = the public form URL, `configuration_set` = the stack's set name, owner email for cc). It is gitignored. It does not hold the AE list (that is in SSM) or any secret.
3. Add to the host's `.env` (no secrets, only names):
   ```
   SSM_PREFIX=/ns-estimator
   WEB_QUEUE_URL=<SubmissionsQueueUrl>
   WEB_EVENTS_QUEUE_URL=<SesEventsQueueUrl>
   WEB_ATTACHMENTS_BUCKET=<AttachmentsBucketName>
   ```
4. Build and start the new image, mounting the config file read-only (same command as DEPLOYMENT_PLAN.md 2.6, plus one `-v`):
   ```bash
   sudo docker build -t ns-estimator:v3 .
   sudo docker stop ns-estimator && sudo docker rm ns-estimator
   sudo docker run -d --name ns-estimator --env-file .env \
     -v ns-estimator-data:/srv/data \
     -v /home/ec2-user/web_form.yaml:/srv/config/web_form.yaml:ro \
     --restart unless-stopped ns-estimator:v3
   ```
5. **Migration is automatic.** On start `Store._migrate()` adds the new columns (`source`, `submission_id`, `meta_json`, `review_at`, `reminded_at`) to the existing table. It only adds; it never drops or rewrites rows. The Postgres branch has never been run: if `DATABASE_URL` points at Postgres, test on a copy first.
6. Check `docker logs ns-estimator`: Slack connected, then the web-intake, ses-events and web-reminder threads started, no tracebacks.

## 8. Public address (advised)
Emails contain links. Put the form behind a name Oracle AEs will trust (for example `estimate.lirik.io`): API Gateway custom domain + ACM certificate + a CNAME, then redeploy with `FormBaseUrl=https://estimate.lirik.io`. Without it, links use the long `execute-api.amazonaws.com` address, which looks like phishing to some mail filters.

## 9. Smoke test (before telling any AE)
1. Open the form, submit a test request with your own Lirik address added to the approved list. Confirm via the email link.
2. Slack review channel: a card "Web form: …" appears; attachments are in the thread.
3. Approve. The PDF arrives; open it and check it has only the range, duration, milestones and scope, no rates or hours.
4. Return a second test request; check the email and the pre-filled resubmit link.
5. Remove your test address from the approved list afterwards.

## 10. Rollback
| Problem | Action |
|---|---|
| Bot misbehaves | run the previous image (`ns-estimator:v2`) with the old `docker run` command. The new database columns are harmless to old code. If the database itself is damaged, restore the copy or snapshot from step 7.1. |
| Form must stop now | WAF console: set the web ACL default action to Block, or delete the `Submit` API method. Quicker: `aws ssm put-parameter --name /ns-estimator/web/approved_ae_emails --overwrite --value "none@invalid"`; everyone then gets the generic success page and nothing is queued. |
| Queued requests unwanted | purge the submissions queue in the SQS console (this drops AEs' pending requests; tell them). |
| Remove everything | delete the CloudFormation stack. The attachments bucket is set to **Retain**, so empty and delete it by hand if wanted. Remove the three DKIM CNAMEs and the SPF include at your DNS host. |

## 11. Things to watch after go-live
Alarms go to `ns-estimator-alerts`: queue age over 30 min (bot down), anything in the DLQ, Lambda errors, SES bounce rate over 5%. A bounce or complaint also posts in the request's Slack review thread.

---

## OPEN DECISIONS

| # | Decision | Suggested answer | Owner |
|---|---|---|---|
| D1 | **Decided:** pilot AEs are `updesh.singh@lirik.io` and `komaldeep@lirik.io`. **Decided:** cc owner is `komaldeep@lirik.io` (set `reviewers.lirik_presales_owner_email` in the host's `web_form.yaml`). | none | Sales Director |
| D2 | **Decided:** sender is `komaldeep@lirik.io`, form name is "NS ROM Form". Still open: the public web address (for example `estimate.lirik.io`), see step 8 | Custom domain | CTO |
| D3 | **Decided:** AI spend about $5 a day. The Anthropic console only has a monthly limit, so set it to $150 and check the daily figure (see COSTS.md) | Monthly limit $150 | Both |
| D4 | Who backs up the primary reviewer, and does the 1 business day promise hold on holidays and weekends? | Named backup; the 4-hour reminder already skips weekends | Sales Director |
| D5 | **Decided:** privacy notice approved. Keep the text in `lambda/form/page.html` in step with what was approved; re-approval is needed if it changes | none | Both |
| D6 | **Decided:** keep the current USD and CAD handling (CAD range = USD range scaled by the workbook's own ratio, rounded to 1,000) | none | Sales Director |
| D7 | Over-limit or unapproved AEs see the normal success message and get no email (no probing of the approved list) | Accept for the pilot | Both |
| D8 | **Decided:** keep the 5 stale staffing tests for now (deselected) | none | CTO |
| D9 | **Decided:** commit everything and push the branch. Still open: pull request and merge | Open a pull request | CTO |
| D10 | DMARC policy after the pilot (`none` → `quarantine`) | Move after one clean week | CTO |
