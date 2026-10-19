/* detect_popup.js — Scan the room-type selection popup for search inputs, checkboxes, confirm buttons and room items. */
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

    const result = {search_inputs: [], checkboxes: [], confirm_buttons: [], room_items: []};

    const searchClues = ['搜索', '输入', 'search', '房型名称', '房型名'];
    document.querySelectorAll('input[type="text"], input:not([type]), input[type="search"]').forEach((input) => {
        if (!visible(input)) return;
        const placeholder = input.placeholder || '';
        const parent = input.parentElement;
        const near = parent
            ? (parent.innerText || parent.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160)
            : '';
        let score = 0;
        searchClues.forEach((clue) => {
            if (placeholder.includes(clue) || near.includes(clue)) score += 2;
        });
        if (score > 0) {
            result.search_inputs.push({
                selector_hint: cssPath(input),
                placeholder,
                nearby_text: near,
                score,
            });
        }
    });

    document.querySelectorAll('input[type="checkbox"], [role="checkbox"]').forEach((el) => {
        if (!visible(el)) return;
        const parent = el.parentElement;
        const label = parent
            ? (parent.innerText || parent.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160)
            : '';
        result.checkboxes.push({
            selector_hint: cssPath(el),
            label,
            is_select_all: label.includes('全选') || label.includes('Select All'),
        });
    });

    const confirmClues = ['确定', '确认', 'OK', 'Confirm', '完成'];
    document.querySelectorAll('button, [role="button"], span[class*="btn"], div[class*="btn"]').forEach((el) => {
        if (!visible(el)) return;
        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        let score = 0;
        confirmClues.forEach((clue) => {
            if (text.includes(clue)) score += 3;
        });
        if (score > 0) {
            result.confirm_buttons.push({
                selector_hint: cssPath(el),
                text,
                score,
            });
        }
    });

    const roomClues = ['房型', '套餐', 'room'];
    document.querySelectorAll('li, [role="option"], [role="listitem"], tr, [class*="item"], [class*="row"]').forEach((el) => {
        if (!visible(el)) return;
        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        if (text.length <= 3 || text.length >= 240) return;
        let score = 0;
        roomClues.forEach((clue) => {
            if (text.includes(clue)) score += 1;
        });
        if (score > 0) {
            result.room_items.push({
                selector_hint: cssPath(el),
                text: text.slice(0, 160),
                score,
            });
        }
    });

    return result;
}
