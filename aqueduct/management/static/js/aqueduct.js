(() => {
  const root = document.documentElement;
  const storageKey = 'selectedTheme';

  let stored = null;
  try { stored = localStorage.getItem(storageKey); } catch { }
  root.dataset.theme = stored === 'dark' ? 'dark' : 'light';

  function toggleTheme() {
    const theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = theme;
    try { localStorage.setItem(storageKey, theme); } catch { }
  }

  function copy(button) {
    const label = button.querySelector('.copy-label');
    if (label) label.dataset.idle ??= label.textContent;
    const report = (copied) => {
      button.classList.add(copied ? 'st-ok' : 'st-err');
      if (label) label.textContent = copied ? 'Copied' : 'Copy failed';
      setTimeout(() => {
        button.classList.remove('st-ok', 'st-err');
        if (label) label.textContent = label.dataset.idle;
      }, 1500);
    };
    // The Clipboard API exists only on HTTPS and localhost.
    if (!navigator.clipboard) return report(false);
    navigator.clipboard.writeText(button.dataset.copy).then(() => report(true), () => report(false));
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-toggle-theme]')) toggleTheme();
    const copyButton = event.target.closest('[data-copy]');
    if (copyButton) copy(copyButton);
    toggleRow(event);
  });
  document.addEventListener('auxclick', (event) => {
    if (event.button === 1) toggleRow(event);
  });

  let pendingToggle;

  // A click on a log row follows its arrow link. Links in the row, text selection, and
  // double-clicks keep their usual effect, and a modified or middle click opens a new tab.
  function toggleRow(event) {
    const row = event.target.closest('[data-row-toggle]');
    if (!row || event.target.closest('a, button')) return;
    const href = row.querySelector('[data-row-toggle-link]').href;
    clearTimeout(pendingToggle);
    if (event.button === 1 || event.ctrlKey || event.metaKey || event.shiftKey) {
      window.open(href, '_blank', 'noopener');
      return;
    }
    if (event.detail > 1) return;
    pendingToggle = setTimeout(() => {
      if (!getSelection().toString()) window.location.href = href;
    }, 250);
  }

})();
