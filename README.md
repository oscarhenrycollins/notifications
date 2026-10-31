# Notifications

Your Wizard's own notification server: unmodified [ntfy](https://ntfy.sh) on your box. The agent tells you when something is done or needs you.

A [Wizard app](https://www.aiwizards.com/wizard-apps): install it from the Wizard Apps screen, or with the Wizard App Manager on any box running stock [Runtipi](https://runtipi.io). It fills the `notifications` system spot.

- `wizard.md`: what the app is and what it gives the agent (spec: wizard.md v0.2).
- `runtipi/`: stock Runtipi `config.json` and `docker-compose.yml`. The engine is the official `binwiederhier/ntfy` image, pinned by digest, unchanged.
- `mcp/`: the app's MCP front (`front.py` + `tools.py`), inlined into the compose file by `python3 mcp/inline.py`. It serves `/mcp` (tools `notify`, `notifications_recent`, `notifications_inbox`, `notifications_mark`, box-only), the inbox at `/notifications/`, the scheduled-job relay (`mcp/relay.py`), and passes everything else unchanged to ntfy.
- `SKILL.md`: the skill the agent loads when this app is installed.
- `.github/workflows/publish.yml`: every push to `main` re-publishes to the index at apps.aiwizards.com.

## Scheduled jobs

Set **Hermes scheduled jobs folder** in the app's settings to your agent's cron folder (e.g. `/home/you/.hermes/cron`, mounted read-only) and every scheduled-job result lands in the inbox on its own, no Hermes changes. Silent runs stay quiet, an unchanged failure alerts once and then once a day, a changed error or a failure after a success alerts at once, and a post the server doesn't confirm is retried. The first pass looks back 24 hours only. Optional: **Jobs to keep quiet when they succeed** (names or ids) for jobs that show their own badge, such as a morning report. `CRON_RELAY=off` turns it off.

**Failures go to your agent, not to you.** Add a Hermes cron job named `Failure responder` (deliver `local`) and the relay stops posting failed runs. It keeps them in `cron-failures.json` in the app's inbox folder, where the responder picks them up, fixes the cause and marks each one handled in `cron-failures-ack.json`. The job's next success closes it. You hear about a failure only when the responder says you must act, or as a safety net once per episode if a failure is still open and unhandled after 24 hours (`CRON_ESCALATE_H`). The responder's own runs and crashes always post. Pause or remove the responder and failures post to you again. `CRON_FAILURES=owner` keeps the old behaviour.

## On your phone

Install the free ntfy app, add this server's private address (for example `https://mybox.tail1234.ts.net:8790`), and subscribe to the topic `wizard`. On iPhone, set the optional relay (`https://ntfy.sh`) in the app's settings on the box if you want notifications instantly; it forwards only a wake-up ping, never the text.

## License

ntfy is Apache-2.0 (LICENSE). The packaging in this repo is Apache-2.0 as well.
