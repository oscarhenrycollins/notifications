# Notifications tools: the agent sends the owner a notification and reads recent ones.
# Everything goes to one topic on the box's own ntfy server (the engine behind this front), which the owner
# subscribes to once in the ntfy phone app or the web app. Nothing leaves the box unless the owner points
# their phone at it.

TOPIC = os.environ.get("NOTIFY_TOPIC", "wizard")
PRIORITIES = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}


def _box_only():
    """Only apps on this box (e.g. the agent through the Wizard hub) can use these tools, never a request that
    came in through Tailscale Serve, a tunnel or a proxy."""
    h = REQUEST.get()
    if any(h.get(k) for k in ("tailscale-user-login", "x-forwarded-for", "forwarded", "cf-connecting-ip",
                              "cf-access-jwt-assertion", "x-real-ip")):
        raise PermissionError("the Notifications tools are only for apps on this box")


def _clean(text, limit):
    text = " ".join(str(text or "").split()) if limit <= 250 else str(text or "").strip()
    return text[:limit]


@tool("notify", "Send an update through the app's AI editor. Only concrete owner actions push; useful FYI stays in the inbox and routine noise is suppressed. Original details are retained.",
      {"message": {"type": "string", "description": "What happened, in one or two sentences"},
       "title": {"type": "string", "description": "Short title, e.g. 'Video ready' (optional)"},
       "priority": {"type": "string", "enum": list(PRIORITIES), "description": "default unless it is urgent"},
       "link": {"type": "string", "description": "https link to open when tapped (optional)"},
       "tags": {"type": "string", "description": "Comma-separated emoji short codes, e.g. 'white_check_mark' (optional)"}},
      ["message"])
def notify(message, title="", priority="default", link="", tags=""):
    _box_only()
    body = str(message or "").strip()
    if not body:
        raise ValueError("say what to tell the owner")
    payload = {"topic": TOPIC, "message": body, "priority": PRIORITIES.get(str(priority or "default"), 3)}
    if title:
        payload["title"] = _clean(title, 250)
    if link:
        url = str(link).strip()
        if not url.startswith("https://") or any(c.isspace() for c in url):
            raise ValueError("link must be an https:// address")
        payload["click"] = url[:2000]
    if tags:
        payload["tags"] = [t for t in (_clean(x, 40) for x in str(tags).split(",")) if t][:5]
    return rewrite_and_deliver(payload)


@tool("notifications_recent", "Read the notifications sent to the owner recently, newest last.",
      {"since": {"type": "string", "description": "How far back: e.g. '30m', '12h', '2d' (default 24h)"},
       "limit": {"type": "integer", "description": "Max notifications, default 20, up to 100"}})
def notifications_recent(since="24h", limit=20):
    _box_only()
    window = str(since or "24h").strip()
    if not window or len(window) > 6 or not window[:-1].isdigit() or window[-1] not in "smhd":
        raise ValueError("since looks like 30m, 12h or 2d")
    cap = max(1, min(int(limit or 20), 100))
    status, raw = http("GET", "/%s/json?poll=1&since=%s" % (urllib.parse.quote(TOPIC), window), timeout=20)
    items = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("event") != "message":
            continue
        items.append({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(m.get("time", 0))),
                      "title": m.get("title", ""), "message": m.get("message", ""),
                      "priority": m.get("priority", 3), "link": m.get("click", "")})
    return {"topic": TOPIC, "since": window, "count": len(items), "notifications": items[-cap:]}


# ---- Inbox: the owner's view of everything sent here, with read / flag / archive ----
# The page and its API live on this front at /notifications/ (the Wizard shell opens it at the same path on its own
# origin). ntfy keeps the messages (30 days); this keeps only the owner's marks, in a small JSON file.
import threading
import hashlib
import re

INBOX = "/notifications/"
# The card inbox (tap a row, it opens as a full card with actions) is the default on every skin;
# NOTIF_LAYOUT=classic keeps the original screen for skins outside Miguel's set.
LAYOUT = os.environ.get("NOTIF_LAYOUT", "").strip().lower() or "cards"
STATE_FILE = os.environ.get("INBOX_STATE", "/data/inbox.json")
TOPICS = [t for t in os.environ.get("INBOX_TOPICS", TOPIC).split(",") if t.strip()]
_lock = threading.Lock()


def _load_state():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, STATE_FILE)


# ntfy charges every message a poll replays from its cache against the visitor's daily bandwidth budget
# (500 MB by default, the same budget attachments use; the request-limit exemption does not cover it). The
# inbox reads both topics on every list, count and mark, so re-reading "since=all" each time used the whole
# budget in a day and the page showed "HTTP Error 429: Too Many Requests". Each topic is read in full once
# every POLL_FULL_EVERY seconds (that also drops messages ntfy has expired); in between only messages newer
# than the last one seen are fetched. If the engine refuses, the last good copy is shown instead of an error.
POLL_FULL_EVERY = int(os.environ.get("INBOX_POLL_FULL_EVERY", "600"))
_poll_cache = {}
_poll_lock = threading.Lock()


def _poll(topic, since):
    status, raw = http("GET", "/%s/json?poll=1&since=%s" % (urllib.parse.quote(topic.strip()), since), timeout=20)
    out = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("error") and not m.get("event"):    # ntfy can report a limit inside a 200 stream
            raise RuntimeError("notification server: %s" % m.get("error"))
        if m.get("event") == "message":
            out.append(m)
    return out


def _topic_messages(topic, now=None):
    now = time.time() if now is None else now
    with _poll_lock:
        c = _poll_cache.get(topic)
        try:
            if c is None or now - c["full_at"] >= POLL_FULL_EVERY:
                c = {"msgs": _poll(topic, "all"), "full_at": now}
            elif c["msgs"]:
                seen = {m["id"] for m in c["msgs"]}
                c["msgs"] += [m for m in _poll(topic, str(int(c["msgs"][-1].get("time", 0)))) if m["id"] not in seen]
            else:
                c["msgs"] = _poll(topic, "all")
            _poll_cache[topic] = c
        except Exception as e:
            if c is None:
                raise
            print("inbox poll %s: %s: %s (showing the last good copy)" % (topic, type(e).__name__, e), flush=True)
        return [dict(m) for m in c["msgs"]]


def _messages(since="all"):
    out = []
    for t in TOPICS:
        out.extend(_topic_messages(t))
    details, thread_titles = {}, {}
    quiet, details = readable_messages()
    with readable_db() as db:
        for row in db.execute("SELECT raw,sent FROM updates WHERE sent != ''"):
            sent, raw = json.loads(row["sent"]), json.loads(row["raw"])
            if "cron" in (raw.get("tags") or []):
                thread_titles[sent["id"]] = raw.get("title", "")
    out.extend(quiet)
    for item in out:
        if item["id"] in details:
            item["details"] = details[item["id"]]
        if item["id"] in thread_titles:
            item["thread_title"] = thread_titles[item["id"]]
    out.sort(key=lambda m: m.get("time", 0), reverse=True)
    return out


def _thread_key(it):
    """Scheduled-job runs of one job share a thread (the job name); agent notes stay one row each."""
    if it["source"] != "cron":
        return ""
    return " ".join(re.sub(r"^(Failed|Recovered): ", "", it.get("thread_title", it["title"]) or "").split()).lower()


def _thread_id(key):
    return "t-" + hashlib.sha256(key.encode()).hexdigest()[:16]


_STAMP = re.compile(r"(\d{1,2}) (\w{3}) (\d{2}):(\d{2}) UTC\s*\Z")
_MONTHS = "JanFebMarAprMayJunJulAugSepOctNovDec"


def _run_time(it):
    """When the job ran: the relay ends each result with its run stamp; the post time is the fallback (a backlog
    relayed in one pass has one post time)."""
    m = _STAMP.search(it["message"] or "")
    if not m or m.group(2) not in _MONTHS or _MONTHS.index(m.group(2)) % 3:
        return it["time"]
    import calendar
    y = time.gmtime(it["time"]).tm_year
    t = calendar.timegm((y, _MONTHS.index(m.group(2)) // 3 + 1, int(m.group(1)), int(m.group(3)), int(m.group(4)), 0))
    return calendar.timegm((y - 1,) + time.gmtime(t)[1:6]) if t > it["time"] + 86400 else t


def _threads(items):
    """Collapse each job's runs into one row. The row is the newest run (title, body, link) and carries every run
    in `runs`; its id is a thread id that inbox_mark fans out to the member ids. Marks stay per run, so a new run
    after Mark read or Archive is unread again and re-lights the row."""
    out, groups = [], {}
    for it in items:                                   # items arrive newest first
        key = _thread_key(it)
        if not key:
            out.append(it)
            continue
        g = groups.get(key)
        if g is None:
            g = groups[key] = dict(it, id=_thread_id(key), runs=[])
            out.append(g)
        run = {k: it[k] for k in ("id", "time", "title", "message", "priority", "read", "flag", "archived")}
        run["run_time"] = _run_time(it)
        g["runs"].append(run)
    for key, g in groups.items():
        g["runs"].sort(key=lambda r: -r["run_time"])
        head = next(i for i in items if i["id"] == g["runs"][0]["id"])
        g.update({k: head[k] for k in ("time", "title", "message", "link", "tags")})
        runs = g["runs"]
        unread = [r for r in runs if not r["read"]]
        g["read"] = not unread
        g["flag"] = any(r["flag"] for r in runs)
        g["archived"] = all(r["archived"] for r in runs)
        g["priority"] = max([r["priority"] for r in unread] or [runs[0]["priority"]])
        g["unread_runs"] = len(unread)
    # Newest first, and nothing else: opening or marking a row never moves it (the red dot shows failures).
    out.sort(key=lambda i: -i["time"])
    return out


def inbox_items(view="inbox", limit=200, threads=True):
    st = _load_state()
    items = []
    for m in _messages():
        s = st.get(m["id"], {})
        tags = m.get("tags") or []
        it = {"id": m["id"], "time": m.get("time", 0), "title": m.get("title", ""), "message": m.get("message", ""), "details": m.get("details", ""), "thread_title": m.get("thread_title", m.get("title", "")),
              "priority": m.get("priority", 3), "link": m.get("click", ""), "tags": tags,
              "source": "cron" if "cron" in tags else "agent",
              "read": bool(s.get("read")), "flag": bool(s.get("flag")), "archived": bool(s.get("archived"))}
        items.append(it)
    group = _threads if threads else (lambda xs: xs)
    # Counts come from the same rows the inbox shows (one per job thread), so badge and list never disagree.
    live = group([i for i in items if not i["archived"]])
    counts = {"unread": sum(1 for i in live if not i["read"]),
              "needs": sum(1 for i in live if not i["read"] and i["priority"] >= 4),
              "flagged": sum(1 for i in live if i["flag"])}
    # Filter runs first, then group, so an archived thread comes back as a fresh row when a new run lands.
    if view == "flagged":
        rows = group([i for i in items if i["flag"] and not i["archived"]])
    elif view == "archive":
        rows = group([i for i in items if i["archived"]])
    elif view == "all":
        rows = group(items)
    else:
        rows = live
    return {"view": view, "counts": counts, "items": rows[:max(1, min(int(limit or 200), 500))]}


def _expand_ids(ids, marks):
    """Thread ids fan out to their runs. Mark unread on a thread re-opens only its newest run."""
    if not any(i.startswith("t-") for i in ids):
        return ids
    want = {i for i in ids if i.startswith("t-")}
    members = {}
    for m in _messages():                              # newest first
        tags = m.get("tags") or []
        it = {"source": "cron" if "cron" in tags else "agent", "title": m.get("title", ""), "thread_title": m.get("thread_title", m.get("title", ""))}
        key = _thread_key(it)
        if key and _thread_id(key) in want:
            members.setdefault(_thread_id(key), []).append(m["id"])
    out = [i for i in ids if not i.startswith("t-")]
    only_newest = set(marks) == {"read"} and marks["read"] is False
    for t, run_ids in members.items():
        out += run_ids[:1] if only_newest else run_ids
    return out


def inbox_mark(ids, **marks):
    ids = [str(i)[:40] for i in (ids or [])][:500]
    keep = {k: bool(v) for k, v in marks.items() if k in ("read", "flag", "archived") and v is not None}
    if not ids or not keep:
        raise ValueError("give ids and at least one of read, flag, archived")
    ids = _expand_ids(ids, keep)[:2000]
    with _lock:
        st = _load_state()
        for i in ids:
            cur = st.get(i, {})
            cur.update(keep)
            if keep.get("archived"):
                cur["read"] = True
            st[i] = cur
        live = {m["id"] for m in _messages()}   # forget marks for messages ntfy has expired
        st = {k: v for k, v in st.items() if k in live}
        _save_state(st)
    return {"ok": True, "changed": len(ids)}


@tool("notifications_inbox", "Read the owner's notification inbox (agent notes and scheduled-job results) with what they "
      "have read, flagged or archived. Use it to answer 'what came in', 'what failed overnight' or 'what did I flag'.",
      {"view": {"type": "string", "enum": ["inbox", "flagged", "archive", "all"], "description": "default inbox"},
       "unread_only": {"type": "boolean", "description": "only unread items"},
       "limit": {"type": "integer", "description": "max items, default 30"}})
def notifications_inbox(view="inbox", unread_only=False, limit=30):
    _box_only()
    d = inbox_items(view, 500)
    items = [i for i in d["items"] if not (unread_only and i["read"])][:max(1, min(int(limit or 30), 200))]
    for i in items:
        i["time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(i["time"]))
        i["message"] = i["message"][:600]
        if "runs" in i:
            i["runs"] = [{"id": r["id"], "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["time"])),
                          "failed": r["priority"] >= 4, "read": r["read"]} for r in i["runs"][:20]]
    return {"counts": d["counts"], "items": items}


@tool("notifications_mark", "Mark inbox items read, unread, flagged or archived for the owner, when they ask.",
      {"ids": {"type": "array", "items": {"type": "string"}, "description": "item ids from notifications_inbox"},
       "read": {"type": "boolean"}, "flag": {"type": "boolean"}, "archived": {"type": "boolean"}},
      ["ids"])
def notifications_mark(ids, read=None, flag=None, archived=None):
    _box_only()
    return inbox_mark(ids, read=read, flag=flag, archived=archived)


def _json(code, obj):
    return code, "application/json", json.dumps(obj).encode()


# ---- Notification pictures: MEDIA:/abs/path lines in a body show as thumbnails ----
# Read-only, image files only, under an explicit allowlist of roots (mounted read-only at the same paths).
# Every path is resolved with realpath first, so ../ and symlinks that leave a root are refused.
MEDIA_ROOTS = [os.path.realpath(r) for r in os.environ.get("INBOX_MEDIA_ROOTS", "").split(":") if r.strip().startswith("/")]
MEDIA_MAX = int(os.environ.get("INBOX_MEDIA_MAX", str(8 << 20)))
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


def _sniff(head):
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return ""


def media_file(raw):
    """(status, content_type, bytes) for one allowed image; never a listing, never a non-image."""
    raw = str(raw or "")
    if not raw.startswith("/") or len(raw) > 1000 or "\x00" in raw:
        return _json(400, {"error": "bad path"})
    real = os.path.realpath(raw)
    if not any(real.startswith(root.rstrip("/") + "/") for root in MEDIA_ROOTS):
        return _json(404, {"error": "not found"})
    want = MEDIA_TYPES.get(os.path.splitext(real)[1].lower())
    if not want:
        return _json(404, {"error": "not found"})
    try:
        fd = os.open(real, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return _json(404, {"error": "not found"})
    try:
        st = os.fstat(fd)
        if (st.st_mode & 0o170000) != 0o100000:  # regular files only
            return _json(404, {"error": "not found"})
        if st.st_size > MEDIA_MAX:
            return _json(413, {"error": "image too large"})
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(MEDIA_MAX + 1)
    finally:
        os.close(fd)
    if len(data) > MEDIA_MAX:
        return _json(413, {"error": "image too large"})
    if _sniff(data[:16]) != want:
        return _json(415, {"error": "not an image"})
    return 200, want, data


@route(INBOX)
def inbox_route(method, path, query, headers, body):
    sub = path[len(INBOX):] if path.startswith(INBOX) else ""
    q = urllib.parse.parse_qs(query)
    if method == "GET" and sub in ("", "index.html"):
        html = MINIMAL_HTML if (q.get("layout") or [""])[0] == "minimal" or LAYOUT == "cards" else PAGE_HTML.replace('<script defer', '<script src="layout.js"></script><script defer')
        return 200, "text/html; charset=utf-8", html.encode()
    if method == "GET" and sub in MINIMAL_ASSETS:
        mime, content = MINIMAL_ASSETS[sub]
        return 200, mime + "; charset=utf-8", content.encode()
    if method == "GET" and sub == "inbox.js":
        return 200, "text/javascript; charset=utf-8", PAGE_JS.encode()
    if method == "GET" and sub == "inbox.css":
        return 200, "text/css; charset=utf-8", PAGE_CSS.encode()
    if method == "GET" and sub == "api/items":
        return _json(200, inbox_items((q.get("view") or ["inbox"])[0], (q.get("limit") or [200])[0]))
    if method == "GET" and sub == "api/count":
        return _json(200, inbox_items("inbox", 1)["counts"])
    if method == "POST" and sub == "api/mark":
        # JSON only: a form on another site cannot send this without a CORS preflight, which is never granted.
        if not headers.get("content-type", "").startswith("application/json"):
            return _json(415, {"error": "send application/json"})
        d = json.loads(body or b"{}")
        return _json(200, inbox_mark(d.get("ids"), read=d.get("read"), flag=d.get("flag"), archived=d.get("archived")))
    if method == "GET" and sub.startswith("media/"):
        # /notifications/media/<absolute path>: the path keeps its image extension so the shell's viewer opens it.
        return media_file("/" + urllib.parse.unquote(sub[len("media/"):]))
    if method == "GET" and sub == "healthz":
        return _json(200, {"ok": True, "view_revision": MINIMAL_REVISION})
    return _json(404, {"error": "not found"})


PAGE_HTML = """<!doctype html>
<html lang="en" data-app="notifications">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>Notifications</title>
<script>try { if (window.parent !== window) document.documentElement.classList.add('wz-hosted'); } catch (e) { document.documentElement.classList.add('wz-hosted'); }</script>
<link rel="stylesheet" href="inbox.css">
<script src="/wizard-os-home/core/app-client.js" onerror="this.remove()"></script>
<script defer src="inbox.js"></script>
</head>
<body>
<div class="wz-app">
  <header class="wz-head"><h1>Notifications</h1></header>
  <nav class="nt-tabs" role="tablist">
    <button role="tab" data-view="inbox" aria-selected="true">Inbox <small id="c-inbox"></small></button>
    <button role="tab" data-view="flagged" aria-selected="false">Flagged <small id="c-flagged"></small></button>
    <button role="tab" data-view="archive" aria-selected="false">Archive</button>
    <button class="nt-allread" id="allread" type="button">Mark all read</button>
  </nav>
  <main class="wz-scroll" id="scroll"><ul class="wz-list" id="list"></ul>
    <div class="wz-empty" id="empty" hidden><div class="wz-tile">&#128276;</div><strong>Nothing here</strong><p id="empty-p">Scheduled jobs and your Wizard's notes land here.</p></div>
  </main>
  <p class="wz-status" id="status" role="status" aria-live="polite"></p>
</div>
</body>
</html>
"""

PAGE_CSS = """
:root { --ink: var(--wz-text, #f3efff); --ink-2: var(--wz-text-secondary, #b9b0d8); --ink-3: var(--wz-text-muted, #a59abf);
  --line-2: var(--wz-border, rgba(205,190,255,.24)); --line: color-mix(in srgb, var(--line-2) 55%, transparent);
  --fill: color-mix(in srgb, var(--ink) 6%, transparent); --fill-2: color-mix(in srgb, var(--ink) 10%, transparent);
  --accent: var(--wz-accent, #b99bff); --on-accent: var(--wz-on-accent, #160b32); --canvas: var(--wz-canvas, #05030c);
  --danger: #ff8f8f; --amber: #ffbe55; --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color-scheme: dark; }
html[data-color-scheme="light"] { color-scheme: light; --danger: #b42318; --amber: #a15c00; }
*, *::before, *::after { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { font: 15px/1.4 var(--font); color: var(--ink); background: transparent; -webkit-font-smoothing: antialiased; overscroll-behavior: none; }
html:not(.wz-hosted) body { background: var(--canvas); }
button { font: inherit; color: inherit; cursor: pointer; border: 0; background: none; padding: 0; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.wz-app { display: flex; flex-direction: column; height: 100%; max-width: 680px; margin: 0 auto; }
.wz-head { flex: none; padding: 18px 18px 4px; }
.wz-head h1 { margin: 0; font-size: 26px; font-weight: 750; letter-spacing: -.02em; }
html.wz-hosted .wz-head { display: none; }
.nt-tabs { flex: none; display: flex; gap: 6px; align-items: center; padding: 10px 10px 6px; overflow-x: auto; scrollbar-width: none; }
.nt-tabs::-webkit-scrollbar { display: none; }
.nt-tabs [role=tab] { flex: none; white-space: nowrap; height: 32px; padding: 0 12px; border-radius: 99px; font-size: 14px; font-weight: 600; color: var(--ink-2); background: var(--fill); }
.nt-tabs [role=tab][aria-selected=true] { color: var(--on-accent); background: var(--accent); }
.nt-tabs small { font-size: 12px; font-weight: 700; opacity: .8; margin-left: 2px; }
.nt-allread { flex: none; white-space: nowrap; margin-left: auto; font-size: 13px; color: var(--ink-3); padding: 6px 8px; border-radius: 8px; }
.wz-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 2px 10px 16px; scrollbar-width: none; }
.wz-list { list-style: none; margin: 0; padding: 0; }
.nt-item { border-radius: 14px; }
.nt-item + .nt-item { margin-top: 2px; }
.nt-row { display: flex; gap: 12px; width: 100%; padding: 10px 8px; border-radius: 14px; text-align: left; align-items: flex-start; }
.nt-row:active { background: var(--fill); }
@media (hover: hover) { .nt-row:hover { background: var(--fill); } }
.nt-dot { flex: none; width: 8px; height: 8px; margin-top: 7px; border-radius: 50%; background: transparent; }
.nt-item.unread .nt-dot { background: var(--accent); }
.nt-item.unread.p4 .nt-dot, .nt-item.unread.p5 .nt-dot { background: var(--danger); }
.nt-main { flex: 1; min-width: 0; }
.nt-top { display: flex; gap: 8px; align-items: baseline; }
.nt-top b { flex: 1; min-width: 0; font-size: 15px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nt-item:not(.unread) .nt-top b { font-weight: 500; color: var(--ink-2); }
.nt-top time { flex: none; font-size: 12px; color: var(--ink-3); }
.nt-src { display: inline-block; font-weight: 800; color: var(--danger); margin-right: 5px; }
.nt-flag { color: var(--amber); font-size: 13px; }
.nt-msg { margin: 2px 0 0; font-size: 13.5px; color: var(--ink-3); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.nt-item.open .nt-msg { display: block; white-space: pre-wrap; color: var(--ink-2); -webkit-line-clamp: none; }
.nt-acts { display: none; gap: 6px; padding: 0 8px 10px 28px; flex-wrap: wrap; }
.nt-item.open .nt-acts { display: flex; }
.nt-acts button, .nt-acts a { height: 30px; padding: 0 12px; border-radius: 99px; font-size: 13px; font-weight: 600; background: var(--fill); color: var(--ink); display: inline-flex; align-items: center; text-decoration: none; }
.nt-acts .on { color: var(--amber); }
.wz-empty { padding: 44px 24px; text-align: center; color: var(--ink-3); }
.wz-tile { margin: 0 auto 14px; width: 52px; height: 52px; border-radius: 18px; display: grid; place-items: center; font-size: 24px; background: var(--fill); }
.wz-empty strong { display: block; margin-bottom: 4px; font-size: 16px; color: var(--ink); }
.wz-empty p { margin: 0 auto; max-width: 32ch; font-size: 14px; }
.wz-status { flex: none; margin: 0; padding: 0 18px 6px; font-size: 13px; color: var(--danger); }
.wz-status:empty { padding: 0; }
html[data-skin="signal"] .nt-row, html[data-skin="signal"] .nt-tabs [role=tab], html[data-skin="signal"] .nt-acts button { border-radius: 2px; }
"""

PAGE_JS = r"""'use strict';
(() => {
  const el = id => document.getElementById(id);
  let view = 'inbox', items = [], open = '';
  const ago = t => { const s = Date.now() / 1000 - t; if (s < 60) return 'now'; if (s < 3600) return Math.floor(s / 60) + 'm';
    if (s < 86400) return Math.floor(s / 3600) + 'h'; const d = new Date(t * 1000); return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }); };
  const say = t => { el('status').textContent = t || ''; };
  async function api(path, data) {
    const r = await fetch('api/' + path, data ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : { cache: 'no-store' });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(b.error || 'Notifications are not reachable right now.');
    return b;
  }
  const plain = t => String(t || '').replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, (m, t) => t).replace(/^\s*[-*]\s+/gm, '').replace(/\*\*|`/g, '').trim();
  function node(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function row(it) {
    const li = node('li', 'nt-item p' + it.priority + (it.read ? '' : ' unread') + (open === it.id ? ' open' : ''));
    li.dataset.id = it.id;
    const b = node('button', 'nt-row'); b.type = 'button'; b.dataset.act = 'toggle'; b.setAttribute('aria-expanded', String(open === it.id));
    b.appendChild(node('span', 'nt-dot'));
    const m = node('span', 'nt-main'), top = node('span', 'nt-top');
    const title = node('b'); if (it.priority >= 4) title.appendChild(node('span', 'nt-src', '!'));
    title.appendChild(document.createTextNode((it.title || it.message.split('\n')[0]).replace(/^Failed: /, '')));
    top.appendChild(title); if (it.flag) top.appendChild(node('span', 'nt-flag', '\u2691'));
    const tm = node('time', '', ago(it.time)); tm.dateTime = new Date(it.time * 1000).toISOString(); top.appendChild(tm);
    m.appendChild(top); m.appendChild(node('p', 'nt-msg', plain(it.message))); b.appendChild(m); li.appendChild(b);
    const acts = node('div', 'nt-acts');
    // Raw input is escaped by textContent and stays collapsed until requested.
    if (it.details && open === it.id) { const d = node('details'); const p = node('pre', '', it.details); p.style.whiteSpace = 'pre-wrap'; p.style.overflowWrap = 'anywhere'; d.append(node('summary', '', 'Details'), p); li.appendChild(d); }
    const btn = (act, label, on) => { const x = node('button', on ? 'on' : '', label); x.type = 'button'; x.dataset.act = act; acts.appendChild(x); };
    btn('flag', it.flag ? '\u2691 Flagged' : '\u2690 Flag', it.flag);
    btn('read', it.read ? 'Mark unread' : 'Mark read');
    btn('archive', it.archived ? 'Move to inbox' : 'Archive');
    if (/^https:\/\//.test(it.link || '')) { const a = node('a', '', 'Open'); a.href = it.link; a.target = '_blank'; a.rel = 'noopener'; acts.appendChild(a); }
    li.appendChild(acts);
    return li;
  }
  function paint(d) {
    items = d.items; const c = d.counts;
    el('c-inbox').textContent = c.unread ? String(c.unread) : '';
    el('c-flagged').textContent = c.flagged ? String(c.flagged) : '';
    el('list').replaceChildren(...items.map(row));
    el('empty').hidden = items.length > 0;
    el('empty-p').textContent = view === 'flagged' ? 'Flag a notification to keep it here.' : view === 'archive' ? 'Archived notifications stay here for 30 days.' : 'Scheduled jobs and your Wizard\u2019s notes land here.';
    el('allread').hidden = view !== 'inbox' || !c.unread;
    try { parent !== window && parent.postMessage({ type: 'wizard:notifications:count', unread: c.unread, needs: c.needs }, location.origin); } catch (e) {}
  }
  async function load() { try { paint(await api('items?view=' + view)); say(''); } catch (e) { say(e.message); } }
  async function mark(ids, marks) { try { await api('mark', Object.assign({ ids }, marks)); await load(); } catch (e) { say(e.message); } }
  document.addEventListener('click', e => {
    const tab = e.target.closest('[role=tab]');
    if (tab) { view = tab.dataset.view; open = ''; document.querySelectorAll('[role=tab]').forEach(t => t.setAttribute('aria-selected', String(t === tab))); el('scroll').scrollTop = 0; return load(); }
    if (e.target.closest('#allread')) return mark(items.filter(i => !i.read).map(i => i.id), { read: true });
    const a = e.target.closest('[data-act]'); const li = e.target.closest('.nt-item'); if (!a || !li) return;
    const it = items.find(i => i.id === li.dataset.id); if (!it) return;
    if (a.dataset.act === 'toggle') { open = open === it.id ? '' : it.id; if (open && !it.read) return mark([it.id], { read: true }); return paint({ items, counts: countsNow() }); }
    if (a.dataset.act === 'flag') return mark([it.id], { flag: !it.flag });
    if (a.dataset.act === 'read') return mark([it.id], { read: !it.read });
    if (a.dataset.act === 'archive') { open = ''; return mark([it.id], { archived: !it.archived }); }
  });
  function countsNow() { return { unread: Number(el('c-inbox').textContent || 0), flagged: Number(el('c-flagged').textContent || 0), needs: 0 }; }
  // Back inside the shell: an open item closes first.
  window.__wizardCloseOverlay = () => { if (!open) return false; open = ''; paint({ items, counts: countsNow() }); return true; };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  load();
})();
"""
