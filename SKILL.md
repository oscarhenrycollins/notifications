---
name: notifications
description: Use when the owner asks to be told when something happens or finishes, when a long job you started is done, or when something needs their attention. Send them a short notification with the Notifications app.
---

# Notifications

The Notifications app owns a `readable` AI feature through the Connection Router. Every `notify`, cron result, and HTTP publish is rewritten before delivery. Only `needs_you` items push to the phone, at high priority, with what happened and `What to do: …`. Useful `fyi` items are saved in the web inbox only (not published to ntfy at any priority); routine no-action `noise` keeps a receipt but no inbox item. Original input is preserved under collapsed **Details**. Never post directly to the engine: it is read-only to other publishers.

If the AI connection is unavailable, input is saved, a short quiet fallback appears in the inbox, and the app retries later. Explicit failures with a clear owner action may still alert; it never falls back to a raw JSON dump. The app admits model answers only after the router's validated `done` receipt. Inspect Connection Router readiness for `notifications/readable` if rewrites stay pending.

## Tools

- `notify` — send one notification. `message` is required; add a short `title`. Use `priority: high` only when it needs them soon and `urgent` only for real problems. Add a `link` (https) when there is something to open.
- `notifications_recent` — what was sent lately (default: last 24 hours). Check it before repeating yourself.
- `notifications_inbox` / `notifications_mark` — the owner's inbox with read, flagged and archived state; mark items only when they ask.

## Scheduled jobs

The app reads the agent's scheduled-job results once its Hermes cron folder is configured. Silent runs, successful routine maintenance, all-clear checks and verifier footers do not push. A failed task pushes only if the rewrite identifies a concrete action for the owner; agent-owned recovery is not a human action. Existing quiet-job, unchanged-failure dedupe, recovery lanes and job grouping remain in force before rewriting. Don't `notify` again for a result the relay already processed.

## When to notify

- They asked: "tell me when…", "let me know if…", "ping me when it's done".
- A long job you started for them finished or failed, and they are probably not watching the chat.
- Something needs their decision or action.

## When not to

- Don't notify for every small step, or for things you are already telling them in the chat they are looking at.
- Never put passwords, keys, codes or other secrets in a notification.
- One notification per event. If nothing changed, don't send another.

## Verify or repair delivery

- Resolve the running front and port from container mounts and runtime metadata, not an old app checkout or assumed default port.
- Exercise a real rewrite on an unsubscribed test topic, then read back the exact app receipt and ntfy cache. A quiet FYI must have an inbox row and no ntfy publication; minimum priority alone cannot prove no phone push.
- Require the router's validated `done` before admitting model text. Keep a failed rewrite pending and retain its original; do not switch providers to get a test to pass.
- Test engine authorization from empty storage: ntfy creates its auth database only after the server first starts. Provision the publishing user after that startup and deny anonymous writes throughout.
- Preserve original cron thread keys separately from rewritten titles, or changing the copy silently splits the owner's existing job threads.

## Style

Plain and short: what happened and what (if anything) they need to do. For example: title "Video ready", message "Your edit of beach.mp4 is in Files → Videos/Edited."
