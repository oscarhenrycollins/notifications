"""Scheduled-job relay: silent runs, failure dedupe, reminders, recovery, quiet jobs, retry, lookback, state."""
import json, os, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(env=None):
    for k, v in (env or {}).items():
        os.environ[k] = v
    src = "\n".join((ROOT / "mcp" / n).read_text() for n in ("front.py", "tools.py", "minimal_assets.py", "relay.py"))
    ns = {"__name__": "relay_test"}
    exec(compile(src, "front+tools+relay", "exec"), ns)
    for k in (env or {}):
        os.environ.pop(k, None)
    return ns


def run_md(status="ok", response="", error=""):
    t = "# Cron Job: Demo\n\n**Status:** %s\n\n" % status
    if error:
        t += "## Error\n\n```\n%s\n```\n" % error
    if response:
        t += "## Response\n\n%s\n" % response
    return t


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.cron = self.root / "cron"; (self.cron / "output").mkdir(parents=True)
        (self.cron / "jobs.json").write_text(json.dumps({"jobs": [{"id": "job1", "name": "Backup check"},
                                                                  {"id": "morning", "name": "Morning briefing"}]}))
        self.state = str(self.root / "state.json")
        self.ns = load({"CRON_QUIET_JOBS": "morning"})
        self.posts = []; self.fail_posts = False
        self.t0 = time.time() - 3600

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, title, message, priority, tags):
        if self.fail_posts:
            return False
        self.posts.append((title, message, priority, tags)); return True

    def add(self, job, name, text, t):
        d = self.cron / "output" / job; d.mkdir(exist_ok=True)
        p = d / name; p.write_text(text); os.utime(p, (t, t)); return p

    def run_once(self, now=None):
        return self.ns["cron_relay_once"](str(self.cron), self.state, self.post, now or time.time())

    def test_success_posts_once_with_job_name(self):
        self.add("job1", "2026-10-24_01-00-00.md", run_md(response="All 3 backups fresh."), self.t0)
        self.assertEqual(self.run_once()["sent"], 1)
        self.assertEqual(self.posts[0][0], "Backup check"); self.assertEqual(self.posts[0][2], 2)
        self.assertEqual(self.run_once()["sent"], 0)   # never twice

    def test_silent_runs_post_nothing(self):
        self.add("job1", "a.md", run_md(response="[SILENT]"), self.t0)
        self.add("job1", "b.md", run_md(status="silent"), self.t0 + 1)
        self.assertEqual(self.run_once()["sent"], 0); self.assertEqual(self.posts, [])

    def test_same_failure_quiet_for_a_day_then_reminds(self):
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        self.add("job1", "2.md", run_md(status="error", error="disk full"), self.t0 + 600)
        self.run_once()
        self.assertEqual(len(self.posts), 1); self.assertTrue(self.posts[0][0].startswith("Failed: "))
        self.assertEqual(self.posts[0][2], 4)
        self.add("job1", "3.md", run_md(status="error", error="disk full"), self.t0 + 86400 + 60)
        self.run_once(now=self.t0 + 86400 + 120)
        self.assertEqual(len(self.posts), 2)          # daily safety reminder

    def test_changed_error_and_recurrence_alert(self):
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        self.add("job1", "2.md", run_md(status="error", error="network down"), self.t0 + 60)
        self.add("job1", "3.md", run_md(response="All good now."), self.t0 + 120)
        self.add("job1", "4.md", run_md(status="error", error="disk full"), self.t0 + 180)
        self.run_once()
        self.assertEqual([p[0] for p in self.posts],
                         ["Failed: Backup check", "Failed: Backup check", "Backup check", "Failed: Backup check"])

    def test_dated_outcomes_are_not_merged(self):
        self.add("job1", "1.md", run_md(response="Report for 23 Oct: 4 items."), self.t0)
        self.add("job1", "2.md", run_md(response="Report for 24 Oct: 4 items."), self.t0 + 60)
        self.run_once(); self.assertEqual(len(self.posts), 2)

    def test_quiet_job_success_is_badge_only_but_failure_alerts(self):
        self.add("morning", "2026-10-24_02-00-00.md", run_md(response="Your briefing is ready."), self.t0)
        self.run_once(); self.assertEqual(self.posts, [])
        st = json.loads(Path(self.state).read_text()); self.assertIn("2026-10-24", st["quiet_days"])
        self.add("morning", "2026-10-25_02-00-00.md", run_md(status="error", error="model timeout"), self.t0 + 60)
        self.run_once(); self.assertEqual(self.posts[0][0], "Failed: Morning briefing")

    def test_quiet_by_name(self):
        ns = load({"CRON_QUIET_JOBS": "Backup Check"})
        self.add("job1", "1.md", run_md(response="fine"), self.t0)
        ns["cron_relay_once"](str(self.cron), self.state, self.post, time.time()); self.assertEqual(self.posts, [])

    def test_failed_post_is_retried_and_keeps_fingerprint(self):
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        self.fail_posts = True; self.assertEqual(self.run_once()["sent"], 0)
        st = json.loads(Path(self.state).read_text())
        self.assertEqual(st["done"], []); self.assertNotIn("job1", st["failure_alerts"])
        self.fail_posts = False; self.assertEqual(self.run_once()["sent"], 1)

    def test_first_pass_ignores_old_history_and_fresh_files(self):
        self.add("job1", "old.md", run_md(response="ancient"), time.time() - 3 * 86400)
        self.add("job1", "writing.md", run_md(response="half written"), time.time() - 2)
        self.assertEqual(self.run_once()["sent"], 0)
        self.assertEqual(self.run_once(now=time.time() + 60)["sent"], 1)   # the fresh one, once it settled

    def test_no_cron_folder_is_reported_not_raised(self):
        r = self.ns["cron_relay_once"](str(self.root / "nope"), self.state, self.post)
        self.assertIn("off", r)

    def test_reads_existing_state_from_the_old_host_script(self):
        p = self.add("job1", "1.md", run_md(response="done"), self.t0)
        Path(self.state).write_text(json.dumps({"since": self.t0 - 10, "done": ["job1/1.md"], "failure_alerts": {}}))
        self.assertEqual(self.run_once()["sent"], 0)

    # ---- failures go to the agent's responder ----
    def responder_box(self):
        (self.cron / "jobs.json").write_text(json.dumps({"jobs": [
            {"id": "job1", "name": "Backup check"}, {"id": "morning", "name": "Morning briefing"},
            {"id": "resp", "name": "Failure responder", "enabled": True}]}))

    def openf(self):
        p = self.root / "cron-failures.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def test_without_responder_failures_still_post(self):
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        self.run_once(); self.assertEqual(self.posts[0][0], "Failed: Backup check")
        self.assertFalse((self.root / "cron-failures.json").exists())

    def test_with_responder_failures_are_queued_not_posted(self):
        self.responder_box()
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        self.add("job1", "2.md", run_md(response="Standby has not synced for 8 hours (last error: low disk)"), self.t0 + 60)
        self.add("job1", "3.md", run_md(response="Failed again: standby 10 hours"), self.t0 + 120)
        self.run_once()
        self.assertEqual(self.posts, [])
        o = self.openf()["job1"]; self.assertEqual(o["runs"], 3); self.assertIn("10 hours", o["body"])
        self.assertEqual(o["first_seen"], self.t0)

    def test_success_closes_the_open_failure_and_still_posts_success(self):
        self.responder_box()
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        self.run_once(); self.assertIn("job1", self.openf())
        self.add("job1", "2.md", run_md(response="All 3 backups fresh."), self.t0 + 60)
        self.run_once(now=self.t0 + 200)
        self.assertNotIn("job1", self.openf()); self.assertEqual([p[0] for p in self.posts], ["Backup check"])

    def test_silent_success_closes_too(self):
        self.responder_box()
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        self.add("job1", "2.md", run_md(response="[SILENT]"), self.t0 + 60)
        self.run_once(); self.assertEqual(self.openf(), {}); self.assertEqual(self.posts, [])

    def test_responder_output_and_failures_always_post(self):
        self.responder_box()
        self.add("resp", "1.md", run_md(response="Needs you: add Codex credits. Failed to fix."), self.t0)
        self.add("resp", "2.md", run_md(status="error", error="provider down"), self.t0 + 60)
        self.run_once()
        self.assertEqual([(p[0], p[2]) for p in self.posts], [("Failure responder", 4), ("Failed: Failure responder", 4)])
        self.assertEqual(self.openf(), {})

    def test_escalates_once_after_a_day_unless_acknowledged(self):
        self.responder_box()
        t = time.time() - 30 * 3600
        self.add("job1", "1.md", run_md(status="error", error="disk full"), t)
        self.run_once(now=t + 3600); self.assertEqual(self.posts, [])
        self.run_once(now=t + 25 * 3600)
        self.assertEqual(len(self.posts), 1); self.assertTrue(self.posts[0][0].startswith("Still failing: Backup check"))
        self.run_once(now=t + 50 * 3600); self.assertEqual(len(self.posts), 1)   # once per episode

    def test_acknowledged_failure_never_escalates(self):
        self.responder_box()
        t = time.time() - 30 * 3600
        self.add("job1", "1.md", run_md(status="error", error="disk full"), t)
        self.run_once(now=t + 3600)
        (self.root / "cron-failures-ack.json").write_text(json.dumps({"job1": t}))
        self.run_once(now=t + 25 * 3600); self.assertEqual(self.posts, [])

    def test_owner_mode_overrides_responder(self):
        self.responder_box(); ns = load({"CRON_FAILURES": "owner"})
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        ns["cron_relay_once"](str(self.cron), self.state, self.post, time.time())
        self.assertEqual(self.posts[0][0], "Failed: Backup check")

    def test_paused_responder_means_owner_gets_failures(self):
        (self.cron / "jobs.json").write_text(json.dumps({"jobs": [{"id": "job1", "name": "Backup check"},
                                                                  {"id": "resp", "name": "Failure responder", "enabled": False}]}))
        self.add("job1", "1.md", run_md(status="error", error="disk full"), self.t0)
        self.run_once(); self.assertEqual(self.posts[0][0], "Failed: Backup check")

    def test_compose_is_generated_and_has_no_dollar_in_front(self):
        self.assertNotIn("$", (ROOT / "mcp" / "relay.py").read_text())


if __name__ == "__main__":
    unittest.main()
