"""Marks must survive a partial ntfy read. ntfy can end a poll with HTTP 200 and an error line part-way through
(its daily bandwidth limit); pruning against that half list once brought weeks of read items back as unread."""
import json, os, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(state):
    os.environ["INBOX_STATE"] = state
    os.environ["INBOX_TOPICS"] = "wizard,cron"
    src = (ROOT / "mcp/front.py").read_text() + "\n" + (ROOT / "mcp/tools.py").read_text() + "\n" + (ROOT / "mcp/minimal_assets.py").read_text()
    ns = {"__name__": "notif_test"}
    exec(compile(src, "front+tools", "exec"), ns)
    return ns


def lines(*ms):
    return "\n".join(json.dumps(m) for m in ms).encode()


CUT = {"code": 42905, "http": 429, "error": "limit reached: daily bandwidth reached"}


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmp.name) / "inbox.json")
        self.ns = load(self.state)
        self.topics = {}
        self.ns["http"] = lambda method, path, **kw: (200, self.topics[path.split("/")[1]])
        self.ns["TOPICS"] = ["wizard", "cron"]
        self.ns["TOPICS"][:] = ["wizard", "cron"]

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, marks):
        Path(self.state).write_text(json.dumps(marks))

    def saved(self):
        return json.loads(Path(self.state).read_text())

    def test_cut_off_poll_raises(self):
        self.topics = {"wizard": lines({"event": "message", "id": "w1", "time": 1}),
                       "cron": lines({"event": "message", "id": "c1", "time": 1}, CUT)}
        with self.assertRaises(RuntimeError):
            self.ns["_messages"]()

    def test_mark_during_cut_off_poll_keeps_other_marks(self):
        self.seed({"c_old": {"read": True, "archived": True}, "c_new": {"read": True, "archived": True}})
        self.topics = {"wizard": lines({"event": "message", "id": "w1", "time": 1}),
                       "cron": lines({"event": "message", "id": "c_old", "time": 1}, CUT)}
        self.ns["inbox_mark"](["w1"], archived=True)
        st = self.saved()
        self.assertIn("c_new", st)
        self.assertTrue(st["c_new"]["archived"])
        self.assertTrue(st["w1"]["archived"])

    def test_missing_message_kept_for_grace_then_dropped(self):
        self.seed({"gone1": {"read": True}})
        self.topics = {"wizard": lines({"event": "message", "id": "w1", "time": 1}), "cron": b""}
        self.ns["inbox_mark"](["w1"], read=True)
        st = self.saved()
        self.assertIn("gone1", st)
        st["gone1"]["gone"] = time.time() - 4 * 86400
        Path(self.state).write_text(json.dumps(st))
        self.ns["inbox_mark"](["w1"], read=True)
        self.assertNotIn("gone1", self.saved())

    def test_message_back_in_list_clears_gone(self):
        self.seed({"c1": {"read": True, "gone": time.time() - 86400}})
        self.topics = {"wizard": b"", "cron": lines({"event": "message", "id": "c1", "time": 1})}
        self.ns["inbox_mark"](["c1"], archived=True)
        self.assertNotIn("gone", self.saved()["c1"])


if __name__ == "__main__":
    unittest.main()
