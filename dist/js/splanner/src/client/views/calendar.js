/** The Calendar: week and day time grids (drag to move, drag the bottom edge to resize, click a gap to create) and a month overview. */
import { glyph, h, replace } from '../kit.js';
import { defaultStart, openEventDialog } from '../editors.js';
import { createTimeGrid } from '../timegrid.js';
import { formatTime, isoDate, monthGrid, occursOn, parseInstant, sameDay, startOfDay, weekDays, weekdayNames } from '../../core/dates.js';
import { shortRepeat } from '../../core/recurrence.js';
const MODES = [{ mode: 'week', label: 'Week' }, { mode: 'day', label: 'Day' }, { mode: 'month', label: 'Month' }];
export function mountCalendar(host, ctx) {
    const { store, ui } = ctx;
    let grid = null;
    let scrollTop = null;
    let lastMode = null;
    let lastFocusKey = '';
    const modeSwitch = () => h('div', { class: 'seg', attrs: { role: 'group', 'aria-label': 'Calendar view' } }, MODES.map(({ mode, label }) => h('button', { type: 'button', text: label, attrs: { 'aria-pressed': String(ui.mode === mode) }, on: { click: () => ui.setMode(mode) } })));
    function monthView() {
        const cells = monthGrid(ui.focus, store.prefs.week_start);
        store.ensureWindow(cells[0], cells[cells.length - 1]);
        const today = startOfDay(store.now());
        const format = store.prefs.time_format;
        const names = weekdayNames(store.prefs.week_start, 'short');
        const grid7 = h('div', { class: 'month-grid', attrs: { role: 'grid', 'aria-label': 'Month' } }, names.map((name) => h('div', { class: 'month-dow', attrs: { role: 'columnheader' }, text: name })));
        for (const day of cells) {
            const inMonth = day.getMonth() === ui.focus.getMonth();
            const dayEvents = store.calendarEvents().filter((e) => occursOn(e, day)).sort((a, b) => Number(b.all_day) - Number(a.all_day) || a.start_at.localeCompare(b.start_at));
            const shown = dayEvents.slice(0, 3);
            const cell = h('div', { class: `month-cell${inMonth ? '' : ' out'}${sameDay(day, today) ? ' is-today' : ''}`, attrs: { role: 'gridcell', 'data-date': isoDate(day) },
                on: { click: (event) => {
                        if (event.target !== cell && !event.target.classList.contains('month-num-row'))
                            return;
                        void openEventDialog(ctx, { start: new Date(day.getFullYear(), day.getMonth(), day.getDate(), 9, 0) });
                    } } }, h('div', { class: 'month-num-row' }, h('button', { class: 'month-num', type: 'button', text: String(day.getDate()), attrs: { 'aria-label': day.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' }) },
                on: { click: () => { ui.setFocus(day); ui.setMode('day'); } } })), shown.map((event) => monthChip(event, format)), dayEvents.length > shown.length ? h('button', { class: 'month-more', type: 'button', text: `+${dayEvents.length - shown.length} more`, on: { click: () => { ui.setFocus(day); ui.setMode('day'); } } }) : null);
            grid7.appendChild(cell);
        }
        return grid7;
    }
    function monthChip(event, format) {
        const time = event.all_day ? '' : `${formatTime(parseInstant(event.start_at), format)} `;
        return h('button', { class: `month-chip tone-${event.color}${event.recurrence ? ' is-repeating' : ''}`, type: 'button',
            attrs: { title: event.recurrence ? `${event.title} (${shortRepeat(event.recurrence).toLowerCase()})` : event.title,
                'aria-label': `${event.title}${event.all_day ? ', all day' : `, ${time.trim()}`}${event.recurrence ? `, ${shortRepeat(event.recurrence).toLowerCase()}` : ''}` },
            on: { click: (e) => { e.stopPropagation(); void openEventDialog(ctx, { event }); } } }, h('span', { class: 'chip-time', text: time }), event.title, event.recurrence ? h('span', { class: 'chip-repeat', attrs: { 'aria-hidden': 'true' } }, glyph('repeat')) : null);
    }
    const render = () => {
        const focused = document.activeElement?.closest?.('.ev') ?? null;
        const hadEvent = focused ? { id: focused.getAttribute('data-id'), occ: focused.getAttribute('data-occ') } : null;
        if (grid && lastMode === ui.mode)
            scrollTop = grid.scrollTarget().scrollTop;
        grid?.destroy();
        grid = null;
        const prefs = store.prefs;
        let body;
        if (ui.mode === 'month')
            body = monthView();
        else {
            const days = ui.mode === 'day' ? [ui.focus] : weekDays(ui.focus, prefs.week_start);
            grid = createTimeGrid(ctx, { days, startHour: 0, endHour: 24, hourHeight: 52, editable: true, onDay: (day) => { ui.setFocus(day); ui.setMode('day'); } });
            body = grid.el;
        }
        replace(host, h('section', { class: 'card cal-card', attrs: { 'aria-label': 'Calendar' } }, h('div', { class: 'card-head' }, modeSwitch(), h('button', { class: 'btn small', type: 'button', on: { click: () => { void openEventDialog(ctx, { start: defaultStart(ctx) }); } } }, glyph('plus'), 'Add event')), body));
        if (grid) {
            const target = grid.scrollTarget();
            const key = `${ui.mode}`;
            if (scrollTop !== null && lastMode === ui.mode && lastFocusKey === key)
                target.scrollTop = scrollTop;
            else {
                const now = store.now();
                const days = grid.el.querySelectorAll('.tg-col.is-today').length > 0;
                target.scrollTop = Math.max(0, ((days ? Math.max(0, now.getHours() - 2) : Math.max(0, prefs.day_start - 1)) * 52));
            }
            lastFocusKey = key;
        }
        lastMode = ui.mode;
        if (hadEvent) {
            const same = Array.from(host.querySelectorAll('.ev')).find((el) => el.getAttribute('data-id') === hadEvent.id && el.getAttribute('data-occ') === hadEvent.occ);
            same?.focus();
        }
    };
    render();
    const offs = [store.changed.on((kind) => { if (kind === 'events' || kind === 'all' || kind === 'prefs')
            render(); }), ui.changed.on((kind) => { if (kind === 'focus' || kind === 'mode')
            render(); })];
    return { destroy: () => { for (const off of offs)
            off(); grid?.destroy(); } };
}
//# sourceMappingURL=calendar.js.map