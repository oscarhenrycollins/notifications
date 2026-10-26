(() => {
 const ours = new Set(['obsidian','obsidian-light','landscapes']);
 const minimal = new URL(location.href).searchParams.get('layout') === 'minimal';
 window.WizardApp?.theme.onChange(theme => {
   const skin = theme?.skin?.id || theme?.skin || document.documentElement.dataset.skin;
   if (!skin) return;
   const wanted = ours.has(skin);
   if (wanted !== minimal) { const url = new URL(location.href); if (wanted) url.searchParams.set('layout','minimal'); else url.searchParams.delete('layout'); location.replace(url.href); }
 });
})();