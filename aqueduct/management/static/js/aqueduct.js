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
    const dismissButton = event.target.closest('[data-dismiss]');
    if (dismissButton) dismissButton.parentElement.remove();
    const metricLink = event.target.closest('[data-metric-show]');
    if (metricLink) showMetric(event, metricLink);
    const checkAll = event.target.closest('[data-check-all]');
    if (checkAll) {
      const boxes = checkAll.closest('[data-checklist]').querySelectorAll('input[type=checkbox]');
      boxes.forEach((box) => { box.checked = checkAll.dataset.checkAll === 'true'; });
    }
    toggleRow(event);
  });
  document.addEventListener('auxclick', (event) => {
    if (event.button === 1) toggleRow(event);
  });

  // The chart's metric links show a series the page already holds instead of loading the page
  // again. The address, and the links and searches that reload this page, keep the choice.
  function showMetric(event, link) {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const shown = link.dataset.metricShow;
    const scope = link.closest('[data-metric-scope]');
    scope.querySelectorAll('[data-metric]').forEach((part) => { part.hidden = part.dataset.metric !== shown; });
    scope.querySelectorAll('[data-metric-show]').forEach((other) => {
      other.classList.toggle('btn-primary', other === link);
      if (other === link) other.setAttribute('aria-current', 'page');
      else other.removeAttribute('aria-current');
    });
    // The raw attribute: an empty href, the default metric, would resolve against the address set below.
    const metric = new URLSearchParams(link.getAttribute('href').split('?')[1] ?? '').get('metric');
    const keep = (url) => {
      if (metric === null) url.searchParams.delete('metric');
      else url.searchParams.set('metric', metric);
      return url;
    };
    history.replaceState(history.state, '', keep(new URL(location.href)));
    document.querySelectorAll('a[href]:not([data-metric-show])').forEach((a) => {
      const url = new URL(a.href);
      if (url.origin === location.origin && url.pathname === location.pathname) a.href = keep(url);
    });
    document.querySelectorAll('form[method=get]').forEach((form) => {
      if (new URL(form.action).pathname !== location.pathname) return;
      let input = form.querySelector('input[name=metric]');
      if (metric === null) return input?.remove();
      if (!input) {
        input = Object.assign(document.createElement('input'), { type: 'hidden', name: 'metric' });
        form.prepend(input);
      }
      input.value = metric;
    });
  }

  let pendingToggle;

  // A click on a log row follows its arrow link. Links in the row, text selection, and
  // double-clicks keep their usual effect, and a modified or middle click opens a new tab.
  function toggleRow(event) {
    clearTimeout(pendingToggle);
    const row = event.target.closest('[data-row-toggle]');
    if (!row || event.target.closest('a, button')) return;
    const href = row.querySelector('[data-row-toggle-link]').href;
    if (event.button === 1 || event.ctrlKey || event.metaKey || event.shiftKey) {
      window.open(href, '_blank', 'noopener');
      return;
    }
    if (event.detail > 1) return;
    pendingToggle = setTimeout(() => {
      if (!getSelection().toString()) window.location.href = href;
    }, 250);
  }

  // A link or submit button with data-confirm asks in the shared dialog first. The dialog posts
  // a link's address; a button submits its own form, so its name and value still count. A
  // modified or middle click on a link opens the confirm page behind it, as without JavaScript.
  let confirming = null;
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-confirm]');
    const dialog = document.getElementById('confirm-dialog');
    if (!trigger || !dialog || event.button !== 0) return;
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    confirming = trigger;
    const form = dialog.querySelector('form[method=post]');
    form.action = trigger.matches('a') ? trigger.href : '';
    dialog.querySelector('#confirm-title').textContent = trigger.dataset.confirm;
    const detail = dialog.querySelector('#confirm-detail');
    detail.textContent = trigger.dataset.confirmDetail;
    detail.hidden = !trigger.dataset.confirmDetail;
    dialog.querySelector('[data-confirm-submit]').textContent = trigger.dataset.confirmAction || 'Delete';
    dialog.showModal();
  });
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!form.closest('#confirm-dialog') || form.method === 'dialog') return;
    if (event.submitter?.getAttribute('formmethod') === 'dialog' || confirming?.matches('a')) return;
    event.preventDefault();
    const button = confirming;
    form.closest('dialog').close();
    button.form.requestSubmit(button);
  });
  document.addEventListener('close', (event) => {
    if (event.target.id === 'confirm-dialog') confirming?.focus();
  }, true);

  // Success and info messages leave after a few seconds, but not while the pointer or focus is on them.
  function autodismiss(alert) {
    setTimeout(() => {
      if (alert.matches(':hover, :focus-within')) autodismiss(alert);
      else alert.remove();
    }, 5000);
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-autodismiss]').forEach(autodismiss);
  });
})();
