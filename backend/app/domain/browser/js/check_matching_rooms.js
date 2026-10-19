/* check_matching_rooms.js — In the room-type popup, find and check the matching room-type checkbox. */
({containerSelector, roomType}) => {
    const container = containerSelector ? document.querySelector(containerSelector) : document.body;
    if (!container) return {checked: false, strategy: 'container_missing', count: 0};
    const visible = (el) => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.visibility !== 'hidden'
            && style.display !== 'none'
            && rect.width > 0
            && rect.height > 0;
    };
    const isChecked = (el) => {
        if (el.matches('input[type="checkbox"]')) return el.checked;
        return el.getAttribute('aria-checked') === 'true';
    };
    const scrollAndClick = (el) => {
        el.scrollIntoView({block: 'center', inline: 'center'});
        // Small delay-like approach: fire click after scroll
        setTimeout(() => {}, 0);
        el.click();
    };
    const clickCheck = (el) => {
        if (!visible(el) || isChecked(el)) return false;
        scrollAndClick(el);
        return true;
    };
    const labelOf = (el) => {
        const host = el.closest('label, div, span, tr, li, td, a, button') || el.parentElement || el;
        return (host.innerText || host.textContent || '').replace(/\s+/g, ' ').trim();
    };
    const isActive = (el) => {
        if (el.matches('input[type="checkbox"], input[type="radio"]')) return el.checked;
        if (el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-checked') === 'true') return true;
        const host = el.parentElement || el;
        return el.classList.contains('active') || el.classList.contains('selected')
            || host.classList.contains('active') || host.classList.contains('selected');
    };

    // Step 1: 选「全部房型」筛选以展示全部房型。量态「非直连」会过滤掉房型，
    // 且弹窗默认又是它，所以必须主动切到「全部房型」；兼容 checkbox/radio/
    // tab/button 等形态。无「全部房型」可点时，退而取消已勾的量态「非直连」。
    const filterClickables = Array.from(container.querySelectorAll(
        'input[type="checkbox"], input[type="radio"], [role="checkbox"], [role="radio"], button, [role="tab"], a'
    )).filter(visible);
    const allRooms = filterClickables.find((el) => {
        const label = labelOf(el);
        // 精确命中优先；前缀命中仅限短标签（筛选 chip 短，房型行文本长），
        // 避免把名为「全部房型…」的房型行 checkbox 当成筛选选项。
        return label === '全部房型'
            || (label.startsWith('全部房型') && label.length <= 12);
    });
    let filterState = null;
    if (allRooms) {
        filterState = isActive(allRooms) ? 'all_room_types' : null;
        if (!isActive(allRooms)) {
            scrollAndClick(allRooms);
            return {checked: true, strategy: 'all_room_types_filter_clicked', count: 0, filterApplied: true};
        }
    } else {
        const nonDirect = filterClickables
            .filter((el) => el.matches('input[type="checkbox"], [role="checkbox"]'))
            .find((el) => {
                const label = labelOf(el);
                return label === '非直连' || label.startsWith('非直连');
            });
        if (nonDirect) {
            filterState = isChecked(nonDirect) ? 'non_direct' : null;
            if (isChecked(nonDirect)) {
                scrollAndClick(nonDirect);
                return {checked: true, strategy: 'non_direct_filter_cleared', count: 0, filterApplied: true};
            }
        }
    }

    // Step 2: Find the exact room-type row (shortest text containing roomType, excluding suffixed variants)
    const roomDescriptorSuffixes = ['城景', '江景', '海景', '湖景', '山景', '园景', '街景',
        '更大空间', '更宽', '更大', '高楼层', '低楼层', '无烟', '吸烟',
        '阳台', '露台', '套房', '连通', '家庭', '亲子'];
    const rows = Array.from(container.querySelectorAll('tr, li, label, [class*="row"], [class*="item"], [class*="list"] > *'));
    const matchingRows = [];
    rows.forEach((row) => {
        if (!visible(row)) return;
        const text = (row.innerText || row.textContent || '').replace(/\s+/g, ' ').trim();
        if (!text.includes(roomType) || text.length > 320) return;
        // Exclude rows where roomType is followed by a known descriptor suffix
        const idx = text.indexOf(roomType);
        const afterRoomType = text.slice(idx + roomType.length).trim();
        const hasSuffix = roomDescriptorSuffixes.some((suffix) => afterRoomType.startsWith(suffix));
        const checkbox = row.querySelector('input[type="checkbox"], [role="checkbox"]');
        if (!checkbox || !visible(checkbox)) return;
        matchingRows.push({row, checkbox, text, hasSuffix, textLen: text.length});
    });

    // Prefer non-suffixed rows; if none, fall back to all matching
    let candidates = matchingRows.filter((r) => !r.hasSuffix);
    if (!candidates.length) candidates = matchingRows;
    // Pick the one with shortest text (most likely the exact match)
    candidates.sort((a, b) => a.textLen - b.textLen);

    if (!candidates.length) return {checked: false, strategy: 'no_matching_checkbox', count: 0};

    let checked = 0;
    let alreadyChecked = 0;
    for (const c of candidates) {
        const cb = c.checkbox;
        if (isChecked(cb)) { alreadyChecked++; continue; }
        // Scroll the row into view, then click its checkbox
        c.row.scrollIntoView({block: 'center', inline: 'center'});
        cb.scrollIntoView({block: 'center', inline: 'center'});
        cb.click();
        checked++;
        break; // Only check the best-matching one
    }

    return {
        checked: checked > 0 || alreadyChecked > 0,
        strategy: checked > 0
            ? 'exact_match'
            : alreadyChecked > 0
                ? 'exact_match_already_checked'
                : 'no_matching_checkbox',
        count: checked + alreadyChecked,
        filterApplied: filterState,
    };
}
