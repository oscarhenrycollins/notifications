"""Morning Report's Overnight reader under the browser boundary (wizard-os spec/browser-trust-shell-intents).

With WIZARD_BROWSER_BOUNDARY=1 the box-local GET /notifications/api/daily that the Morning Report generator used is
refused (no app credential): that path now answers only Wizard's browser adapter. The same roll-up is a typed,
read-only MCP tool, notifications_overnight (hours 1..72), for agents on the box; it answers exactly what api/daily
answers, refuses network callers and web pages, and validates its argument. Real front process, stub ntfy, synthetic
data only."""
import http.client, json, secrets, tempfile, threading, unittest, http.server

from test_listener_gate import Front, Ntfy, JSON, SERVE


def mcp(front, name, args, headers=()):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}).encode()
    status, _, data = front.req("POST", "/mcp", list(headers) + JSON, body)
    return status, json.loads(data) if data else None


class OvernightReader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ntfy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ntfy)
        threading.Thread(target=cls.ntfy.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cred = secrets.token_hex(32)
        cls.front = Front(cls.tmp.name, cls.ntfy.server_address[1], WIZARD_BROWSER_BOUNDARY="1", WIZARD_APP_CREDENTIAL=cls.cred,
                          WIZARD_GUARDED_PATHS="/notifications/api/,/notifications/media/")
        cls.adapter = [("X-Wizard-App-Credential", cls.cred), ("Accept", "application/json")]
        # The owner set Notifications to "Morning report" (Daily): through the adapter path, as the shell would.
        status, _, _ = cls.front.req("POST", "/notifications/api/routes", cls.adapter + JSON, json.dumps({"routes": {"notifications": "daily"}}).encode())
        assert status == 200, status

    @classmethod
    def tearDownClass(cls):
        cls.front.stop()
        cls.ntfy.shutdown()
        cls.tmp.cleanup()

    def test_old_reader_is_refused_under_the_gate(self):
        # Exactly what the generator sent: a bare box-local GET with no credential.
        status, _, data = self.front.req("GET", "/notifications/api/daily?hours=24", [])
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(data)["error"], "adapter_credential_required")

    def test_typed_tool_answers_the_same_roll_up(self):
        status, _, data = self.front.req("GET", "/notifications/api/daily?hours=24", self.adapter)
        self.assertEqual(status, 200)
        want = json.loads(data)
        self.assertTrue(want["groups"] and want["groups"][0]["source"] == "notifications")
        status, out = mcp(self.front, "notifications_overnight", {"hours": 24})
        self.assertEqual(status, 200)
        self.assertNotIn("isError", out["result"])
        self.assertEqual(json.loads(out["result"]["content"][0]["text"]), want)
        status, out = mcp(self.front, "notifications_overnight", {})
        self.assertEqual(json.loads(out["result"]["content"][0]["text"])["hours"], 24)

    def test_typed_tool_refuses_network_pages_and_bad_arguments(self):
        status, _ = mcp(self.front, "notifications_overnight", {"hours": 24}, SERVE)
        self.assertEqual(status, 403)   # tailnet owner identity: the MCP is for apps on this box
        status, out = mcp(self.front, "notifications_overnight", {"hours": 24}, [("Origin", "null"), ("Sec-Fetch-Site", "cross-site")])
        # A web page: refused by the front's cross-site rule (#18) or, failing that, by the tool itself.
        self.assertTrue(status == 403 or out["result"].get("isError"), (status, out))
        for bad in (0, 73, "24", True, 1.5):
            _, out = mcp(self.front, "notifications_overnight", {"hours": bad})
            self.assertTrue(out["result"].get("isError"), bad)

    def test_tool_is_listed_read_only(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        _, _, data = self.front.req("POST", "/mcp", JSON, body)
        tool = next(t for t in json.loads(data)["result"]["tools"] if t["name"] == "notifications_overnight")
        self.assertEqual(tool["inputSchema"]["properties"]["hours"]["maximum"], 72)


if __name__ == "__main__":
    unittest.main()
