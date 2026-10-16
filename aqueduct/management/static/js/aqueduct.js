// Theme switch and copy buttons for every page. _cdn.html loads this synchronously in <head>,
// so the stored theme is on <html> before the first paint and before page scripts read colours.
(() => {
  const root = document.documentElement;
  const storageKey = 'selectedTheme';

  let stored = null;
  try { stored = localStorage.getItem(storageKey); } catch { /* storage blocked */ }
  root.dataset.theme = stored === 'dark' ? 'dark' : 'light';

  function toggleTheme() {
    const theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = theme;
    try { localStorage.setItem(storageKey, theme); } catch { /* storage blocked */ }
  }

  // <button data-copy="text">, with an optional <span class="copy-label"> that reports the result.
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
  });
})();
