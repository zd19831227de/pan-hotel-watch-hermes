/* popup_container.js — Walk up from a search input to find the popup/dialog container. */
(searchSelector) => {
    const searchInput = searchSelector ? document.querySelector(searchSelector) : null;
    if (!searchInput) return null;
    const visible = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden'
            && style.display !== 'none'
            && rect.width > 0
            && rect.height > 0;
    };
    const cssPath = (el) => {
        if (!el) return '';
        if (el.id) return '#' + CSS.escape(el.id);
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
            let seg = cur.tagName.toLowerCase();
            if (cur.classList && cur.classList.length) {
                seg += '.' + Array.from(cur.classList)
                    .slice(0, 2)
                    .map((name) => CSS.escape(name))
                    .join('.');
            }
            parts.unshift(seg);
            cur = cur.parentElement;
        }
        return parts.join(' > ');
    };
    let el = searchInput.parentElement;
    for (let i = 0; i < 16 && el; i += 1) {
        const className = (el.className || '').toString();
        const id = el.id || '';
        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        const looksLikePopup = /drawer|panel|dialog|modal|popup|overlay|slide|chooseRate/i.test(`${className} ${id}`)
            || (text.includes('全选') && text.includes('确定') && text.length < 2000);
        if (looksLikePopup && visible(el)) {
            const rect = el.getBoundingClientRect();
            if (rect.width > 200 && rect.height > 120) return cssPath(el);
        }
        el = el.parentElement;
    }
    return null;
}
