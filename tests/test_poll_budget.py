"""Regression: opening Notifications showed "HTTPError: HTTP Error 429: Too Many Requests".

ntfy charges messages replayed by polls against the visitor's daily bandwidth budget; re-reading every
topic "since=all" on each list/count/mark spent it, and then the whole inbox failed. The inbox must read
in full only periodically, fetch only newer messages in between, and keep showing its last good copy
when the engine refuses."""
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    ns = {"__name__": "tests"}
    src = "\n".join((ROOT / "mcp" / p).read_text() for p in ("front.py", "tools.py", "minimal_assets.py", "relay.py", "readable.py"))
    exec(compile(src, "notifications", "exec"), ns)
    ns["READABLE_DB"] = str(path)
    return ns


def msg(i, t):
    return {"id": "m%d" % i, "time": t, "event": "message", "topic": "wizard", "title": "T%d" % i, "message": "x"}


class PollBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ns = load(Path(self.tmp.name) / "updates.db")
        self.ns["TOPICS"] = ["wizard"]
        self.store, self.calls, self.refuse = [msg(1, 100), msg(2, 200)], [], False

        def http(method, path, body=None, headers=None, timeout=None):
            self.calls.append(path)
            if self.refuse:
                raise urllib.error.HTTPError(path, 429, "Too Many Requests", {}, None)
            since = path.split("since=")[1]
            rows = self.store if since == "all" else [m for m in self.store if m["time"] >= int(since)]
            return 200, "".join(json.dumps(m) + "\n" for m in rows).encode()
        self.ns["http"] = http

    def tearDown(self):
        self.tmp.cleanup()

    def ids(self, now):
        return [m["id"] for m in self.ns["_topic_messages"]("wizard", now=now)]

    def test_repeated_inbox_reads_do_not_replay_the_whole_topic(self):
        self.ids(1000)
        for n in range(20):
            self.ids(1001 + n)
        full = [c for c in self.calls if c.endswith("since=all")]
        self.assertEqual(len(full), 1, self.calls)
        self.assertTrue(all(c.endswith("since=200") for c in self.calls[1:]), self.calls)

    def test_new_message_appears_without_a_full_read_and_without_duplicates(self):
        self.ids(1000)
        self.store.append(msg(3, 200))
        self.store.append(msg(4, 300))
        self.assertEqual(self.ids(1001), ["m1", "m2", "m3", "m4"])

    def test_full_read_returns_after_the_interval_and_drops_expired(self):
        self.ids(1000)
        self.store.pop(0)
        self.assertEqual(self.ids(1000 + self.ns["POLL_FULL_EVERY"]), ["m2"])

    def test_engine_refusal_keeps_the_last_good_inbox(self):
        self.ids(1000)
        self.refuse = True
        self.assertEqual(self.ids(1001), ["m1", "m2"])
        self.assertEqual(self.ids(5000), ["m1", "m2"])

    def test_limit_reported_inside_a_200_stream_is_not_an_empty_inbox(self):
        self.ids(1000)
        self.ns["http"] = lambda *a, **k: (200, b'{"code":42905,"http":429,"error":"limit reached: daily bandwidth reached"}\n')
        self.assertEqual(self.ids(5000), ["m1", "m2"])

    def test_cached_items_are_copies(self):
        first = self.ns["_topic_messages"]("wizard", now=1000)
        first[0]["details"] = "added by the inbox"
        self.assertNotIn("details", self.ns["_topic_messages"]("wizard", now=1001)[0])


if __name__ == "__main__":
    unittest.main()
