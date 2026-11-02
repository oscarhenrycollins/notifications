"""Browser boundary on the Notifications listener (wizard-os #386): with WIZARD_BROWSER_BOUNDARY=1 the owner-data
paths (/notifications/api/*, /notifications/media/*) answer only a box-local caller holding this installation's app
credential. Network owner identity (Tailscale Serve headers), frames/pages (Origin, Sec-Fetch-*, Cookie), simple
form posts and preflights are refused by the real front process; the view's static files still load; flag off keeps
today's behaviour. Synthetic data only, over a stub ntfy."""
import http.client, http.server, json, os, secrets, socket, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = int(time.time())
MESSAGES = [{"id": "SynthGate0001", "time": NOW - 60, "event": "message", "topic": "wizard", "title": "Synthetic", "message": "One."},
            {"id": "SynthGate0002", "time": NOW - 30, "event": "message", "topic": "wizard", "title": "Synthetic 2", "message": "Two."}]


class Ntfy(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        topic = self.path.split("?")[0].strip("/").split("/")[0]
        body = "".join(json.dumps(m) + "\n" for m in MESSAGES if m["topic"] == topic).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def front_source():
    src = "\n\n".join((ROOT / "mcp" / f).read_text() for f in ("front.py", "tools.py", "minimal_assets.py", "relay.py"))
    return src + '\n\nif __name__ == "__main__":\n    asyncio.run(main())\n'


class Front:
    def __init__(self, tmp, ntfy_port, **env):
        self.port, self.tmp = free_port(), tmp
        self.state = Path(tmp) / "inbox.json"
        f = Path(tmp) / "front.py"
        f.write_text(front_source())
        e = {k: v for k, v in os.environ.items() if not k.startswith("WIZARD_")}
        e.update(WIZARD_APP="notifications", FRONT_PORT=str(self.port), UPSTREAM_HOST="127.0.0.1", UPSTREAM_PORT=str(ntfy_port),
                 INBOX_TOPICS="wizard,cron", INBOX_STATE=str(self.state), CRON_RELAY="off", PYTHONDONTWRITEBYTECODE="1", **env)
        self.proc = subprocess.Popen([sys.executable, str(f)], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            if self.proc.poll() is not None:
                break
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.05)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        return self.proc.communicate(timeout=10)[0].decode()

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", "box.example.ts.net")
        for k, v in (headers or []):
            c.putheader(k, v)
        if body is not None:
            c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse()
        data, hdrs = r.read(), {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, hdrs, data

    def marks(self):
        try:
            return json.loads(self.state.read_text())
        except (OSError, ValueError):
            return {}


SERVE = [("Tailscale-User-Login", "owner@example.com"), ("Tailscale-User-Name", "Owner"), ("X-Forwarded-For", "100.64.0.7")]
FRAME = [("Origin", "null"), ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "cors"), ("Sec-Fetch-Dest", "empty")]
JSON = [("Content-Type", "application/json")]
MARK = json.dumps({"ids": ["SynthGate0001"], "read": True}).encode()
HOSTILE = json.dumps({"ids": ["SynthGate0002"], "read": True}).encode()   # never marked by the adapter test


class GateOn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ntfy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ntfy)
        threading.Thread(target=cls.ntfy.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cred = secrets.token_hex(32)
        cls.front = Front(cls.tmp.name, cls.ntfy.server_address[1], WIZARD_BROWSER_BOUNDARY="1", WIZARD_APP_CREDENTIAL=cls.cred,
                          WIZARD_GUARDED_PATHS="/notifications/api/,/notifications/media/")
        cls.adapter = [("X-Wizard-App-Credential", cls.cred), ("Accept", "application/json"), ("Sec-Fetch-Mode", "cors")]

    @classmethod
    def tearDownClass(cls):
        cls.front.stop()
        cls.ntfy.shutdown()
        cls.tmp.cleanup()

    def refused(self, method, path, headers, body=None, want=(401, 403)):
        status, hdrs, data = self.front.req(method, path, headers, body)
        self.assertIn(status, want, (method, path, headers, status, data))
        self.assertFalse(any(k.startswith("access-control-") for k in hdrs), hdrs)   # never any CORS grant
        self.assertNotIn(b"SynthGate", data)
        return json.loads(data)["error"]

    def test_network_owner_identity_is_not_authority(self):
        self.assertEqual(self.refused("GET", "/notifications/api/items?view=inbox&limit=50", SERVE), "network_identity_refused")
        self.assertEqual(self.refused("GET", "/notifications/api/count", SERVE), "network_identity_refused")
        self.assertEqual(self.refused("POST", "/notifications/api/mark", SERVE + JSON, HOSTILE), "network_identity_refused")
        self.assertEqual(self.refused("GET", "/notifications/api/items", []), "adapter_credential_required")   # bare box caller
        self.assertNotIn("SynthGate0002", self.front.marks())

    def test_frames_reads_writes_forms_media_and_preflight_refused(self):
        self.assertEqual(self.refused("GET", "/notifications/api/items?view=all&limit=500", SERVE + FRAME), "browser_request_refused")
        self.assertEqual(self.refused("POST", "/notifications/api/mark", SERVE + FRAME + JSON, HOSTILE), "browser_request_refused")
        form = [("Origin", "null"), ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "iframe"),
                ("Content-Type", "application/x-www-form-urlencoded")]
        self.assertEqual(self.refused("POST", "/notifications/api/mark", SERVE + form, b"ids=SynthGate0002&read=true"), "browser_request_refused")
        text = [("Origin", "null"), ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "no-cors"), ("Content-Type", "text/plain")]
        self.assertEqual(self.refused("POST", "/notifications/api/sources", SERVE + text, b'{"app":"x","chats":false}'), "browser_request_refused")
        img = [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "no-cors"), ("Sec-Fetch-Dest", "image")]
        self.assertEqual(self.refused("GET", "/notifications/media/tmp/x.png", SERVE + img), "browser_request_refused")
        pre = [("Origin", "null"), ("Access-Control-Request-Method", "POST"), ("Access-Control-Request-Headers", "content-type")]
        for path in ("/notifications/api/mark", "/notifications/", "/notifications/minimal.js"):
            self.assertEqual(self.refused("OPTIONS", path, SERVE + pre), "preflight_refused")
        # Same-origin shell documents are browsers too: a stolen credential does not help a page or frame.
        same = [("Origin", "https://box.example.ts.net"), ("Sec-Fetch-Site", "same-origin"), ("Cookie", "a=b")]
        self.assertEqual(self.refused("GET", "/notifications/api/items", same + [("X-Wizard-App-Credential", self.cred)]), "browser_request_refused")
        self.assertNotIn("SynthGate0002", self.front.marks())

    def test_wrong_duplicate_or_stale_credentials_refused(self):
        for creds in ([("X-Wizard-App-Credential", secrets.token_hex(32))],
                      [("X-Wizard-App-Credential", self.cred), ("X-Wizard-App-Credential", self.cred)],
                      [("X-Wizard-App-Credential", self.cred[:-1])],
                      [("X-Wizard-Shell-Token", self.cred)], [("Authorization", "Bearer " + self.cred)]):
            self.assertEqual(self.refused("GET", "/notifications/api/items", creds), "adapter_credential_required")
        self.assertEqual(self.refused("GET", "/notifications/api/items", self.adapter + SERVE), "network_identity_refused")

    def test_adapter_call_reads_and_writes(self):
        status, _, data = self.front.req("GET", "/notifications/api/items?view=inbox&limit=50", self.adapter)
        self.assertEqual(status, 200, data)
        self.assertEqual({i["id"] for i in json.loads(data)["items"]}, {"SynthGate0001", "SynthGate0002"})
        status, _, data = self.front.req("POST", "/notifications/api/mark", self.adapter + JSON, MARK)
        self.assertEqual(status, 200, data)
        self.assertTrue(self.front.marks()["SynthGate0001"]["read"])
        self.assertNotIn("SynthGate0002", self.front.marks())

    def test_view_static_files_still_load_but_never_writes(self):
        for path in ("/notifications/", "/notifications/minimal.js", "/notifications/healthz"):
            status, hdrs, _ = self.front.req("GET", path, SERVE + [("Sec-Fetch-Dest", "iframe")])
            self.assertEqual(status, 200, path)
            self.assertFalse(any(k.startswith("access-control-") for k in hdrs))


class GateOff(unittest.TestCase):
    """Flag off: unchanged (today the network identity alone reads and writes; this is what the flag closes)."""

    def test_off_is_unchanged_and_on_fails_closed_without_credential(self):
        ntfy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ntfy)
        threading.Thread(target=ntfy.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as tmp:
            front = Front(tmp, ntfy.server_address[1])
            try:
                status, _, data = front.req("GET", "/notifications/api/items?view=inbox&limit=50", SERVE)
                self.assertEqual(status, 200)
                self.assertIn(b"SynthGate0001", data)
                status, _, _ = front.req("POST", "/notifications/api/mark", SERVE + JSON, MARK)
                self.assertEqual(status, 200)
            finally:
                front.stop()
            for env in ({"WIZARD_BROWSER_BOUNDARY": "1", "WIZARD_GUARDED_PATHS": "/notifications/api/"},
                        {"WIZARD_BROWSER_BOUNDARY": "1", "WIZARD_APP_CREDENTIAL": "x" * 64, "WIZARD_GUARDED_PATHS": "/notifications/api/"},
                        {"WIZARD_BROWSER_BOUNDARY": "1", "WIZARD_APP_CREDENTIAL": secrets.token_hex(32)}):
                bad = Front(tmp, ntfy.server_address[1], **env)
                bad.proc.wait(timeout=10)
                self.assertNotEqual(bad.proc.returncode, 0, env)
                self.assertIn("WIZARD_BROWSER_BOUNDARY=1 needs", bad.stop())
        ntfy.shutdown()


if __name__ == "__main__":
    unittest.main()
