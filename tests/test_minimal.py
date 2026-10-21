import ast, json, subprocess, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def constants(path):
    result = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            try: result[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError, AttributeError): pass
    return result
class MinimalInboxTests(unittest.TestCase):
    def test_every_skin_gets_the_minimal_inbox(self):
        # No skin gate: the page served by default is the minimal inbox, ?layout=original is the previous screen.
        tools = (ROOT/'mcp/tools.py').read_text()
        self.assertIn('html = PAGE_HTML if (q.get("layout") or [""])[0] == "original" else MINIMAL_HTML', tools)
        assets = constants(ROOT/'mcp/minimal_assets.py')
        self.assertNotIn('layout.js', assets['MINIMAL_HTML'])
        gate = assets['MINIMAL_ASSETS']['layout.js'][1]
        for word in ['obsidian', 'location.replace', 'searchParams']:
            self.assertNotIn(word, gate)
    def test_both_screens_still_exist(self):
        consts = constants(ROOT/'mcp/minimal_assets.py')
        self.assertIn('data-filter="unread"', consts['MINIMAL_HTML']); self.assertIn('data-filter="all"', consts['MINIMAL_HTML'])
        self.assertIn('nt-tabs', constants(ROOT/'mcp/tools.py')['PAGE_HTML'])
    def test_swipe_strip_stays_on_miguels_skins_only(self):
        source = (ROOT/'web/minimal/inbox.js').read_text()
        self.assertIn("return !!skin && OURS.has(skin) && phone() && innerWidth < 768;", source)
        self.assertNotIn('(!skin || OURS.has(skin))', source)
    def test_james_skins_use_their_own_tokens(self):
        css = (ROOT/'web/minimal/styles.css').read_text()
        import re
        self.assertIsNone(re.search(r'#[0-9a-fA-F]{3,8}\b|rgba?\(', css), 'colours come from skin tokens only')
        self.assertNotIn('font-family: "WOS', css); self.assertNotIn('Sora', css); self.assertNotIn('Inter', css)
        self.assertIn('.nt-filters button[aria-pressed="true"] { background: var(--accent); color: var(--on-accent);', css)
        self.assertIn('html:is([data-skin="obsidian"], [data-skin="obsidian-light"], [data-skin="landscapes"]) .nt-filters button[aria-pressed="true"]', css)
        self.assertIn('html[data-skin="signal"] :is(', css)
    def test_original_screen_preserved(self):
        import hashlib
        const = constants(ROOT/'mcp/tools.py')
        baseline = json.loads((ROOT/'tests/original-screen-hashes.json').read_text())
        self.assertEqual({k:hashlib.sha256(const[k].encode()).hexdigest() for k in baseline},baseline)
    def test_chat_is_supported_unsent_not_url_or_send(self):
        source = (ROOT/'web/minimal/inbox.js').read_text()
        self.assertIn('WizardApp.nav.openContextDraft', source)
        self.assertNotIn('nav.openThread', source)
        self.assertNotIn('?session=', source)
        self.assertIn("receipt.sent !== false", source)
        self.assertNotIn('confirm(', source)
        self.assertIn('Confirm mark all read', source)
    def test_swipe_strip_uses_chats_numbers_and_skin_gate(self):
        source = (ROOT/'web/minimal/inbox.js').read_text()
        for needle in ['LOCK = 10', 'RATIO = 1.3', 'GO = 70', 'VGO = 0.45', 'MS = 240', "EASE = 'cubic-bezier(.2,.8,.2,1)'", 'EDGE = 24',
                       "new Set(['obsidian', 'obsidian-light', 'landscapes'])", 'innerWidth < 768']:
            self.assertIn(needle, source)
        self.assertNotIn('setTimeout(', source.split('const strip')[1].split('window.__ntStrip')[0])
    def test_mark_unread_never_wraps(self):
        css = (ROOT/'web/minimal/styles.css').read_text()
        self.assertIn('.act span { max-width: 100%; white-space: nowrap;', css)
if __name__ == '__main__': unittest.main()
