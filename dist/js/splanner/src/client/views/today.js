/** The Today dashboard: three counts, this week's grid, your priorities, quick capture and reminders. */
import { glyph, h, replace } from '../kit.js';
import { openEventDialog, openReminderDialog, defaultStart } from '../editors.js';
import { reminderRow } from '../alerts.js';
import { taskRow } from '../task-ui.js';
import { createTimeGrid } from '../timegrid.js';
import { emptyState, iconTile } from '../widgets.js';
import { occursOn, sameDay, startOfDay, weekDays } from '../../core/dates.js';
import { priorities } from '../../core/tasks.js';
import { upcoming } from '../../core/reminders.js';
export function mountToday(host, ctx) {
    const { store, ui } = ctx;
    let grid = null;
    const stat = (icon, tone, value, label, go) => h('button', { class: 'card stat', type: 'button', on: { click: go }, attrs: { 'aria-label': `${value} ${label}` } }, iconTile(icon, tone), h('span', { class: 'stat-text' }, h('strong', { class: 'stat-value', text: String(value) }), h('span', { class: 'stat-label', text: label })));
    const render = () => {
        grid?.destroy();
        const now = store.now();
        const today = startOfDay(now);
        const days = weekDays(ui.focus, store.prefs.week_start);
        store.ensureWindow(today, today);
        const eventsToday = store.calendarEvents().filter((e) => occursOn(e, today)).length;
        const thisWeek = days.some((d) => sameDay(d, today));
        const prefs = store.prefs;
        grid = createTimeGrid(ctx, { days, startHour: prefs.day_start, endHour: Math.max(prefs.day_end, prefs.day_start + 2), hourHeight: 32, editable: false,
            onDay: (day) => { ui.setFocus(day); ui.setMode('day'); ui.go({ view: 'calendar' }); } });
        const top = priorities(store.data.tasks, 4);
        const reminders = upcoming(store.data.reminders, 4);
        replace(host, h('div', { class: 'stats' }, stat('calendar-day', 'green', eventsToday, eventsToday === 1 ? 'Event today' : 'Events today', () => { ui.today(); ui.go({ view: 'calendar' }); }), stat('check-circle', 'blue', store.counts.openTasks, store.counts.openTasks === 1 ? 'Open task' : 'Open tasks', () => ui.go({ view: 'tasks' })), stat('note', 'violet', store.data.notes.length, store.data.notes.length === 1 ? 'Your note' : 'Your notes', () => ui.go({ view: 'notes' }))), h('div', { class: 'today-grid' }, h('section', { class: 'card week-card', attrs: { 'aria-labelledby': 'week-title' } }, h('div', { class: 'card-head' }, h('h2', { id: 'week-title', text: thisWeek ? 'This week' : `Week of ${days[0]?.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}` }), h('button', { class: 'btn small', type: 'button', on: { click: () => { void openEventDialog(ctx, { start: defaultStart(ctx) }); } } }, glyph('plus'), 'Add event')), grid.el, grid.hiddenCount > 0 ? h('p', { class: 'muted small grid-note' }, `${grid.hiddenCount} ${grid.hiddenCount === 1 ? 'event is' : 'events are'} outside ${String(prefs.day_start).padStart(2, '0')}:00–${String(prefs.day_end).padStart(2, '0')}:00. `, h('button', { class: 'linkbtn', type: 'button', text: 'Open the full calendar', on: { click: () => ui.go({ view: 'calendar' }) } })) : null), h('div', { class: 'side-col' }, h('section', { class: 'card', attrs: { 'aria-labelledby': 'prio-title' } }, h('div', { class: 'card-head' }, h('h2', { id: 'prio-title', text: 'Your priorities' }), h('button', { class: 'textbtn', type: 'button', on: { click: () => ui.go({ view: 'tasks' }) } }, 'View all ', glyph('arrow-right'))), top.length ? h('ul', { class: 'plain-list task-list' }, top.map((t) => taskRow(ctx, t))) : emptyState('Nothing pending. Add a task to get going.', { label: 'Add task', run: () => ui.go({ view: 'tasks' }) })), h('section', { class: 'card capture', attrs: { 'aria-labelledby': 'cap-title' } }, h('h2', { id: 'cap-title', text: 'Quick capture' }), h('p', { class: 'muted', text: 'Ideas don’t need to be perfect. Capture the first thought.' }), h('button', { class: 'btn wide', type: 'button', on: { click: () => ui.go({ view: 'notes', id: 'new' }) } }, 'New note', glyph('plus'))), h('section', { class: 'card', attrs: { 'aria-labelledby': 'rem-title' } }, h('h2', { id: 'rem-title', text: 'Reminders' }), reminders.length ? h('ul', { class: 'plain-list reminder-list' }, reminders.map((r) => reminderRow(ctx, r))) : h('p', { class: 'muted', text: 'No alerts waiting.' }), h('button', { class: 'btn wide', type: 'button', on: { click: () => { openReminderDialog(ctx); } } }, 'Add alert', glyph('plus'))))));
    };
    render();
    const offs = [store.changed.on((kind) => { if (kind !== 'stickies')
            render(); }), ui.changed.on((kind) => { if (kind === 'focus')
            render(); })];
    return { destroy: () => { for (const off of offs)
            off(); grid?.destroy(); } };
}
//# sourceMappingURL=today.js.map