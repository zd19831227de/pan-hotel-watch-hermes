/* price_modal_button.js — Locate the save/confirm button inside the active Ctrip EBK price edit modal. */
(labels) => {
    const visible = (el) => {
        if (!el) return false;
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
        while (cur && cur.nodeType === Node.ELEMENT_NODE && parts.length < 6) {
            let seg = cur.tagName.toLowerCase();
            if (cur.classList && cur.classList.length) {
                seg += '.' + Array.from(cur.classList)
                    .slice(0, 2)
                    .map((name) => CSS.escape(name))
                    .join('.');
            }
            const parent = cur.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children)
                    .filter((child) => child.tagName === cur.tagName);
                if (siblings.length > 1) seg += `:nth-of-type(${siblings.indexOf(cur) + 1})`;
            }
            parts.unshift(seg);
            cur = cur.parentElement;
        }
        return parts.join(' > ');
    };
    const dialogSelector = [
        '[role="dialog"]', '[aria-modal="true"]',
        '[class*="modal" i]', '[class*="dialog" i]', '[class*="popup" i]',
        '[class*="popover" i]',
        '.ant-modal', '.ant-modal-content', '.semi-modal', '.semi-modal-content',
        '.el-dialog', '.el-dialog__wrapper'
    ].join(',');
    const titleTerms = [
        '\u4fee\u6539\u4ef7\u683c', '\u4ef7\u683c\u4fee\u6539',
        '\u8c03\u4ef7', '\u6539\u4ef7', '\u623f\u4ef7'
    ];
    const dialogs = Array.from(document.querySelectorAll(dialogSelector)).filter(visible);
    const scoredDialogs = dialogs.map((dialog, index) => {
        const text = (dialog.innerText || dialog.textContent || '').replace(/\s+/g, ' ').trim();
        const rect = dialog.getBoundingClientRect();
        let score = titleTerms.some((term) => text.includes(term)) ? 20 : 0;
        if (dialog.matches('[role="dialog"], [aria-modal="true"]')) score += 6;
        // 2026-09-15：修改价格弹窗容器是 class="popover pop-amendprice-table-box"，
        // 旧选择器无 popover，只能靠 body.modal-open 命中——body 是全页容器，
        // 优先级必须压到真实弹层之下，否则可能选到弹层外的按钮。
        if (/popover/i.test(String(dialog.className || ''))) score += 5;
        if (/modal|dialog|popup/i.test(String(dialog.className || ''))) score += 3;
        if (dialog.tagName === 'BODY') score -= 15;
        if (rect.width >= 240 && rect.height >= 120) score += 2;
        score += index / 1000;
        return { dialog, score, text };
    }).filter((item) => item.score > 0).sort((a, b) => b.score - a.score);
    for (const item of scoredDialogs) {
        const buttons = Array.from(item.dialog.querySelectorAll('button, [role="button"]'))
            .filter(visible)
            .filter((button) => {
                const rawText = button.innerText || button.textContent
                    || button.getAttribute('aria-label') || '';
                const text = rawText.trim();
                return labels.some((label) => text.includes(label));
            });
        if (buttons.length > 0) {
            return {
                selector: cssPath(buttons[0]),
                modal_text: item.text.slice(0, 300),
                button_text: (
                    buttons[0].innerText || buttons[0].textContent || ''
                ).trim().slice(0, 80),
            };
        }
    }
    return null;
}
