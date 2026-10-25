"""Generate inline assets; original Notifications UI remains the default."""
import json, hashlib
from pathlib import Path
root = Path(__file__).resolve().parents[1]
web = root / 'web' / 'minimal'
gate = """(() => {
 const ours = new Set(['obsidian','obsidian-light','landscapes']);
 const minimal = new URL(location.href).searchParams.get('layout') === 'minimal';
 window.WizardApp?.theme.onChange(theme => {
   const skin = theme?.skin?.id || theme?.skin || document.documentElement.dataset.skin;
   if (!skin) return;
   const wanted = ours.has(skin);
   if (wanted !== minimal) { const url = new URL(location.href); if (wanted) url.searchParams.set('layout','minimal'); else url.searchParams.delete('layout'); location.replace(url.href); }
 });
})();"""
gate = (web/'layout.js').read_text()
html = (web/'index.html').read_text()
assets = {'minimal.js': ('text/javascript', (web/'inbox.js').read_text()), 'minimal-render.js': ('text/javascript', (web/'render.js').read_text()), 'minimal.css': ('text/css',(web/'styles.css').read_text()), 'minimal-kit.css': ('text/css',(web/'wizard-ui.css').read_text()), 'minimal-boot.js': ('text/javascript',(web/'boot.js').read_text()), 'layout.js': ('text/javascript',gate)}
# Python literals are safe in the inlined front (compose forbids dollar characters).
revision = hashlib.sha256(json.dumps({'html': html, 'assets': assets}, sort_keys=True).encode()).hexdigest()
(root/'mcp'/'minimal_assets.py').write_text('MINIMAL_REVISION = '+repr(revision)+'\nMINIMAL_HTML = '+repr(html)+'\nMINIMAL_ASSETS = '+repr(assets)+'\n')
