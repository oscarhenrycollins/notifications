# ---- Scheduled-job relay: the agent's cron results become notifications ----
# Reads the Hermes cron folder (mounted read-only at CRON_DIR) every few minutes and posts each new run to the
# `cron` topic, so the inbox shows what scheduled jobs did without the agent spending a turn on it. Rules:
# - silent runs ([SILENT], "no change") post nothing;
# - an identical failure alerts once, then at most once a day while it keeps failing; a changed error, or a
#   failure that comes back after a success, alerts at once;
# - dated outcomes are never merged (numbers and dates are kept), so each daily report is its own item;
# - jobs listed in CRON_QUIET_JOBS (ids or names) never post a success (an app badge covers them); their
#   failures still alert;
# - the agent runtime's own self-check footer ("File-mutation verifier: ...") is never owner content: it is
#   stripped, and a run that said [SILENT] stays silent whatever the runtime appended after it;
# - CRON_FAILURES=off: an agent-side recovery lane owns failed runs (it repairs them and tells the owner only
#   what it could not fix), so failures are not posted here; warning-sign alerts written for the owner still are;
# - a post the server does not confirm is retried on the next pass; it never consumes the run.
# The first pass on a box only looks back CRON_LOOKBACK_H hours, so installing never floods the inbox.
import hashlib
import re

CRON_DIR = os.environ.get("CRON_DIR", "/hermes-cron")
CRON_TOPIC = os.environ.get("CRON_TOPIC", "cron")
CRON_RELAY = os.environ.get("CRON_RELAY", "on").strip().lower() not in ("off", "0", "false", "no")
CRON_EVERY = max(30, int(os.environ.get("CRON_EVERY_S", "300") or 300))
CRON_LOOKBACK_H = float(os.environ.get("CRON_LOOKBACK_H", "24") or 24)
CRON_QUIET = {x.strip().lower() for x in os.environ.get("CRON_QUIET_JOBS", "").split(",") if x.strip()}
CRON_STATE = os.environ.get("CRON_STATE", os.path.join(os.path.dirname(STATE_FILE) or "/data", "cron-relay.json"))


def _failures_setting():
    """CRON_FAILURES from the app form, else a box-local /data/cron-relay-settings.json {"failures": "off"}."""
    v = os.environ.get("CRON_FAILURES", "")
    if not v.strip():
        try:
            with open(os.path.join(os.path.dirname(CRON_STATE), "cron-relay-settings.json")) as f:
                v = str(json.load(f).get("failures", ""))
        except (OSError, ValueError, AttributeError):
            v = ""
    return v.strip().lower() not in ("off", "0", "false", "no")


CRON_FAILURES = _failures_setting()
# The agent runtime appends this self-check to a turn's text; it is diagnostics for the agent, not a result.
_RUNTIME_FOOTER = re.compile(r"\n?[ \t]*(\u26a0\ufe0f?[ \t]*)?File-mutation verifier:[\s\S]*")
_EOL = r"[ \t]*(?=\n|\Z)"   # end of line without a dollar sign (compose would interpolate one)
_relay_lock = threading.Lock()


def _job_names(cron_dir):
    try:
        with open(os.path.join(cron_dir, "jobs.json")) as f:
            d = json.load(f)
        jobs = d.get("jobs", d) if isinstance(d, dict) else d
        return {j["id"]: j.get("name") or j["id"] for j in jobs}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _section(text, name):
    m = list(re.finditer(r"^## %s%s" % (re.escape(name), _EOL), text, re.M))
    if not m:
        return ""
    rest = text[m[-1].end():]
    nxt = re.search(r"^## |^\*\*Response Characters", rest, re.M)
    return (rest[:nxt.start()] if nxt and name != "Response" else rest).strip()


def cron_summarize(text):
    """(failed, body) for a run worth telling the owner about, or None when the run is silent."""
    status = (re.search(r"^\*\*Status:\*\*[ \t]*(.+)", text, re.M) or [None, ""])[1].strip()
    if any(s in status for s in ("silent", "no_change")):
        return None
    err = _section(text, "Error")
    failed = bool(err) or any(w in status.lower() for w in ("failed", "blocked", "error"))
    if err:
        body = err.strip("`\n ") or "The job failed."
    else:
        body = _section(text, "Response") or _section(text, "Script Output")
        if not body:
            parts = re.split(r"^---%s" % _EOL, text, maxsplit=1, flags=re.M)
            body = parts[1] if len(parts) > 1 else text.split(status, 1)[-1] if status else ""
    body = _RUNTIME_FOOTER.sub("", body).strip()
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines or any(x.upper().startswith(("[SILENT]",)) or x.upper() == "SILENT" for x in (lines[0], lines[-1])):
        return None
    # A short all-clear heartbeat ("meter snapshot ok") is a silent run, unless the job says it failed.
    if not failed and len(body) < 60 and re.search(r"\b(ok|healthy|nothing to report|no new activity)\b", body, re.I) and \
            not re.search(r"\b(fail\w*|error\w*|warn\w*|full|down)\b", body, re.I):
        return None
    # A body that opens with a warning sign is an alert the owner needs, even when the job itself succeeded.
    if body.startswith(("\u26a0", "\U0001f6a8")):
        failed = True
    head = body[:240].lower()
    if re.search(r"\b(failed|failure|needs review|needs you|error|stale|not_started|action required)\b", head) and \
            not re.search(r"\b(no|0) (failures|errors)\b|all monitored checks passed", head):
        failed = True
    return failed, body


def cron_failure_policy(job, body, failed, alerts, now):
    """(suppress, receipt). Exact-body repeats are quiet for 24 hours; anything else alerts."""
    if not failed:
        alerts.pop(job, None)
        return False, None
    digest = hashlib.sha256(body.encode()).hexdigest()
    prior = alerts.get(job, {})
    suppress = prior.get("digest") == digest and 0 <= now - prior.get("sent_at", 0) < 86400
    return suppress, {"digest": digest, "sent_at": now}


def _cron_post(title, message, priority, tags):
    payload = {"topic": CRON_TOPIC, "title": title, "message": message, "priority": priority, "tags": tags}
    rewrite_and_deliver(payload)
    return True


def cron_relay_once(cron_dir=None, state_path=None, post=None, now=None):
    """One pass. Returns {"sent", "skipped"} (or {"off": reason})."""
    cron_dir = cron_dir or CRON_DIR
    state_path = state_path or CRON_STATE
    post = post or _cron_post
    out_dir = os.path.join(cron_dir, "output")
    if not os.path.isdir(out_dir):
        return {"off": "no cron output folder at %s" % out_dir}
    with _relay_lock:
        try:
            with open(state_path) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {"since": (now or time.time()) - CRON_LOOKBACK_H * 3600, "done": []}
        done = set(st.get("done", []))
        alerts = st.get("failure_alerts", {})
        quiet_days = st.get("quiet_days", {})
        since = float(st.get("since", 0))
        names = _job_names(cron_dir)
        files = []
        for job in os.listdir(out_dir):
            d = os.path.join(out_dir, job)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.endswith(".md"):
                        p = os.path.join(d, fn)
                        try:
                            files.append((os.stat(p).st_mtime, job, fn, p))
                        except OSError:
                            pass
        sent = skipped = 0
        for mt, job, fn, p in sorted(files):
            key = job + "/" + fn
            if key in done:
                continue
            clock = now or time.time()
            if mt < since or clock - mt < 20:     # older than the first pass, or still being written
                if mt < since:
                    done.add(key)
                continue
            try:
                with open(p, errors="replace") as f:
                    raw = f.read()
            except OSError:
                continue
            s = cron_summarize(raw)
            if s is None:
                if not re.search(r"^\*\*Status:\*\*[ \t]*(error|failed|blocked)", raw, re.M | re.I):
                    alerts.pop(job, None)
                skipped += 1
                done.add(key)
                continue
            failed, body = s
            if failed and not CRON_FAILURES and not body.startswith(("\u26a0", "\U0001f6a8")):
                skipped += 1                       # the recovery lane owns it; the owner hears only its outcome
                done.add(key)
                continue
            had_alert = job in alerts
            suppress, receipt = cron_failure_policy(job, body, failed, alerts, mt)
            if suppress:
                skipped += 1
                done.add(key)
                continue
            name = names.get(job) or (re.search(r"^# Cron Job: (.+)", raw, re.M) or [0, job])[1]
            recovered = False
            if not failed and (job.lower() in CRON_QUIET or str(name).strip().lower() in CRON_QUIET):
                if not had_alert:
                    quiet_days.setdefault(fn[:10], key)
                    skipped += 1
                    done.add(key)
                    continue
                recovered = True                   # a quiet job's first success after a failure closes its thread
            when = time.strftime("%d %b %H:%M UTC", time.gmtime(mt))
            warning = body.startswith(("\u26a0", "\U0001f6a8"))
            prefix = "Recovered: " if recovered else "Failed: " if failed and not warning else ""
            try:
                ok = post(prefix + str(name), "%s\n\n%s" % (body, when),
                          4 if failed else 2, ["cron", "warning" if failed else "clock"])
            except OSError:
                ok = False
            if not ok:
                break                              # retried next pass; the run and its fingerprint stay unconsumed
            done.add(key)
            if receipt is not None:
                alerts[job] = receipt
            sent += 1
        keep = sorted(k for k in done if os.path.exists(os.path.join(out_dir, k)))
        quiet_days = dict(sorted(quiet_days.items())[-60:])
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"since": since, "done": keep, "failure_alerts": alerts, "quiet_days": quiet_days}, f)
        os.replace(tmp, state_path)
        return {"sent": sent, "skipped": skipped}


def _cron_loop():
    time.sleep(20)                                 # let the engine start first
    while True:
        try:
            r = cron_relay_once()
            if r.get("sent"):
                print("cron relay: sent %d, skipped %d" % (r["sent"], r["skipped"]), flush=True)
        except Exception as e:                     # never take the front down; try again next pass
            print("cron relay error: %s: %s" % (type(e).__name__, e), flush=True)
        time.sleep(CRON_EVERY)


if __name__ == "__main__" and CRON_RELAY and os.path.isdir(CRON_DIR):
    threading.Thread(target=_cron_loop, name="cron-relay", daemon=True).start()
