/* success_toast.js — Read visible success toast/message on the page after save. */
() => {
    const visible = (el) => {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden'
            && style.display !== 'none'
            && rect.width > 0
            && rect.height > 0;
    };
    const selector = [
        '[class*="message" i]', '[class*="toast" i]', '[class*="notice" i]',
        '[class*="notification" i]', '[role="alert"]', '.ant-message', '.semi-toast',
        '.el-message'
    ].join(',');
    const successTerms = [
        '\u4fdd\u5b58\u6210\u529f', '\u4fee\u6539\u6210\u529f',
        '\u63d0\u4ea4\u6210\u529f', '\u8bbe\u7f6e\u6210\u529f',
        '\u6210\u529f', 'success'
    ];
    for (const el of Array.from(document.querySelectorAll(selector)).filter(visible)) {
        const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
        const lower = text.toLowerCase();
        if (successTerms.some((term) => lower.includes(term.toLowerCase()))) {
            return text.slice(0, 300);
        }
    }
    return '';
}
