"""Generate inline assets; the minimal inbox is the default for every skin (?layout=original = previous screen)."""
import json, hashlib
from pathlib import Path
root = Path(__file__).resolve().parents[1]
web = root / 'web' / 'minimal'
# Every skin gets this inbox. layout.js stays as a no-op (pages cached before this change still load it).
gate = """/* Notifications: one inbox for every skin. ?layout=original still opens the previous screen. */"""
html = (web/'index.html').read_text().replace('boot.js','minimal-boot.js').replace('wizard-ui.css','minimal-kit.css').replace('styles.css','minimal.css').replace('inbox.js','minimal.js').replace('render.js','minimal-render.js')
assets = {'minimal.js': ('text/javascript', (web/'inbox.js').read_text()), 'minimal-render.js': ('text/javascript', (web/'render.js').read_text()), 'minimal.css': ('text/css',(web/'styles.css').read_text()), 'minimal-kit.css': ('text/css',(web/'wizard-ui.css').read_text()), 'minimal-boot.js': ('text/javascript',(web/'boot.js').read_text()), 'layout.js': ('text/javascript',gate)}
# Python literals are safe in the inlined front (compose forbids dollar characters).
revision = hashlib.sha256(json.dumps({'html': html, 'assets': assets}, sort_keys=True).encode()).hexdigest()
(root/'mcp'/'minimal_assets.py').write_text('MINIMAL_REVISION = '+repr(revision)+'\nMINIMAL_HTML = '+repr(html)+'\nMINIMAL_ASSETS = '+repr(assets)+'\n')
