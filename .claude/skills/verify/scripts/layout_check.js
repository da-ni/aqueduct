// Evaluated by `aqv tour --layout` on every page. Returns the text that overflows its box, is
// cut off without a title, scrolls on a wide window, or wraps although it is a short label.
() => {
  const hiddenText = node => node.parentElement.closest('.sr-only, .tooltip-content, [hidden], svg');
  const visible = el => {
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2 && getComputedStyle(el).visibility !== 'hidden';
  };
  const describe = el => el.localName + (el.id ? '#' + el.id : '')
    + [...el.classList].slice(0, 3).map(c => '.' + c.replace(/:/g, '\\:')).join('');
  // Overflow that a reader sees: a shown text node reaching past the element's padding box.
  const textSpills = el => {
    const box = el.getBoundingClientRect();
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim() || hiddenText(node)) continue;
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) {
        if (r.width > 0 && (r.right > box.right + 1 || r.left < box.left - 1)) return true;
      }
    }
    return false;
  };
  const found = [];
  const root = document.documentElement;
  if (root.scrollWidth > innerWidth + 1) {
    found.push({kind: 'page', el: null, where: 'html', text: root.scrollWidth + 'px wide'});
  }
  for (const el of document.body.querySelectorAll('*')) {
    if (['svg', 'canvas', 'input', 'select', 'textarea', 'img', 'option'].includes(el.localName)
        || el.closest('svg, .sr-only, .tooltip-content') || !visible(el)) continue;
    const style = getComputedStyle(el);
    if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) {
      const scrolls = ['auto', 'scroll'].includes(style.overflowX);
      const kind = scrolls ? 'scrolls' : style.textOverflow === 'ellipsis' ? 'truncated'
        : ['hidden', 'clip'].includes(style.overflowX) ? 'clipped' : 'overflows';
      const titled = el.closest('[title]') || el.querySelector('[title]');
      if (scrolls ? innerWidth >= 768 : textSpills(el) && !(kind === 'truncated' && titled)) {
        found.push({kind, el, where: describe(el), text: el.innerText.trim().slice(0, 60)});
      }
    }
    const text = el.childElementCount === 0 ? el.textContent.trim() : '';
    if (text && text.length <= 32 && style.wordBreak !== 'break-all'
        && style.overflowWrap !== 'anywhere') {
      const range = document.createRange();
      range.selectNodeContents(el);
      const lines = new Set([...range.getClientRects()].filter(r => r.width > 1)
        .map(r => Math.round(r.bottom)));
      if (lines.size > 1) found.push({kind: 'wraps', el, where: describe(el), text});
    }
  }
  return found.filter(f => !f.el || f.kind === 'wraps'
      || !found.some(g => g !== f && g.el && g.kind !== 'wraps' && f.el.contains(g.el)))
    .map(({el, ...rest}) => rest);
}
