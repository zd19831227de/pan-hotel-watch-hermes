/* find_price_inputs.js — Find visible price input candidates on the pricing page. */
() => {
    const visible = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden'
            && style.display !== 'none'
            && rect.width > 0
            && rect.height > 0;
    };
    const text = (el) => (el && (el.innerText || el.textContent) || '')
        .replace(/\s+/g, ' ')
        .trim();
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
                    .map(function(name) { return CSS.escape(name); })
                    .join('.');
            }
            parts.unshift(seg);
            cur = cur.parentElement;
        }
        return parts.join(' > ');
    };
    const allowedTypes = new Set(['', 'text', 'search', 'number', 'tel']);
    const excludedTypes = new Set(['checkbox', 'radio', 'button', 'submit', 'reset', 'hidden', 'file']);
    // 日期类输入绝不能当价格输入候选：点击会弹日期选择器拦截后续操作
    // （2026-09-15 实测：词表乱码致全部 0 分时 #s_dateYearMode 被选中）。
    // 'date' 按词元匹配，避免误伤 update 等类名。
    const isDateInput = (input) => {
        const hints = [
            input.getAttribute('placeholder') || '',
            input.getAttribute('aria-label') || '',
            String(input.className || ''),
            input.id || '',
        ].join(' ').toLowerCase();
        if (hints.includes('日期') || hints.includes('yyyy')) return true;
        const tokens = hints.split(/[^a-z]+/).filter(Boolean);
        return tokens.includes('date');
    };
    const editablePriceInput = (input) => {
        if (!visible(input)) return false;
        if (input.tagName.toLowerCase() !== 'input') return false;
        const type = (input.getAttribute('type') || '').trim().toLowerCase();
        if (excludedTypes.has(type) || !allowedTypes.has(type)) return false;
        if (input.disabled || input.readOnly) return false;
        if (input.getAttribute('aria-disabled') === 'true') return false;
        if (isDateInput(input)) return false;
        return true;
    };
    // 价格语义词：nearby 命中越多 match_score 越高。
    // 2026-09-15 修复：本行自 7da9d5e 引入起就是乱码 ['???','???','???','???']，
    // 打分恒 0，价格输入框无法胜出。
    const inputTerms = ['卖价', '底价', '价格', '元'];
    const inputs = Array.from(document.querySelectorAll('input')).filter(editablePriceInput);
    return inputs.slice(0, 80).map(function(input, index) {
        const container = input.closest(
            '[class*="row"], [class*="Row"], [class*="cell"], [class*="Cell"],'
            + '[class*="card"], [class*="Card"], tr, td, li'
        ) || input.parentElement || input;
        const aria = input.getAttribute('aria-label') || '';
        const placeholder = input.getAttribute('placeholder') || '';
        const value = input.value || input.getAttribute('value') || '';
        const nearby = text(container).slice(0, 500);
        const matchScore = inputTerms.filter(function(term) { return nearby.includes(term); }).length;
        return {
            index: index,
            selector_hint: cssPath(input),
            tag: input.tagName.toLowerCase(),
            type: input.getAttribute('type') || '',
            aria_label: aria,
            placeholder: placeholder,
            value: value,
            nearby_text: nearby,
            match_score: matchScore,
        };
    });
}
