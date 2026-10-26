# Readable notifications

Outcome: before publication, Notifications owns one Connection Router model feature and decides whether an update needs its owner, belongs only in the inbox, or is noise.

Reuse:
- Model routing: REUSE Connection Router `/v1/models/readable/run`, app grants and validated done provenance.
- Inbox, marks, job grouping, media, layouts and Appearance: REUSE the exact running front and its shared WizardApp client.
- Notification admission: EXTEND existing app publisher/cron relay with one durable rewrite and delivery function; no new platform capability or view implementation.
- Details: REUSE native collapsed `details` element within the existing item renderer, original input retained in app storage.

Policy: needs_you publishes summary only at high priority; fyi is stored locally and merged into the inbox (never published, so Android subscription settings cannot cause a push); noise keeps a receipt only. Model failure retains input, stores a quiet plain fallback and retries. Raw HTTP publishing on the existing front is intercepted before ntfy sees it. No Hermes core changes.

Baseline: copied the exact served front before editing, split back into its original source modules and recovered embedded live web assets. Upstream push denied (403); live baseline pushed to the existing defleur-agents fork on fix/readable-notification-admission.

Verification: focused fake-router tests, full existing source tests, generated-compose freshness, app-parity-gate, real router replay to an unadvertised test topic, cache and inbox readback, restart and MCP/health/cron verification. Existing owner inbox state is preserved. Rollback: restore saved live front and compose override, recreate only Notifications.
