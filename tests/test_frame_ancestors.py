"""Rehearsal finding (wizard-os 389, KVM box, real Chromium): a page on another site could frame
https://<box>:8796/notifications/ (Tailscale Serve -> this front) and it rendered the owner's inbox. The app's pages
now carry frame-ancestors 'self' + X-Frame-Options SAMEORIGIN: only the Wizard shell on the same origin frames them
(its opaque window included, since both checks use the page's URL origin). JSON/JS answers are unchanged."""
import http.server, threading, tempfile, unittest

from test_cross_site import Front, Ntfy


class FrameAncestors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ntfy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ntfy)
        threading.Thread(target=cls.ntfy.serve_forever, daemon=True).start()
        cls.front = Front(cls.tmp.name, cls.ntfy.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.front.stop()
        cls.ntfy.shutdown()
        cls.tmp.cleanup()

    def test_pages_refuse_foreign_framing(self):
        for path in ("/notifications/", "/notifications/index.html", "/notifications/?layout=minimal"):
            code, hdrs, _ = self.front.req("GET", path, [("Sec-Fetch-Dest", "iframe"), ("Sec-Fetch-Mode", "navigate")])
            self.assertEqual(code, 200, path)
            self.assertEqual(hdrs.get("x-frame-options"), "SAMEORIGIN", path)
            self.assertEqual(hdrs.get("content-security-policy"), "frame-ancestors 'self'", path)

    def test_scripts_and_json_unchanged(self):
        code, hdrs, _ = self.front.req("GET", "/notifications/inbox.js")
        self.assertEqual(code, 200)
        self.assertNotIn("x-frame-options", hdrs)


if __name__ == "__main__":
    unittest.main()
