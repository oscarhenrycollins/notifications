# Notifications

The Notifications app rewrites every incoming update with its own `readable` Connection Router model feature before deciding whether to notify. The engine remains unmodified [ntfy](https://ntfy.sh).

A [Wizard app](https://www.aiwizards.com/wizard-apps): install it from the Wizard Apps screen, or with the Wizard App Manager on any box running stock [Runtipi](https://runtipi.io). It fills the `notifications` system spot.

- `wizard.md`: what the app is and what it gives the agent (spec: wizard.md v0.2).
- `runtipi/`: stock Runtipi `config.json` and `docker-compose.yml`. The engine is the official `binwiederhier/ntfy` image, pinned by digest, unchanged.
- `mcp/`: the app front, existing inbox and cron relay, plus `readable.py` (prompt, validated router completion, durable original input, retry queue and delivery policy). Generate inline packaging with `python3 mcp/embed_minimal.py && python3 mcp/inline.py`. All `notify`, relay, HTTP POST/PUT and GET topic/publish paths enter the same rewrite function. Reads and subscriptions are proxied to ntfy; direct engine writes are denied to other callers.
- `SKILL.md`: the skill the agent loads when this app is installed.
- `.github/workflows/publish.yml`: every push to `main` re-publishes to the index at apps.aiwizards.com.

## Scheduled jobs

Set **Hermes scheduled jobs folder** in the app's settings to the agent's cron folder (mounted read-only). Existing quiet jobs, failure/recovery deduplication and job threads are preserved. Before any delivery the app classifies the result:

- `needs_you`: a plain title and 1–3 sentences describing what happened and **What to do: …**, published at high priority.
- `fyi`: saved only in the Wizard inbox with **Nothing to do.** It is never published to any ntfy topic, so Android subscription/priority settings cannot accidentally turn it into a push.
- `noise`: retained as a receipt only. Routine cleanups, successful backups, all-clear checks, silent/verifier output and agent-owned recovery do not alert.

Original text is stored in the app's SQLite database and shown under collapsed **Details**. If the router fails, the input and a quiet plain fallback are saved and retried; explicit failures with a concrete owner action can still alert. Provisional text is discarded unless the router validates `done`; completion provenance is retained. No Hermes core changes or provider credentials are involved.

The manifest declares the app-owned model pin. WAM mints its Connection Router grant on installation; the front reads `/wizard/grants.json` (or `WIZARD_ROUTER_URL` / `WIZARD_ROUTER_GRANT` environment injection). Follow the installed router's networking convention. A host-loopback router requires a host-network front with a private loopback engine mapping; keep these deployment-specific addresses out of the app workflow. Engine initialization creates a per-install password, and anonymous engine access is read-only; publisher scripts use the front, never the engine.

Run `python3 -m unittest discover -s tests -v`, `python3 mcp/inline.py --check`, and the live-source parity gate before promotion.

## On your phone

Install the free ntfy app, add this server's private address (for example `https://mybox.tail1234.ts.net:8790`), and subscribe to the topic `wizard`. On iPhone, set the optional relay (`https://ntfy.sh`) in the app's settings on the box if you want notifications instantly; it forwards only a wake-up ping, never the text.

## License

ntfy is Apache-2.0 (LICENSE). The packaging in this repo is Apache-2.0 as well.
