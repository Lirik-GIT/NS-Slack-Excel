# NS Estimator – Deployment Plan

> Next steps to deploy the NS Estimator Slack app and bring it into working condition.
> Status at review (22 Oct 2026): dependencies install cleanly and **all 33 tests pass** (`33 passed in 55.8s`), including the end-to-end LibreOffice recalculation.

## Contents

- [Readiness summary](#readiness-summary)
- [Phase 0 – Fix before you deploy](#phase-0--fix-before-you-deploy)
- [Phase 1 – Slack setup](#phase-1--slack-setup)
- [Phase 2 – AWS setup](#phase-2--aws-setup)
- [Phase 3 – Acceptance testing in Slack](#phase-3--acceptance-testing-in-slack)
- [Phase 4 – Go live and run it](#phase-4--go-live-and-run-it)
- [Order and owners](#order-and-owners)

---

## Readiness summary

The app's design already deals with the two main feasibility risks:

- **Claude never supplies hours.** It answers fixed scope questions, and each answer must quote the requirement. The workbook rules then turn the answers into hours, and Claude reads each requirement twice so the reads can be compared.
- **LibreOffice recalculates the formulas** before any totals are read.

What's left is configuration, one manifest fix, and deployment.

---

## Phase 0 – Fix before you deploy

- [ ] **Fix the Slack manifest (this is a bug).** Reviewers edit an estimate by uploading the revised `.xlsx` in the review card's thread. In a DM with the bot, Slack blocks that unless the app's Messages tab is turned on. Add this to `slack_manifest.yml` under `features:`:

  ```yaml
    app_home:
      home_tab_enabled: false
      messages_tab_enabled: true
      messages_tab_read_only_enabled: false
  ```

- [ ] **Decide where reviews go.** If `REVIEW_CHANNEL_ID` is Manish's user ID, every card goes to his DM only, so no one can cover for him.
  - **Recommended:** create a private channel such as `#ns-estimate-review` with Manish and a backup, invite the bot, and use that channel's ID (it starts with `C`).
  - Put both reviewers' user IDs in `REVIEWER_USER_IDS`, separated by commas.
  - The manifest already has the `groups:history` and `message.groups` permissions this needs.
- [ ] **Restrict who can request estimates.** `ALLOWED_REQUESTER_USER_IDS` is empty, which lets anyone in the workspace run `/ns-estimate` and spend API credit. Fill in the AE IDs, or leave it empty only on purpose.
- [ ] **Check the Claude model ID.** Make sure `ANTHROPIC_MODEL` (currently `claude-sonnet-5-5`) is enabled for your Anthropic API organisation. Each request makes 2–4 Claude calls with up to 12k output tokens each, so set a monthly spend limit in the Anthropic console.
- [ ] **Check data terms.** AE requirements can contain client details. Confirm that sending them to the Anthropic API is acceptable under Lirik's agreements.

---

## Phase 1 – Slack setup

*About 30 minutes; may need a workspace admin.*

- [ ] Go to <https://api.slack.com/apps>, choose **Create New App › From a manifest**, and paste the corrected `slack_manifest.yml`.
- [ ] Under **Basic Information › App-Level Tokens**, generate a token with the `connections:write` scope. That's the `xapp-…` token (`SLACK_APP_TOKEN`).
- [ ] **Install to Workspace.** If your workspace restricts apps, an admin has to approve it. Then copy the Bot User OAuth Token (`xoxb-…`, `SLACK_BOT_TOKEN`).
- [ ] Create two channels:
  - the log channel, for example `#ns-estimate-log`
  - the review channel, for example `#ns-estimate-review`
- [ ] Run `/invite @NS Estimator` in **both** channels.
- [ ] Copy both channel IDs (channel name › About › at the bottom).
- [ ] Copy the member IDs for Manish, the backup reviewer and the AEs (profile › ⋮ › Copy member ID).

---

## Phase 2 – AWS setup

### 2.1 Instance

- [ ] **AMI:** Amazon Linux 2023
- [ ] **Type:** `t3.small` (2 GB memory)
- [ ] **Storage:** 20 GB gp3, **encryption on**
- [ ] **Security group:** no inbound rules
- [ ] **Network:** a public subnet with a public IP, so it can reach Slack and Anthropic. Don't add a NAT gateway; it costs about $32 a month.

### 2.2 IAM role

- [ ] `AmazonSSMManagedInstanceCore`
- [ ] `s3:GetObject` on `arn:aws:s3:::<bucket>/ns-estimator/*` only
- [ ] `CloudWatchAgentServerPolicy`, or just `logs:CreateLogGroup`, `logs:CreateLogStream` and `logs:PutLogEvents`

### 2.3 Upload the package

- [ ] Zip the `ns-estimator` folder **without** `tests/` or any `.env`.
- [ ] Upload it to `s3://<bucket>/ns-estimator/ns-estimator.zip`.

### 2.4 Connect and install

Connect with **EC2 › Instance › Connect › Session Manager**, then run:

```bash
sudo dnf install -y docker unzip
sudo systemctl enable --now docker

cd ~
aws s3 cp s3://<bucket>/ns-estimator/ns-estimator.zip .
unzip ns-estimator.zip && cd ns-estimator
```

### 2.5 Create `.env`

```bash
cp .env.example .env && nano .env
```

| Variable | Value |
|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-…` |
| `SLACK_APP_TOKEN` | `xapp-…` |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `ANTHROPIC_MODEL` | Model ID confirmed in Phase 0 |
| `REVIEW_CHANNEL_ID` | Review channel `C…` ID |
| `REVIEWER_USER_IDS` | Manish's and the backup's member IDs, comma-separated |
| `LOG_CHANNEL_ID` | Log channel ID |
| `ALLOWED_REQUESTER_USER_IDS` | AE member IDs, comma-separated |
| `DATABASE_URL` | **Delete this line.** The app then uses SQLite at `/srv/data/estimator.db` |

Lock the file:

```bash
chmod 600 .env
```

> Optional hardening later: move these secrets to SSM Parameter Store.

### 2.6 Build and run

Tag every build with a version so you can roll back. The image includes LibreOffice, so the first build takes about 5–8 minutes.

```bash
sudo docker build -t ns-estimator:v1 .

sudo docker run -d --name ns-estimator --env-file .env \
  -v ns-estimator-data:/srv/data --restart unless-stopped \
  --log-driver awslogs \
  --log-opt awslogs-group=/ns-estimator \
  --log-opt awslogs-create-group=true \
  ns-estimator:v1
```

- [ ] Check the logs in CloudWatch (log group `/ns-estimator`). The `docker logs` command doesn't work with the awslogs driver. You should see the Socket Mode connection and no errors.

### 2.7 Alarms

- [ ] An EC2 status-check alarm with the **Recover** action.
- [ ] A CloudWatch metric filter on `ERROR` in `/ns-estimator` that emails presales.

### 2.8 Backups

- [ ] Create a daily Data Lifecycle Manager snapshot policy for the volume and keep 14 snapshots. The volume holds the workbooks and the SQLite request log.

---

## Phase 3 – Acceptance testing in Slack

Run these five cases before you tell the AEs about it:

| # | Test | Expected result | Pass |
|---|---|---|---|
| 1 | AE runs `/ns-estimate` with a clear 5–6 sentence requirement | DM saying "Working on…", then a review card plus a v1 workbook in the review channel, and a log entry | ☐ |
| 2 | Manish presses **Send to AE** | The AE gets the ROM with the disclaimer, and the card shows it was decided | ☐ |
| 3 | Manish edits the v1 workbook and uploads it in the card's thread | "Applied v2…" with the new totals, and Send to AE releases v2 (this confirms the manifest fix) | ☐ |
| 4 | Manish presses **Return** and adds a note | The AE gets the note; the status changes to returned | ☐ |
| 5 | A user who isn't on the allowed list runs the command, or someone submits nonsense text | A polite refusal, or a failure DM sent to both the AE and the review channel | ☐ |

- [ ] For each estimate, open the workbook and check the totals by hand against the current master sheet.
- [ ] Do this for at least **3 real past deals** before going live.

---

## Phase 4 – Go live and run it

### Updates

Upload the new zip, then:

```bash
sudo docker build -t ns-estimator:v2 .
sudo docker rm -f ns-estimator
# then the same `docker run` command from 2.6, with :v2
```

- **Rollback:** run the `docker run` command again with `:v1`.
- Deploy outside working hours, because `rm -f` drops any estimate that's in progress.

### Template changes

1. Edit the blank template in `templates/source/`.
2. Rebuild it with `tools/build_template.py`.
3. Run `pytest`.
4. Deploy.

Every request records the template's SHA, so older estimates stay traceable to the template version they used.

### Patching

- [ ] Set up a monthly SSM Patch Manager run for the OS.

### Cost

| Item | Approx. monthly (us-east-1) |
|---|---|
| t3.small instance | ~$15 |
| 20 GB gp3 storage | ~$1.60 |
| Public IPv4 address | ~$3.65 |
| Snapshots | ~$1 |
| **AWS total** | **~$21–22** |
| Anthropic API | Variable; check after the first 20 requests |

---

## Order and owners

| Phase | Owner | Effort |
|---|---|---|
| 0 – Fix before you deploy | Developer | Half a day |
| 1 – Slack setup | Slack workspace admin | ~30 minutes |
| 2 – AWS setup | AWS admin | Half a day |
| 3 – Acceptance testing | Manish plus one AE | 1–2 days |
| 4 – Go live | Presales | — |
