(function(){
  function getLang(){
    const saved = localStorage.getItem('lang');
    return saved === 'en' ? 'en' : 'fa';
  }

  function pick(raw, lang){
    if (typeof raw !== 'string' || !raw.includes('|')) return raw;
    const parts = raw.split('|');
    const en = (parts[0] || '').trim();
    const fa = (parts.slice(1).join('|') || '').trim();
    return lang === 'en' ? en : fa;
  }

  function apply(root){
    const lang = getLang();

    const html = document.documentElement;
    html.setAttribute('lang', lang);
    html.setAttribute('dir', lang === 'fa' ? 'rtl' : 'ltr');

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const raw = node.__i18nRaw || node.nodeValue;
      if (raw && raw.includes('|')) {
        node.__i18nRaw = raw;
        node.nodeValue = pick(raw, lang);
      }
    }

    const ATTRS = ['title','placeholder','aria-label'];
    document.querySelectorAll('*').forEach(el => {
      ATTRS.forEach(a => {
        const stored = el.dataset && el.dataset['i18n' + a.replace('-', '_')];
        const current = el.getAttribute(a);
        const raw = stored || current;
        if (raw && raw.includes('|')) {
          if (!stored && el.dataset) el.dataset['i18n' + a.replace('-', '_')] = raw;
          el.setAttribute(a, pick(raw, lang));
        }
      });
    });

    const btn = document.getElementById('langToggle');
    if (btn) btn.textContent = lang === 'fa' ? 'EN' : 'FA';
  }

  function setLang(lang){
    localStorage.setItem('lang', lang === 'en' ? 'en' : 'fa');
    apply(document);
  }

  function toggle(){
    setLang(getLang() === 'fa' ? 'en' : 'fa');
  }

  function mount(){
    apply(document);
    const btn = document.getElementById('langToggle');
    if (btn && !btn.__i18nMounted) {
      btn.__i18nMounted = true;
      btn.addEventListener('click', toggle);
    }
  }

  window.I18N = { getLang, setLang, pick, apply };
  window.__tPipe = function(s){ return pick(s, getLang()); };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
