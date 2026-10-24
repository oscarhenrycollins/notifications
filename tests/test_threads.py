"""Scheduled-job threads: one inbox row per job, marks fan out per run, a new run re-lights an archived thread."""
import json, os, tempfile, unittest
from pathlib import Path
from test_relay import load


def msg(i, t, title, prio=2, cron=True):
    return {"event": "message", "id": i, "time": t, "title": title, "message": "body %s" % i, "priority": prio,
            "tags": ["cron"] if cron else []}


class ThreadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ns = load()
        self.ns["STATE_FILE"] = os.path.join(self.tmp.name, "inbox.json")
        self.msgs = [msg("a1", 100, "Build delivery · flow watch"), msg("a2", 200, "Failed: Build delivery · flow watch", 4),
                     msg("a3", 300, "Build delivery · flow watch"), msg("n1", 250, "Note from your agent", 3, cron=False),
                     msg("d1", 150, "disk-pressure-watchdog", 4)]
        self.ns["_messages"] = lambda since="all": sorted(self.msgs, key=lambda m: -m["time"])

    def tearDown(self):
        self.tmp.cleanup()

    def items(self, view="inbox"):
        return self.ns["inbox_items"](view, 200)

    def test_one_row_per_job_and_notes_stay_single(self):
        d = self.items()
        titles = [i["title"] for i in d["items"]]
        self.assertEqual(len(d["items"]), 3)
        flow = next(i for i in d["items"] if i.get("runs") and "flow" in i["title"])
        self.assertEqual([r["id"] for r in flow["runs"]], ["a3", "a2", "a1"])
        self.assertTrue(flow["id"].startswith("t-"))
        self.assertIn("Note from your agent", titles)
        self.assertEqual(d["counts"], {"unread": 3, "needs": 2, "flagged": 0})   # counted per row, not per run

    def test_order_is_newest_first_and_marking_never_moves_a_row(self):
        before = [i["id"] for i in self.items()["items"]]
        for i in list(before):
            self.ns["inbox_mark"]([i], read=True)
            self.assertEqual([r["id"] for r in self.items()["items"]], before)
        self.ns["inbox_mark"]([before[-1]], read=False)
        self.assertEqual([r["id"] for r in self.items()["items"]], before)

    def test_mark_read_fans_out_and_unread_reopens_newest(self):
        tid = next(i["id"] for i in self.items()["items"] if "flow" in i["title"])
        self.ns["inbox_mark"]([tid], read=True)
        st = json.load(open(self.ns["STATE_FILE"]))
        self.assertTrue(all(st[k]["read"] for k in ("a1", "a2", "a3")))
        self.assertEqual(self.items()["counts"]["needs"], 1)                    # only the disk warning is left
        self.ns["inbox_mark"]([tid], read=False)
        st = json.load(open(self.ns["STATE_FILE"]))
        self.assertFalse(st["a3"]["read"]); self.assertTrue(st["a1"]["read"])

    def test_archived_thread_returns_when_a_new_failure_lands(self):
        tid = next(i["id"] for i in self.items()["items"] if "flow" in i["title"])
        self.ns["inbox_mark"]([tid], archived=True)
        self.assertFalse(any("flow" in i["title"] for i in self.items()["items"]))
        self.msgs.append(msg("a4", 400, "Failed: Build delivery · flow watch", 4))
        row = next(i for i in self.items()["items"] if "flow" in i["title"])
        self.assertEqual([r["id"] for r in row["runs"]], ["a4"])
        self.assertFalse(row["read"]); self.assertEqual(self.items()["items"][0]["id"], row["id"])   # newest run
        self.assertEqual(len(next(i for i in self.items("archive")["items"] if "flow" in i["title"])["runs"]), 3)

    def test_backlog_relayed_in_one_pass_orders_by_run_stamp(self):
        self.msgs = [dict(msg("b1", 500, "Backup"), message="older\n\n23 Oct 01:00 UTC"),
                     dict(msg("b2", 500, "Backup"), message="newer\n\n23 Oct 09:00 UTC")]
        row = self.items()["items"][0]
        self.assertEqual([r["id"] for r in row["runs"]], ["b2", "b1"]); self.assertTrue(row["message"].startswith("newer"))

    def test_plain_ids_still_work(self):
        self.ns["inbox_mark"](["n1"], flag=True)
        self.assertEqual(self.items()["counts"]["flagged"], 1)


if __name__ == "__main__":
    unittest.main()
