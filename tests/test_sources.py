"""Inbox sources: per app, whether its items show in the Chats inbox. Stored on the box (one JSON file, written
atomically), so every device agrees. An app that is not listed shows in Chats; "phone" is reserved for later."""
import json, os, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(state):
    os.environ["INBOX_STATE"] = state
    os.environ.pop("INBOX_SOURCES", None)
    src = (ROOT / "mcp/front.py").read_text() + "\n" + (ROOT / "mcp/tools.py").read_text() + "\n" + (ROOT / "mcp/minimal_assets.py").read_text()
    ns = {"__name__": "notif_test"}
    exec(compile(src, "front+tools", "exec"), ns)
    ns["_messages"] = lambda since="all": [{"id": "ntfy1", "time": 1}]
    return ns


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ns = load(str(self.dir / "inbox.json"))
        self.route = self.ns["inbox_route"]

    def tearDown(self):
        self.tmp.cleanup()

    def get(self):
        code, ctype, raw = self.route("GET", "/notifications/api/sources", "", {}, b"")
        self.assertEqual(code, 200)
        self.assertEqual(ctype, "application/json")
        return json.loads(raw)

    def post(self, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        code, _, out = self.route("POST", "/notifications/api/sources", "", {"content-type": ctype}, raw)
        return code, json.loads(out)

    def test_empty_means_every_app_shows(self):
        self.assertEqual(self.get(), {"sources": {}})
        self.assertFalse((self.dir / "sources.json").exists(), "a read never writes")

    def test_set_and_read_back_with_phone_reserved(self):
        code, out = self.post({"app": "mail", "chats": False})
        self.assertEqual(code, 200)
        self.assertEqual(out["sources"], {"mail": {"chats": False, "phone": True}})
        self.assertEqual(self.get(), {"sources": {"mail": {"chats": False, "phone": True}}})
        self.post({"app": "messenger", "chats": False})
        self.post({"app": "mail", "chats": True})
        self.assertEqual(self.get()["sources"], {"mail": {"chats": True, "phone": True}, "messenger": {"chats": False, "phone": True}})
        # Kept beside the marks, never inside them (marks pruning would drop it), and survives a fresh process.
        self.assertNotIn("mail", json.loads((self.dir / "sources.json").read_text()).get("marks", {}))
        self.assertEqual(load(str(self.dir / "inbox.json"))["inbox_sources"](), self.get())

    def test_marks_pruning_leaves_sources_alone(self):
        self.post({"app": "mail", "chats": False})
        code, _, _ = self.route("POST", "/notifications/api/mark", "", {"content-type": "application/json"}, json.dumps({"ids": ["ntfy1"], "read": True}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(self.get()["sources"]["mail"]["chats"], False)

    def test_json_only_and_bounded(self):
        self.assertEqual(self.post(b"app=mail&chats=false", "application/x-www-form-urlencoded")[0], 415)
        self.assertEqual(self.post(b"{not json")[0], 400)
        self.assertEqual(self.post([1, 2])[0], 400)
        self.assertEqual(self.post({"app": "mail", "chats": "no"})[0], 400, "only real booleans")
        self.assertEqual(self.post({"app": "mail"})[0], 400, "something to change")
        for bad in ("", "Mail", "../x", "a" * 65, 7, None, "-x"):
            self.assertEqual(self.post({"app": bad, "chats": False})[0], 400, repr(bad))
        self.assertEqual(self.post({"app": "mail", "chats": False, "pad": "x" * 5000})[0], 413)
        self.assertEqual(self.get(), {"sources": {}})

    def test_app_count_is_capped(self):
        for i in range(self.ns["SOURCES_MAX_APPS"]):
            self.assertEqual(self.post({"app": "app-%d" % i, "chats": False})[0], 200)
        self.assertEqual(self.post({"app": "one-more", "chats": False})[0], 400)
        self.assertEqual(self.post({"app": "app-3", "chats": True})[0], 200, "existing apps can still change")

    def test_atomic_write_leaves_no_temp_files_and_survives_a_failed_write(self):
        self.post({"app": "mail", "chats": False})
        real = self.ns["os"].replace
        def boom(*a):
            raise OSError("disk full")
        self.ns["os"].replace = boom
        try:
            with self.assertRaises(OSError):
                self.ns["set_inbox_source"]("messenger", chats=False)
        finally:
            self.ns["os"].replace = real
        self.assertEqual(self.get()["sources"], {"mail": {"chats": False, "phone": True}}, "the old file is intact")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["sources.json"])

    def test_corrupt_file_reads_as_defaults(self):
        (self.dir / "sources.json").write_text("{oops")
        self.assertEqual(self.get(), {"sources": {}})
        (self.dir / "sources.json").write_text(json.dumps({"sources": {"Bad!": {"chats": False}, "mail": {"chats": False}, "x": 3}}))
        self.assertEqual(self.get(), {"sources": {"mail": {"chats": False, "phone": True}}})


if __name__ == "__main__":
    unittest.main()
