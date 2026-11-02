"""Browser trust preview (WIZARD_BROWSER_BOUNDARY, default off): the inbox's marks carry a revision and
/api/mark can compare-and-set on it, so the shell's typed notifications.mark never overwrites a change the
owner did not see. Without `expect` the API behaves exactly as before."""
import hashlib, json, os, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(state):
    os.environ["INBOX_STATE"] = state
    src = (ROOT / "mcp/front.py").read_text() + "\n" + (ROOT / "mcp/tools.py").read_text() + "\n" + (ROOT / "mcp/minimal_assets.py").read_text()
    ns = {"__name__": "notif_test"}
    exec(compile(src, "front+tools", "exec"), ns)
    ns["_messages"] = lambda since="all": [{"id": "ntfy1", "time": 2, "message": "one"}, {"id": "ntfy2", "time": 1, "message": "two"}]
    return ns


class BrowserTrustMarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmp.name) / "inbox.json")
        self.ns = load(self.state)
        self.route = self.ns["inbox_route"]

    def tearDown(self):
        self.tmp.cleanup()

    def items(self):
        code, _, raw = self.route("GET", "/notifications/api/items", "view=all&limit=50", {}, b"")
        self.assertEqual(code, 200)
        return {i["id"]: i for i in json.loads(raw)["items"]}

    def post(self, body):
        code, _, raw = self.route("POST", "/notifications/api/mark", "", {"content-type": "application/json"}, json.dumps(body).encode())
        return code, json.loads(raw)

    def test_revision_matches_the_agent_service_digest(self):
        # The Service computes 'r-' + sha256(JSON.stringify([...])).slice(0, 16) in Node; prove byte equality.
        js = ("const c=require('node:crypto');const v=JSON.stringify(['notification-mark-v1','ntfy1',true,false,true]);"
              "console.log('r-'+c.createHash('sha256').update(v).digest('hex').slice(0,16))")
        node = subprocess.check_output(["node", "-e", js], text=True).strip()
        self.assertEqual(self.ns["mark_revision"]("ntfy1", True, False, True), node)

    def test_items_carry_the_revision_of_their_marks(self):
        before = self.items()["ntfy1"]
        self.assertEqual(before["revision"], self.ns["mark_revision"]("ntfy1", False, False, False))
        self.assertEqual(self.post({"ids": ["ntfy1"], "flag": True})[0], 200)
        self.assertEqual(self.items()["ntfy1"]["revision"], self.ns["mark_revision"]("ntfy1", False, True, False))

    def test_expect_matching_writes_every_id(self):
        it = self.items()
        code, body = self.post({"ids": ["ntfy1", "ntfy2"], "read": True, "expect": {"ntfy1": it["ntfy1"]["revision"], "ntfy2": it["ntfy2"]["revision"]}})
        self.assertEqual((code, body), (200, {"ok": True, "changed": 2}))
        self.assertTrue(all(i["read"] for i in self.items().values()))

    def test_stale_expect_writes_nothing(self):
        it = self.items()
        self.assertEqual(self.post({"ids": ["ntfy2"], "flag": True})[0], 200)   # changed elsewhere
        before = Path(self.state).read_bytes()
        code, body = self.post({"ids": ["ntfy1", "ntfy2"], "read": True, "expect": {"ntfy1": it["ntfy1"]["revision"], "ntfy2": it["ntfy2"]["revision"]}})
        self.assertEqual((code, body), (409, {"error": "conflict"}))
        self.assertEqual(Path(self.state).read_bytes(), before)   # all or nothing
        self.assertFalse(self.items()["ntfy1"]["read"])

    def test_unread_is_compare_and_set_too(self):
        self.assertEqual(self.post({"ids": ["ntfy1"], "read": True})[0], 200)
        rev = self.items()["ntfy1"]["revision"]
        self.assertEqual(self.post({"ids": ["ntfy1"], "read": False, "expect": {"ntfy1": rev}})[0], 200)
        self.assertEqual(self.post({"ids": ["ntfy1"], "read": False, "expect": {"ntfy1": rev}})[0], 409)   # replay of a stale view

    def test_expect_must_cover_exactly_the_ids(self):
        rev = self.items()["ntfy1"]["revision"]
        for expect in ({}, {"ntfy2": rev}, {"ntfy1": rev, "ntfy2": rev, "x": rev}, ["ntfy1"], {"ntfy1": 7}):
            code, _ = self.post({"ids": ["ntfy1"], "read": True, "expect": expect})
            self.assertEqual(code, 400, expect)
        self.assertFalse(self.items()["ntfy1"]["read"])

    def test_without_expect_the_api_is_unchanged(self):
        self.assertEqual(self.post({"ids": ["ntfy1"], "archived": True}), (200, {"ok": True, "changed": 1}))
        self.assertTrue(self.items()["ntfy1"]["read"])


class OriginalScreenTests(unittest.TestCase):
    def test_original_page_only_gains_the_two_data_scripts(self):
        import ast
        const = {}
        for node in ast.parse((ROOT / "mcp/tools.py").read_text()).body:
            if isinstance(node, ast.Assign):
                try: const[node.targets[0].id] = ast.literal_eval(node.value)
                except (ValueError, TypeError, AttributeError): pass
        html = const["PAGE_HTML"]
        for tag in ('<script src="/wizard-os-home/core/browser-boundary-client.js" onerror="this.remove()"></script>\n', '<script src="minimal-data.js"></script>\n'):
            self.assertEqual(html.count(tag), 1)
            html = html.replace(tag, "")
        # Hash of PAGE_HTML at e6df99a (before this change): markup and styles are byte-identical.
        self.assertEqual(hashlib.sha256(html.encode()).hexdigest(), "dc329de9160d0fcfa2f4e0de968d81ca4ccd2f99ec1e4d9687ca8cbf2b6e16b3")
        self.assertNotIn("fetch(", const["PAGE_JS"])        # every read/mark goes through minimal-data.js
        self.assertNotIn("parent.postMessage", const["PAGE_JS"])

    def test_minimal_screen_has_no_direct_api_or_parent_reads_in_opaque_mode(self):
        js = (ROOT / "web/minimal/inbox.js").read_text()
        self.assertNotIn("fetch(", js)
        self.assertNotIn("parent.postMessage", js)
        self.assertIn("if (!D.opaque) try { const s = parent !== window && parent.document", js)
        html = (ROOT / "web/minimal/index.html").read_text()
        self.assertLess(html.index("browser-boundary-client.js"), html.index("app-client.js"))
        self.assertLess(html.index("app-client.js"), html.index("data.js"))


if __name__ == "__main__":
    unittest.main()
