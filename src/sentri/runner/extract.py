"""In-page script that extracts forms, inputs, buttons, list items and links."""

# Runs in the page. Builds selectors by preference: data-testid, id, name, then button text.
EXTRACT_JS = r"""
() => {
  const esc = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : v.replace(/["\\]/g, '\\$&');
  const sel = (el) => {
    const tag = el.tagName.toLowerCase();
    if (el.dataset && el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;
    if (el.id) return `#${esc(el.id)}`;
    if (el.name) return `${tag}[name="${el.name}"]`;
    if (tag === 'button' || (tag === 'input' && ['submit','button'].includes(el.type))) {
      const text = (el.innerText || el.value || '').trim();
      if (text) return `${tag}:has-text("${text.slice(0, 40)}")`;
    }
    if (el.type) return `${tag}[type="${el.type}"]`;
    return tag;
  };
  const label = (el) => {
    if (el.labels && el.labels.length) return el.labels[0].innerText.trim();
    return el.getAttribute('aria-label') || el.placeholder || '';
  };
  const fields = (root) => Array.from(root.querySelectorAll('input, textarea, select'))
    .filter((el) => el.type !== 'hidden')
    .map((el) => ({selector: sel(el), tag: el.tagName.toLowerCase(), type: el.type || '',
                   name: el.name || '', label: label(el), required: !!el.required}));
  const buttons = (root) => Array.from(root.querySelectorAll('button, input[type=submit]'))
    .map((el) => ({selector: sel(el), text: (el.innerText || el.value || '').trim().slice(0, 60)}));
  const forms = Array.from(document.querySelectorAll('form')).map((f) => ({
    selector: sel(f), action: f.getAttribute('action') || '', method: (f.method || 'get').toLowerCase(),
    fields: fields(f), buttons: buttons(f)}));
  const loose = forms.length ? [] : fields(document);
  const looseButtons = forms.length ? [] : buttons(document);
  const lists = Array.from(document.querySelectorAll('[data-testid]'))
    .map((el) => el.dataset.testid).filter((t) => /row|item|card|entry/i.test(t));
  return {
    title: document.title, forms, loose_fields: loose, loose_buttons: looseButtons,
    list_testids: Array.from(new Set(lists)),
    links: Array.from(document.querySelectorAll('a[href]')).map((a) => a.getAttribute('href')),
  };
}
"""
