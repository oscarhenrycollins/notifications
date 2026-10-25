import io
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DONE = {"type": "done", "provider": "fake", "model": "tested", "tier": "fast", "tested_on": "tested", "chosen": "match"}


def load(path):
    ns = {"__name__": "tests"}
    src = "\n".join((ROOT / "mcp" / p).read_text() for p in ("front.py", "tools.py", "minimal_assets.py", "relay.py", "readable.py"))
    exec(compile(src, "notifications", "exec"), ns)
    ns["READABLE_DB"] = str(path)
    return ns


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ns = load(Path(self.tmp.name) / "updates.db")
        self.posts = []
        def http(method, path, body=None, headers=None, timeout=None):
            self.posts.append(json.loads(body))
            return 200, b'{"id":"engine-id","event":"message","priority":4}'
        self.ns["http"] = http

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, title, message, verdict, body="The task is finished. Nothing to do."):
        return self.ns["rewrite_and_deliver"]({"title": title, "message": message, "topic": "test"},
            rewrite=lambda p: ({"verdict": verdict, "title": "Task update", "body": body}, DONE))

    def test_exact_cleanup_input_is_not_pushed(self):
        fixture = os.environ.get("NIGHTLY_CLEANUP_FIXTURE")
        raw = Path(fixture).read_text() if fixture else json.dumps({"apply": True, "selected": [{"path": "/owner/releases/old", "reason": "old-unreferenced-release"}] * 9, "removed": ["/owner/releases/old"] * 9, "errors": [], "used_percent": 88.4})
        out = self.send("nightly-disk-hygiene", raw, "noise")
        self.assertFalse(out["pushed"])
        self.assertEqual(self.posts, [])
        with self.ns["readable_db"]() as db:
            self.assertEqual(json.loads(db.execute("select raw from updates").fetchone()[0])["message"], raw)

    def test_failed_silent_plus_footer_is_noise_even_if_model_mistakes_title(self):
        out = self.send("Failed: Thales automatic maintenance", "[SILENT]\n\nFile-mutation verifier: checks passed", "needs_you", "The task failed. What to do: Check the computer.")
        self.assertEqual(out["verdict"], "noise")
        self.assertFalse(out["pushed"])

    def test_recovery_no_human_gate_cannot_push(self):
        out = self.send("Automatic recovery needs your help", "Recovery continues; no genuine human gate.", "needs_you", "The task failed. What to do: Restart it.")
        self.assertEqual(out["verdict"], "noise")
        self.assertFalse(out["pushed"])

    def test_real_disk_action_pushes_only_clear_summary(self):
        out = self.send("Disk warning", "Disk 95% full. Delete old downloaded videos or approve extra storage.", "needs_you", "Storage is almost full. What to do: Delete old downloaded videos or approve extra storage.")
        self.assertTrue(out["pushed"])
        self.assertEqual(self.posts[0]["priority"], 4)
        self.assertIn("What to do:", self.posts[0]["message"])
        self.assertNotIn("Disk 95%", self.posts[0]["message"])

    def test_fyi_is_inbox_only_not_even_min_priority_ntfy(self):
        out = self.send("Requested export", "Your requested export is ready.", "fyi")
        self.assertFalse(out["pushed"])
        self.assertEqual(self.posts, [])
        items, details = self.ns["readable_messages"]()
        self.assertEqual(items[0]["priority"], 1)
        self.assertIn("Your requested export is ready.", details[out["id"]])

    def test_unavailable_retains_raw_and_retries_same_item(self):
        p = {"title": "job-id-123", "message": '{"raw":true}'}
        def unavailable(p):
            raise OSError("unreachable")
        out = self.ns["rewrite_and_deliver"](p, rewrite=unavailable)
        self.assertTrue(out["pending"])
        self.assertFalse(out["pushed"])
        self.assertNotIn("raw", out["body"])
        out2 = self.ns["rewrite_and_deliver"](p, uid=out["rewrite_id"], rewrite=lambda p: ({"verdict": "noise", "title": "Done", "body": "Cleanup finished. Nothing to do."}, DONE))
        self.assertFalse(out2["pending"])
        self.assertEqual(self.ns["readable_messages"]()[0], [])
        self.assertEqual(self.posts, [])

    def test_explicit_disk_owner_action_survives_router_outage(self):
        def unavailable(p):
            raise OSError("unreachable")
        out = self.ns["rewrite_and_deliver"]({"message": "The disk is 95% full. Delete old downloaded videos or approve extra storage."}, rewrite=unavailable)
        self.assertTrue(out["pending"])
        self.assertTrue(out["pushed"])
        self.assertEqual(out["verdict"], "needs_you")
        self.assertIn("Delete old downloaded videos or approve extra storage", out["body"])

    def test_missing_done_or_error_discards_provisional_text(self):
        for s in ['data: {"type":"delta","text":"unsafe"}\n\n', 'data: {"type":"error","code":"needs_owner"}\n\n']:
            with self.assertRaises((ValueError, RuntimeError)):
                self.ns["read_router_stream"](io.BytesIO(s.encode()))

    def test_valid_done_receipt(self):
        s = 'data: {"type":"delta","text":"hello"}\n\ndata: ' + json.dumps(DONE) + '\n\n'
        self.assertEqual(self.ns["read_router_stream"](io.BytesIO(s.encode())), ("hello", DONE))

    def test_model_cannot_push_without_action_or_with_raw_jargon(self):
        for result in [{"verdict": "needs_you", "title": "Check", "body": "Something happened."}, {"verdict": "needs_you", "title": "Check unit", "body": "What to do: Fix lease."}]:
            out = self.ns["rewrite_and_deliver"]({"message": "status"}, rewrite=lambda p: (result, DONE))
            self.assertFalse(out["pushed"])
            self.assertTrue(out["pending"])

    def test_noise_empty_body_is_a_receipt_not_an_invalid_alert(self):
        out = self.ns["rewrite_and_deliver"]({"message": "No genuine human gate."}, rewrite=lambda p: ({"verdict": "noise", "title": "[SILENT]", "body": ""}, DONE))
        self.assertEqual(out["verdict"], "noise")
        self.assertFalse(out["pending"])
        self.assertFalse(out["pushed"])

    def test_rewritten_cron_keeps_original_thread_key(self):
        self.assertEqual(self.ns["_thread_key"]({"source":"cron", "title":"A new readable title", "thread_title":"Recovered: Backup"}), "backup")
        self.assertEqual(self.ns["_thread_key"]({"source":"cron", "title":"Backup"}), "backup")

    def test_http_json_and_topic_publish_use_same_boundary(self):
        called = []
        self.ns["rewrite_and_deliver"] = lambda p: called.append(p) or {"pushed": False}
        route = self.ns["publish_route"]
        self.assertEqual(route("POST", "/", "", {"content-type": "application/json"}, b'{"topic":"wizard","message":"update"}')[0], 200)
        self.assertEqual(route("PUT", "/wizard", "", {"title": "Heading"}, b'update')[0], 200)
        self.assertEqual(route("GET", "/wizard/publish", "message=update", {}, b'')[0], 200)
        self.assertEqual(len(called), 3)


if __name__ == "__main__":
    unittest.main()
