# Expected monthly cost – Oracle AE web form (pilot)

Prices are AWS list prices in us-east-1 as I remember them; check the AWS pricing pages before quoting them to anyone. Figures are USD per month and assume the pilot: 2 AEs, at most about 20 requests a month.

| Item | Basis | Per month |
|---|---|---|
| **AWS WAF** | web ACL $5 + 3 rules $3 + requests ($0.60 per million) + CAPTCHA ($0.40 per 1,000 attempts); AWS managed common rule set has no extra fee | **about $8–10** (the fixed part is almost all of it) |
| Email (SES) | $0.10 per 1,000 emails, 4 or 5 per request; attachments add data charges | under $1 |
| API Gateway, Lambda | well inside the free tier or a few cents | under $1 |
| S3, DynamoDB, SQS, SNS | tiny volumes; attachments deleted after 90 days | under $1 |
| CloudWatch | 4 alarms ($0.10 each) + logs kept 90 days | about $1 |
| **AWS subtotal** | | **about $11–14**, with email, form, queue and storage together under $5 as planned |
| **AI per estimate** | each estimate reads the requirement twice with the large estimator guide; the retry on failure can add more. I have not measured it. Plan on **$0.30–1.00 per estimate** until the first 10 real requests give a real figure | 20 requests ≈ $6–20 |
| Custom domain (optional) | ACM certificate is free; Route 53 zone about $0.50 if you use it | under $1 |
| EC2 host and snapshots | already running; the new threads add negligible load; snapshots add a few cents per GB | no change in practice |

**Total for the pilot: about $20–35 a month.** The WAF is the only fixed cost that matters; it stays the same at 20 or 200 requests.

## Spend cap for the AI
- **Decided target: about $5 a day.** As far as I know the Anthropic console limits spend per month, not per day, so set the workspace limit (console → workspace → limits) to **$150** (5 × 30) and check the daily figure in the console. A true daily cap would need code in the bot; not built. At the assumed $0.30–1.00 an estimate, $5 a day is 5 to 16 estimates a day, far above the pilot volume.
- Note the first 10 requests' cost from the console and update this table.
- The form already limits each AE to 10 requests a day and the whole site by IP, so abuse cannot spend without bound, but only the console limit gives a hard stop.
- When the cap is reached, requests fail after 3 tries: the AE gets the generic "reviewing manually" email and the reviewer sees the error in Slack.

## What would raise the cost
- Opening to many AEs or other countries (WAF stays fixed; AI and SES scale with requests).
- Large attachments left in the bucket (90-day delete limits this).
- A bot or attack passing the WAF: the WAF rate rule and CAPTCHA are the first defence; the CloudWatch alarms are the warning.
