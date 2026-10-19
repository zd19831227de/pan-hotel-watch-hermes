/* price_block_candidates.js — Scan the EBK pricing calendar for price-block DOM nodes matching room-type and date. */
({roomType, checkinDate}) => {
    const visible = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden'
            && style.display !== 'none'
            && rect.width > 0
            && rect.height > 0;
    };
    const textOf = (el) => (el && (el.innerText || el.textContent) || '')
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
                    .map((name) => CSS.escape(name))
                    .join('.');
            }
            parts.unshift(seg);
            cur = cur.parentElement;
        }
        return parts.join(' > ');
    };

    const parseDate = (value) => {
        const match = String(value || '').match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
        if (!match) return null;
        return {
            year: Number(match[1]),
            month: Number(match[2]),
            day: Number(match[3]),
        };
    };
    const targetDate = parseDate(checkinDate);
    if (!targetDate) {
        return {
            status: 'invalid_checkin_date',
            day_column: null,
            candidates: [],
        };
    }
    const targetLabels = [
        `${targetDate.month}-${targetDate.day}`,
        `${String(targetDate.month).padStart(2, '0')}-${String(targetDate.day).padStart(2, '0')}`,
        `${targetDate.month}月${targetDate.day}日`,
    ];
    const targetDateText = `${targetDate.month}-${targetDate.day}`;

    const rawPriceNodes = Array.from(document.querySelectorAll(
        [
            '.son-room-box',
            '[class*="son-room-box"]',
            '[class*="room-box"]',
            'td',
            '[role="gridcell"]',
            '[class*="price"]',
            '[class*="Price"]'
        ].join(',')
    )).filter(visible).map((el) => {
        if (!visible(el)) return;
        if (el.matches('html, body, script, style, input, textarea, button, a')) return;
        const text = textOf(el);
        const className = (el.className || '').toString();
        if (!text || text.length > 180) return;
        if (/星期|端午|节|休/.test(text)) return;
        if (/calendar-day|day-list|date|week/i.test(className)) return;
        const numbers = text.match(/\d{2,5}(?:\.\d{1,2})?/g) || [];
        if (numbers.length < 2) return;
        const parentText = textOf(el.parentElement).slice(0, 260);
        const grandText = textOf(el.parentElement?.parentElement).slice(0, 320);
        const roomContainer = el.closest(
            '.son-room-list, [class*="son-room-list"], [class*="room-active"], tr'
        );
        const roomText = textOf(roomContainer || el.parentElement?.parentElement).slice(0, 520);
        const rect = el.getBoundingClientRect();
        if (rect.width > 260 || rect.height > 220) return;
        return {
            el,
            selector_hint: cssPath(el),
            text: text.slice(0, 120),
            parent_text: parentText,
            nearby_text: `${text} ${parentText} ${grandText}`.slice(0, 220),
            room_text: roomText,
            className,
            rect: {
                left: rect.left,
                right: rect.right,
                top: rect.top,
                bottom: rect.bottom,
                width: rect.width,
                height: rect.height,
                centerX: rect.left + rect.width / 2,
                centerY: rect.top + rect.height / 2,
            },
        };
    }).filter(Boolean);

    const roundedLefts = [];
    rawPriceNodes
        .map((item) => Math.round(item.rect.left))
        .sort((a, b) => a - b)
        .forEach((left) => {
            if (!roundedLefts.some((item) => Math.abs(item - left) <= 4)) {
                roundedLefts.push(left);
            }
        });
    const typicalWidth = rawPriceNodes.length
        ? rawPriceNodes
            .map((item) => item.rect.width)
            .sort((a, b) => a - b)[Math.floor(rawPriceNodes.length / 2)]
        : 80;

    const dayNodes = Array.from(document.querySelectorAll(
        '.calendar-day-list > *, [class*="calendar-day-list"] > *, [class*="day-list"] > *'
    )).filter(visible).map((el) => {
        const rect = el.getBoundingClientRect();
        return {
            text: textOf(el),
            className: (el.className || '').toString(),
            rect,
        };
    }).filter((item) => {
        if (!item.text || item.text.length > 80) return false;
        if (item.rect.width < 35 || item.rect.width > 180) return false;
        return targetLabels.some((label) => item.text.includes(label));
    });

    let dayColumn = null;
    if (dayNodes.length) {
        const node = dayNodes.sort((a, b) => a.rect.width - b.rect.width)[0];
        dayColumn = {
            strategy: 'header_cell',
            label: node.text,
            left: Math.round(node.rect.left),
            right: Math.round(node.rect.right),
            centerX: Math.round(node.rect.left + node.rect.width / 2),
        };
    }

    if (!dayColumn) {
        const dayList = Array.from(document.querySelectorAll(
            '.calendar-day-list, [class*="calendar-day-list"], [class*="day-list"]'
        )).filter(visible).find((el) => targetLabels.some((label) => textOf(el).includes(label)));
        const labels = dayList ? (textOf(dayList).match(/\d{1,2}-\d{1,2}/g) || []) : [];
        const targetIndex = labels.findIndex((label) => label === targetDateText);
        if (targetIndex >= 0 && roundedLefts[targetIndex] !== undefined) {
            const left = roundedLefts[targetIndex];
            dayColumn = {
                strategy: 'header_text_index_to_price_columns',
                label: labels[targetIndex],
                index: targetIndex,
                left: Math.round(left),
                right: Math.round(left + typicalWidth),
                centerX: Math.round(left + typicalWidth / 2),
            };
        }
    }

    if (!dayColumn) {
        return {
            status: 'date_column_not_found',
            day_column: null,
            candidates: [],
            debug: {
                target_labels: targetLabels,
                price_column_lefts: roundedLefts,
                price_node_count: rawPriceNodes.length,
            },
        };
    }

    const results = [];
    rawPriceNodes.forEach((item) => {
        const centerX = item.rect.centerX;
        if (centerX < dayColumn.left - 3 || centerX > dayColumn.right + 3) return;
        if (roomType && !item.room_text.includes(roomType)) return;
        const haystack = `${item.text} ${item.nearby_text} ${item.room_text}`;
        let score = 0;
        if (/son-room-box|room-box/i.test(item.className)) score += 20;
        if (/已开房|开房/.test(item.text)) score += 10;
        if (/已关房|关房/.test(item.text)) score += 4;
        if (/FS|预付|现付|担保|早餐|餐食|份/.test(item.nearby_text)) score += 4;
        if (/卖价|售价|底价|房价|价格/.test(haystack)) score += 6;
        score += 5;
        if (item.rect.width >= 35 && item.rect.height >= 28) score += 1;
        if (/full-room/i.test(item.className)) score -= 4;
        if (/取消|确定|确认|保存|提交|修改价格/.test(item.text)) score -= 20;
        if (score >= 12) {
            results.push({
                selector_hint: item.selector_hint,
                text: item.text,
                nearby_text: item.nearby_text,
                room_text: item.room_text,
                checkin_date: checkinDate,
                day_column: dayColumn,
                score,
                top: Math.round(item.rect.top),
                left: Math.round(item.rect.left),
                width: Math.round(item.rect.width),
                height: Math.round(item.rect.height),
                center_x: Math.round(item.rect.centerX),
                center_y: Math.round(item.rect.centerY),
            });
        }
    });

    return {
        status: results.length ? 'success' : 'price_block_not_found_for_date',
        day_column: dayColumn,
        candidates: results.sort((a, b) => {
        if (b.score !== a.score) return b.score - a.score;
        if (a.top !== b.top) return a.top - b.top;
        return a.left - b.left;
        // 2026-09-14：查房价需要单日列的全部房型价格块（多房型门店实测 60+），
        // 旧上限 20 会丢掉下半段；写价流程只取前 3 个点击，不受影响。
        }).slice(0, 200),
        debug: {
            target_labels: targetLabels,
            price_column_lefts: roundedLefts,
            price_node_count: rawPriceNodes.length,
        },
    };
}
