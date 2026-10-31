"""App inbox feed marks: Done / Snooze for items an app lists (ids "a-<app>-<hash>") are kept with the activity time
they cover, survive ntfy pruning, and come back through /notifications/api/marks."""
import json, os, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(state):
    os.environ["INBOX_STATE"] = state
    src = (ROOT / "mcp/front.py").read_text() + "\n" + (ROOT / "mcp/tools.py").read_text() + "\n" + (ROOT / "mcp/minimal_assets.py").read_text()
    ns = {"__name__": "notif_test"}
    exec(compile(src, "front+tools", "exec"), ns)
    ns["_messages"] = lambda since="all": [{"id": "ntfy1", "time": 1}]
    return ns


class AppMarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ns = load(str(Path(self.tmp.name) / "inbox.json"))
        self.route = self.ns["inbox_route"]

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, body):
        return self.route("POST", "/notifications/api/mark", "", {"content-type": "application/json"}, json.dumps(body).encode())

    def marks(self):
        code, _, raw = self.route("GET", "/notifications/api/marks", "", {}, b"")
        self.assertEqual(code, 200)
        return json.loads(raw)["marks"]

    def test_app_mark_keeps_time_and_survives_pruning(self):
        aid = "a-messenger-0123456789abcdef"
        t = float(int(time.time()) - 100)
        code, _, _ = self.post({"ids": [aid, "ntfy1"], "archived": True, "at": {aid: t}})
        self.assertEqual(code, 200)
        self.assertEqual(self.marks(), {aid: {"read": True, "archived": True, "at": t}})

    def test_recent_mark_kept_and_not_ntfy_ids(self):
        aid = "a-wisdom-ffff"
        now = time.time()
        self.post({"ids": [aid], "read": True, "at": {aid: now - 10}})
        m = self.marks()
        self.assertIn(aid, m)
        self.assertTrue(m[aid]["read"] and not m[aid]["archived"])
        self.assertNotIn("ntfy1", m)
        # Unread again (restore) keeps the entry, cleared.
        self.post({"ids": [aid], "read": False, "archived": False, "at": {aid: now}})
        self.assertEqual(self.marks()[aid]["read"], False)

    def test_old_app_marks_drop_and_bad_ids_stay_short(self):
        old = "a-x-1"
        self.post({"ids": [old], "read": True, "at": {old: time.time() - 90 * 86400}})
        self.assertNotIn(old, self.marks())
        self.post({"ids": ["a-<script>"], "read": True})
        self.assertNotIn("a-<script>", self.marks())

    def test_future_time_is_clamped(self):
        aid = "a-m-2"
        self.post({"ids": [aid], "read": True, "at": {aid: time.time() + 10 ** 6}})
        self.assertLessEqual(self.marks()[aid]["at"], time.time() + 61)


if __name__ == "__main__":
    unittest.main()
