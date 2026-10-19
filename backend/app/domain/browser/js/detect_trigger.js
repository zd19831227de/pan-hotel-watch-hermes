/* detect_trigger.js — Scan DOM for room-type quick-select trigger elements. */
() => {
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
    const roomClues = ['快速选择房型', '快速选择', '房型', '房型名称', '房型名', '房型选择', '选择房型', '全部房型'];
    const results = [];
    const all = document.querySelectorAll(
        '[role], button, a, span, div, [class*="select"], [class*="dropdown"], [class*="trigger"], [class*="picker"]'
    );
    all.forEach((el) => {
        if (!visible(el)) return;
        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        const ariaLabel = el.getAttribute('aria-label') || '';
        const role = el.getAttribute('role') || el.tagName.toLowerCase();
        const title = el.getAttribute('title') || '';
        const placeholder = el.getAttribute('placeholder') || '';
        const className = (el.className || '').toString();
        const haystack = `${text} ${ariaLabel} ${title} ${placeholder} ${className}`;
        let score = 0;
        roomClues.forEach((clue) => {
            if (haystack.includes(clue)) score += clue.includes('快速') ? 6 : 3;
        });
        if (role === 'combobox' || role === 'searchbox' || role === 'button') score += 1;
        if (/select|dropdown|picker|trigger/i.test(className)) score += 1;
        if (text.length > 0 && text.length < 100 && score > 0) {
            results.push({
                selector_hint: cssPath(el),
                role,
                text: text.slice(0, 100),
                aria_label: ariaLabel,
                score,
            });
        }
    });
    return results.sort((a, b) => b.score - a.score);
}
