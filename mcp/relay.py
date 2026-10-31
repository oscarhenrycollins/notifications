
# ---- Scheduled-job relay: the agent's cron results become notifications ----
# Reads the Hermes cron folder (mounted read-only at CRON_DIR) every few minutes and posts each new run to the
# `cron` topic, so the inbox shows what scheduled jobs did without the agent spending a turn on it. Rules:
# - silent runs ([SILENT], "no change") post nothing;
# - an identical failure alerts once, then at most once a day while it keeps failing; a changed error, or a
#   failure that comes back after a success, alerts at once;
# - dated outcomes are never merged (numbers and dates are kept), so each daily report is its own item;
# - jobs listed in CRON_QUIET_JOBS (ids or names) never post a success (an app badge covers them); their
#   failures still alert;
# - a post the server does not confirm is retried on the next pass; it never consumes the run.
# The first pass on a box only looks back CRON_LOOKBACK_H hours, so installing never floods the inbox.
#
# Failures go to the agent, not the owner, once the box has a failure responder: a Hermes cron job named
# CRON_RESPONDER (default "Failure responder", or its id). Then a failed run is not posted; it is kept in the open-
# failures file (cron-failures.json beside the relay state) that the responder watches, fixes and acknowledges in
# cron-failures-ack.json (written by the agent on the host: {"<job id>": <first_seen of the episode it handled>}). The next success of that job
# closes it. Safety net: a failure still open and unacknowledged after CRON_ESCALATE_H hours posts once per episode,
# and the responder's own runs always post (its failures included), so a broken responder is never silent.
# CRON_FAILURES=owner forces the old behaviour (every failure posts); =agent forces routing even without a responder.
import hashlib
import re

CRON_DIR = os.environ.get("CRON_DIR", "/hermes-cron")
CRON_TOPIC = os.environ.get("CRON_TOPIC", "cron")
CRON_RELAY = os.environ.get("CRON_RELAY", "on").strip().lower() not in ("off", "0", "false", "no")
CRON_EVERY = max(30, int(os.environ.get("CRON_EVERY_S", "300") or 300))
CRON_LOOKBACK_H = float(os.environ.get("CRON_LOOKBACK_H", "24") or 24)
CRON_QUIET = {x.strip().lower() for x in os.environ.get("CRON_QUIET_JOBS", "").split(",") if x.strip()}
CRON_STATE = os.environ.get("CRON_STATE", os.path.join(os.path.dirname(STATE_FILE) or "/data", "cron-relay.json"))
CRON_FAILURES = os.environ.get("CRON_FAILURES", "auto").strip().lower()
CRON_RESPONDER = {x.strip().lower() for x in os.environ.get("CRON_RESPONDER", "Failure responder").split(",") if x.strip()}
CRON_ESCALATE_H = float(os.environ.get("CRON_ESCALATE_H", "24") or 24)
_EOL = r"[ \t]*(?=\n|\Z)"   # end of line without a dollar sign (compose would interpolate one)
_relay_lock = threading.Lock()


def _jobs(cron_dir):
    try:
        with open(os.path.join(cron_dir, "jobs.json")) as f:
            d = json.load(f)
        jobs = d.get("jobs", d) if isinstance(d, dict) else d
        return [j for j in jobs if isinstance(j, dict) and j.get("id")]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _job_names(cron_dir):
    return {j["id"]: j.get("name") or j["id"] for j in _jobs(cron_dir)}


def _is_responder(job, name):
    return job.lower() in CRON_RESPONDER or str(name).strip().lower() in CRON_RESPONDER


def _route_to_agent(cron_dir):
    """True when failures go to the agent's responder instead of the owner."""
    if CRON_FAILURES in ("owner", "off", "no", "0"):
        return False
    if CRON_FAILURES == "agent":
        return True
    return any(_is_responder(j["id"], j.get("name") or "") and j.get("enabled", True) is not False
               and j.get("state", "") != "paused" for j in _jobs(cron_dir))


def _read_json(path, default):
    try:
        with open(path) as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


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
    body = body.strip()
    if not body or body in ("[SILENT]", "SILENT"):
        return None
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
    payload = {"topic": CRON_TOPIC, "title": title[:250], "message": message[:3800], "priority": priority, "tags": tags}
    status, _ = http("POST", "/", json.dumps(payload).encode(), {"Content-Type": "application/json"}, timeout=20)
    return status == 200


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
        to_agent = _route_to_agent(cron_dir)
        failures_path = os.path.join(os.path.dirname(state_path) or ".", "cron-failures.json")
        open_f = _read_json(failures_path, {}) if to_agent else {}
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
                    open_f.pop(job, None)              # a clean run closes the agent's open failure
                skipped += 1
                done.add(key)
                continue
            failed, body = s
            name = names.get(job) or (re.search(r"^# Cron Job: (.+)", raw, re.M) or [0, job])[1]
            if to_agent and not _is_responder(job, name):
                if failed:                             # the agent's job: queue it, tell the owner nothing
                    o = open_f.get(job) or {"first_seen": mt, "runs": 0}
                    o.update(name=str(name), body=body[:3800], last_seen=mt, runs=o.get("runs", 0) + 1,
                             digest=hashlib.sha256(body.encode()).hexdigest()[:16])
                    open_f[job] = o
                    skipped += 1
                    done.add(key)
                    continue
                open_f.pop(job, None)
            responder = to_agent and _is_responder(job, name)
            if responder:                              # it speaks only when the owner must act; its own crash is a failure
                failed = bool(_section(raw, "Error")) or bool(re.search(r"^\*\*Status:\*\*[ \t]*(error|failed)", raw, re.M | re.I))
            suppress, receipt = cron_failure_policy(job, body, failed, alerts, mt)
            if suppress:
                skipped += 1
                done.add(key)
                continue
            if not failed and (job.lower() in CRON_QUIET or str(name).strip().lower() in CRON_QUIET):
                quiet_days.setdefault(fn[:10], key)
                skipped += 1
                done.add(key)
                continue
            when = time.strftime("%d %b %H:%M UTC", time.gmtime(mt))
            try:
                ok = post(("Failed: " if failed else "") + str(name), "%s\n\n%s" % (body, when),
                          4 if failed or responder else 2, ["cron", "warning" if failed or responder else "clock"])
            except OSError:
                ok = False
            if not ok:
                break                              # retried next pass; the run and its fingerprint stay unconsumed
            done.add(key)
            if receipt is not None:
                alerts[job] = receipt
            sent += 1
        if to_agent:
            sent += _escalate(open_f, _read_json(os.path.join(os.path.dirname(state_path) or ".", "cron-failures-ack.json"), {}), post, now or time.time())
            _write_json(failures_path, open_f)
        keep = sorted(k for k in done if os.path.exists(os.path.join(out_dir, k)))
        quiet_days = dict(sorted(quiet_days.items())[-60:])
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"since": since, "done": keep, "failure_alerts": alerts, "quiet_days": quiet_days}, f)
        os.replace(tmp, state_path)
        return {"sent": sent, "skipped": skipped}


def _escalate(open_f, ack, post, now):
    """Safety net: one post per failure episode left open and unacknowledged for CRON_ESCALATE_H hours."""
    sent = 0
    for job, o in sorted(open_f.items()):
        handled = ack.get(job)
        if o.get("escalated") or (handled is not None and float(handled) >= float(o.get("first_seen", 0)) - 1):
            continue
        if now - float(o.get("first_seen", now)) < CRON_ESCALATE_H * 3600:
            continue
        hours = int((now - float(o["first_seen"])) // 3600)
        msg = "Failing for %d hours and the agent has not fixed it yet. Latest:\n%s" % (hours, o.get("body", ""))
        try:
            ok = post("Still failing: " + o.get("name", job), msg, 4, ["cron", "warning"])
        except OSError:
            ok = False
        if ok:
            o["escalated"] = now
            sent += 1
    return sent


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
