# linosco-crons

Public scheduler repo for LinosCo background cron jobs, kept **public** so GitHub
Actions minutes are free (private repos consume the paid Actions quota).

Each workflow only triggers work elsewhere (HTTP calls / monitoring scripts) — no
application code lives here. Secrets are configured per-repo in
*Settings → Secrets and variables → Actions*.

## Workflows

| Workflow | Cadence | Purpose | Required secrets |
|---|---|---|---|
| `ai-interviewer-cron.yml` | various (*/10, */15, daily, weekly, monthly) | Drives the AI Interviewer **V1** platform `/api/cron/*` endpoints on Railway | `CRON_SECRET`, `RAILWAY_APP_URL` |
| `contrada-monitor.yml` | daily 05:00 UTC | Site + email uptime monitor for contradatezzamicheloni.it, reports to Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |

Migrated out of the private repos `ai-interviewer` and `contrada-lessinia-legacy`
to stop consuming paid Actions minutes.
