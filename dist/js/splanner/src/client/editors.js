/** Create and edit dialogs for events, tasks and reminders, and the "Create new" menu. Built on the shared dialog and field helpers. */
import { announce, confirmDialog, glyph, h, labelled, openDialog, openMenu, replace, toast } from './kit.js';
import { errorText } from './store.js';
import { colorPicker } from './widgets.js';
import { addDays, allDayInstants, allDaySpan, dayDiff, fromIsoDate, fromLocalInput, isoDate, nextSlot, parseInstant, startOfDay, toInstant, toLocalInput, MINUTE, } from '../core/dates.js';
import { PRESETS, describeRule, nthWeekdayOfMonth, ordinal, presetOf, ruleFromPreset, weekdayName } from '../core/recurrence.js';
import { dueDateInput, dueFromDate, dueTimeInput } from '../core/tasks.js';
import { REMIND_BEFORE_CHOICES } from '../core/reminders.js';
import { PRIORITIES, PRIORITY_LABEL } from '../core/types.js';
const text = (id, label, value = '', max = 180, placeholder = '') => h('input', { type: 'text', id, value, placeholder, attrs: { maxlength: max, autocomplete: 'off', 'aria-label': label } });
function banner() {
    const el = h('div', { class: 'notice err', attrs: { role: 'alert' }, hidden: true });
    return {
        el,
        show(message) { replace(el, glyph('alert'), h('div', { class: 'grow', text: message })); el.hidden = false; announce(message, 'assertive'); },
        clear() { el.hidden = true; },
    };
}
function select(id, options, value) {
    const el = h('select', { id }, options.map((o) => h('option', { value: o.value, text: o.label })));
    el.value = value;
    return el;
}
function dateTimeRow(label, dateValue, timeValue) {
    const date = h('input', { type: 'date', value: dateValue, attrs: { 'aria-label': `${label} date` } });
    const time = h('input', { type: 'time', value: timeValue, attrs: { 'aria-label': `${label} time`, step: '60' } });
    const slot = h('div', { class: 'field-row dt-row' }, date, time);
    return { el: h('div', { class: 'field' }, h('span', { class: 'label', text: label }), slot), date, time, slot };
}
const UNITS = [
    { value: 'daily', label: 'days' }, { value: 'weekly', label: 'weeks' }, { value: 'monthly', label: 'months' }, { value: 'yearly', label: 'years' },
];
/** The "Repeats" menu with its custom panel and its end. Only edits and describes a rule; the server expands it. */
function repeatEditor(initial, startDay) {
    const preset = select('ev-repeat', PRESETS.map((p) => ({ value: p.id, label: p.label })), presetOf(initial));
    const every = h('input', { type: 'number', id: 'ev-every', value: '1', attrs: { min: '1', max: '999', step: '1', 'aria-label': 'Repeat every' } });
    const unit = select('ev-unit', UNITS.map((u) => ({ value: u.value, label: u.label })), 'weekly');
    unit.setAttribute('aria-label', 'Unit');
    const monthMode = select('ev-month-mode', [{ value: 'date', label: 'On day' }, { value: 'weekday', label: 'On weekday' }], 'date');
    const ends = select('ev-end', [{ value: 'never', label: 'Never' }, { value: 'until', label: 'On a date' }, { value: 'count', label: 'After a number of times' }], initial?.until ? 'until' : initial?.count ? 'count' : 'never');
    ends.setAttribute('aria-label', 'Ends');
    monthMode.setAttribute('aria-label', 'Each month');
    const endDate = h('input', { type: 'date', value: initial?.until ?? '', attrs: { 'aria-label': 'Last day of the repeat' } });
    const endCount = h('input', { type: 'number', value: String(initial?.count ?? 10), attrs: { min: '1', max: '999', step: '1', 'aria-label': 'Number of times' } });
    const summary = h('p', { class: 'muted small repeat-summary', attrs: { 'aria-live': 'polite' } });
    let touched = initial?.weekdays !== undefined && initial.weekdays.length > 0;
    const chosen = new Set(initial?.weekdays ?? []);
    const dayButtons = [1, 2, 3, 4, 5, 6, 7].map((day) => h('button', {
        class: 'chip', type: 'button', text: weekdayName(day, 'short'), attrs: { 'aria-label': weekdayName(day, 'long'), 'aria-pressed': 'false' },
        on: { click: () => { touched = true; if (chosen.has(day))
                chosen.delete(day);
            else
                chosen.add(day); paint(); } },
    }));
    const weekdayField = h('div', { class: 'field' }, h('span', { class: 'label', text: 'On' }), h('div', { class: 'chips', attrs: { role: 'group', 'aria-label': 'Days of the week' } }, dayButtons));
    const monthField = h('div', { class: 'field' }, h('span', { class: 'label', text: 'Each month' }), monthMode);
    const custom = h('div', { class: 'repeat-custom' }, h('div', { class: 'field' }, h('span', { class: 'label', text: 'Repeat every' }), h('div', { class: 'row nowrap' }, every, unit)), weekdayField, monthField);
    const endRow = h('div', { class: 'field' }, h('span', { class: 'label', text: 'Ends' }), h('div', { class: 'row nowrap' }, ends, endDate, endCount));
    const isoWeekday = (d) => (d.getDay() === 0 ? 7 : d.getDay());
    const endValue = () => {
        if (ends.value === 'until') {
            if (!endDate.value)
                return { error: 'Choose the last day of the repeat.', focus: endDate };
            if (endDate.value < isoDate(startDay()))
                return { error: 'The repeat can not end before the event starts.', focus: endDate };
            return { until: endDate.value, count: null };
        }
        if (ends.value === 'count') {
            const n = Number(endCount.value);
            if (!Number.isInteger(n) || n < 1 || n > 999)
                return { error: 'Enter how many times it repeats, from 1 to 999.', focus: endCount };
            return { until: null, count: n };
        }
        return { until: null, count: null };
    };
    const read = () => {
        const id = preset.value;
        if (id === 'none')
            return { rule: null };
        const end = endValue();
        if ('error' in end)
            return end;
        if (id !== 'custom')
            return { rule: ruleFromPreset(id, end) };
        const interval = Number(every.value);
        if (!Number.isInteger(interval) || interval < 1 || interval > 999)
            return { error: 'Repeat every 1 to 999 days, weeks, months or years.', focus: every };
        const freq = unit.value;
        if (freq === 'weekly' && chosen.size === 0)
            return { error: 'Choose at least one day of the week.', focus: dayButtons[0] };
        return { rule: { freq, interval, ...(freq === 'weekly' ? { weekdays: [...chosen].sort((a, b) => a - b) } : {}), ...(freq === 'monthly' ? { month_mode: monthMode.value } : {}), ...end } };
    };
    function paint() {
        const day = startDay();
        if (!touched) {
            chosen.clear();
            chosen.add(isoWeekday(day));
        }
        dayButtons.forEach((button, i) => button.setAttribute('aria-pressed', String(chosen.has(i + 1))));
        const n = nthWeekdayOfMonth(day);
        monthMode.options[0].text = `On day ${day.getDate()}`;
        monthMode.options[1].text = `On the ${n >= 5 ? 'last' : ordinal(n)} ${weekdayName(isoWeekday(day), 'long')}`;
        custom.hidden = preset.value !== 'custom';
        weekdayField.hidden = unit.value !== 'weekly';
        monthField.hidden = unit.value !== 'monthly';
        endRow.hidden = preset.value === 'none';
        endDate.hidden = ends.value !== 'until';
        endCount.hidden = ends.value !== 'count';
        const result = read();
        summary.hidden = preset.value === 'none';
        summary.textContent = 'rule' in result && result.rule ? `${describeRule(result.rule, day)}.` : 'error' in result ? result.error : '';
    }
    let previous = preset.value;
    const load = (rule) => {
        every.value = String(rule.interval);
        unit.value = rule.freq;
        monthMode.value = rule.month_mode ?? 'date';
        chosen.clear();
        for (const d of rule.weekdays ?? [])
            chosen.add(d);
        touched = chosen.size > 0;
    };
    if (initial)
        load(initial);
    preset.addEventListener('change', () => {
        const now = preset.value;
        if (now === 'custom' && previous !== 'custom') { // start from what was chosen, so "every 2 weeks" can become "every 2 weeks on Mon and Thu"
            const from = ruleFromPreset(previous, { until: null, count: null });
            if (from)
                load(from);
            else {
                every.value = '1';
                unit.value = 'weekly';
                touched = false;
            }
        }
        if (now !== 'none' && previous === 'none' && ends.value === 'until' && !endDate.value)
            ends.value = 'never';
        previous = now;
        paint();
    });
    for (const control of [every, unit, monthMode, ends, endDate, endCount]) {
        control.addEventListener('input', paint);
        control.addEventListener('change', paint);
    }
    paint();
    return { el: h('div', { class: 'repeat-box' }, labelled('Repeats', preset), custom, endRow, summary), read, refresh: paint };
}
/** Ask what a delete means for one occurrence of a repeating event. */
function askDeleteScope(event) {
    return openDialog({
        title: 'Delete repeating event', size: 'narrow',
        body: [h('p', { text: `“${event.title}” repeats. What should be deleted?` })],
        actions: [{ label: 'Only this event', kind: 'plain', run: () => 'one' }, { label: 'All events', kind: 'danger', run: () => 'all' }],
    }).result;
}
export function openEventDialog(ctx, options = {}) {
    const { store } = ctx;
    const existing = options.event;
    const master = existing ? store.masterOf(existing) : undefined;
    const series = Boolean(master?.recurrence);
    const occurrenceKey = existing ? (existing.occurrence ?? (series ? existing.start_at : undefined)) : undefined;
    const prefs = store.prefs;
    let start;
    let end;
    let allDay = false;
    if (existing) {
        allDay = existing.all_day;
        if (allDay) {
            const span = allDaySpan(existing);
            start = span.first;
            end = span.last;
        }
        else {
            start = parseInstant(existing.start_at);
            end = parseInstant(existing.end_at);
        }
    }
    else {
        allDay = options.allDay ?? false;
        start = options.start ?? nextSlot(store.now());
        end = options.end ?? new Date(start.getTime() + prefs.default_duration * MINUTE);
        if (allDay) {
            start = startOfDay(start);
            end = startOfDay(options.end ? addDays(options.end, -1) : start);
        }
    }
    const problem = banner();
    const title = text('ev-title', 'Title', existing?.title ?? options.title ?? '', 180, 'What are you planning?');
    const allDaySwitch = h('input', { type: 'checkbox', checked: allDay });
    const from = dateTimeRow('Starts', isoDate(start), allDay ? '09:00' : toLocalInput(start).slice(11));
    const to = dateTimeRow('Ends', isoDate(end), allDay ? '10:00' : toLocalInput(end).slice(11));
    const location = text('ev-location', 'Location', existing?.location ?? '', 200, 'Add a place or a link');
    const description = h('textarea', { id: 'ev-description', rows: 3, placeholder: 'Notes for this event', attrs: { maxlength: 5000 } });
    description.value = existing?.description ?? '';
    const colors = colorPicker((existing?.color ?? 'blue'), undefined, 'Event colour');
    const remindValue = existing ? existing.remind_before : prefs.default_remind >= 0 ? prefs.default_remind : null;
    const choices = REMIND_BEFORE_CHOICES.some((c) => c.value === remindValue) ? REMIND_BEFORE_CHOICES : [...REMIND_BEFORE_CHOICES, { label: `${remindValue} minutes before`, value: remindValue }];
    const remind = select('ev-remind', choices.map((c) => ({ value: c.value === null ? '' : String(c.value), label: c.label })), remindValue === null ? '' : String(remindValue));
    const startDay = () => fromIsoDate(from.date.value || isoDate(start));
    const repeat = repeatEditor(master?.recurrence ?? null, startDay);
    const syncAllDay = () => { from.time.hidden = allDaySwitch.checked; to.time.hidden = allDaySwitch.checked; from.slot.classList.toggle('single', allDaySwitch.checked); to.slot.classList.toggle('single', allDaySwitch.checked); };
    syncAllDay();
    allDaySwitch.addEventListener('change', syncAllDay);
    // Moving the start moves the end by the same amount, so a one-hour meeting stays a one-hour meeting.
    let lastStart = fromLocalInput(`${from.date.value}T${from.time.value || '00:00'}`);
    const onStartChange = () => {
        const next = fromLocalInput(`${from.date.value}T${from.time.value || '00:00'}`);
        const currentEnd = fromLocalInput(`${to.date.value}T${to.time.value || '00:00'}`);
        if (next && lastStart && currentEnd) {
            const moved = new Date(currentEnd.getTime() + (next.getTime() - lastStart.getTime()));
            to.date.value = isoDate(moved);
            if (!allDaySwitch.checked)
                to.time.value = toLocalInput(moved).slice(11);
            else if (to.date.value < from.date.value)
                to.date.value = from.date.value;
        }
        lastStart = next;
        repeat.refresh();
    };
    from.date.addEventListener('change', onStartChange);
    from.time.addEventListener('change', onStartChange);
    /** The times above are those of the occurrence that was opened. A change to them moves the whole series by the same amount. */
    const shiftSeries = (range) => {
        if (!master || !existing)
            return range;
        if (existing.all_day && allDaySwitch.checked) {
            const first = addDays(allDaySpan(master).first, dayDiff(allDaySpan(existing).first, fromIsoDate(from.date.value)));
            return allDayInstants(isoDate(first), isoDate(addDays(first, dayDiff(fromIsoDate(from.date.value), fromIsoDate(to.date.value)))));
        }
        const delta = parseInstant(range.start_at).getTime() - parseInstant(existing.start_at).getTime();
        const length = parseInstant(range.end_at).getTime() - parseInstant(range.start_at).getTime();
        const first = new Date(parseInstant(master.start_at).getTime() + delta);
        return { start_at: toInstant(first), end_at: toInstant(new Date(first.getTime() + length)) };
    };
    const readInput = () => {
        const name = title.value.trim();
        problem.clear();
        for (const control of [title, from.date, to.date, from.time, to.time])
            control.removeAttribute('aria-invalid');
        if (!name) {
            title.setAttribute('aria-invalid', 'true');
            problem.show('Give the event a title.');
            title.focus();
            return null;
        }
        let range;
        if (allDaySwitch.checked) {
            if (!from.date.value || !to.date.value) {
                problem.show('Choose the first and last day.');
                return null;
            }
            if (to.date.value < from.date.value) {
                to.date.setAttribute('aria-invalid', 'true');
                problem.show('The last day can not be before the first day.');
                to.date.focus();
                return null;
            }
            range = allDayInstants(from.date.value, to.date.value);
        }
        else {
            const s = fromLocalInput(`${from.date.value}T${from.time.value}`);
            const e = fromLocalInput(`${to.date.value}T${to.time.value}`);
            if (!s || !e) {
                problem.show('Enter a start and end date and time.');
                (s ? to.date : from.date).focus();
                return null;
            }
            if (e.getTime() <= s.getTime()) {
                to.date.setAttribute('aria-invalid', 'true');
                problem.show('An event has to end after it starts.');
                to.time.focus();
                return null;
            }
            range = { start_at: toInstant(s), end_at: toInstant(e) };
        }
        const rule = repeat.read();
        if ('error' in rule) {
            problem.show(rule.error);
            rule.focus.focus();
            return null;
        }
        return {
            title: name, ...(series || rule.rule ? shiftSeries(range) : range), all_day: allDaySwitch.checked, time_zone: store.zone, location: location.value.trim(), description: description.value,
            color: colors.value(), remind_before: remind.value === '' ? null : Number(remind.value), recurrence: rule.rule,
        };
    };
    const asOccurrence = () => (existing && occurrenceKey ? { ...existing, occurrence: occurrenceKey } : null);
    const noteAboutSeries = existing && series
        ? h('p', { class: 'muted small repeat-note', text: existing.moved ? 'This one was moved from its usual time. Saving changes the whole series.' : 'This event repeats. Saving changes the whole series; to change only this one, drag it in the calendar.' })
        : null;
    const handle = openDialog({
        title: existing ? (series ? 'Edit repeating event' : 'Edit event') : 'New event', eyebrow: existing ? 'CALENDAR' : 'QUICK CREATE', focus: () => title,
        body: [
            problem.el,
            noteAboutSeries,
            labelled('Title', title),
            h('label', { class: 'switch' }, allDaySwitch, h('span', { text: 'All day' })),
            h('div', { class: 'fields' }, from.el, to.el),
            repeat.el,
            labelled('Location', location),
            h('div', { class: 'fields' }, labelled('Reminder', remind), h('div', { class: 'field' }, h('span', { class: 'label', text: 'Colour' }), colors.el)),
            labelled('Description', description),
        ],
        actions: [
            ...(existing ? [{ label: 'Delete', kind: 'danger', run: async () => {
                        const occurrence = asOccurrence();
                        if (series && occurrence) {
                            const scope = await askDeleteScope(existing);
                            if (scope === null)
                                return undefined;
                            try {
                                if (scope === 'one') {
                                    await store.changeOccurrence(occurrence, 'skip');
                                    toast('Removed this event from the series');
                                }
                                else {
                                    await store.deleteEvent(existing.id);
                                    toast('Series deleted');
                                }
                                return null;
                            }
                            catch (error) {
                                problem.show(errorText(error));
                                return undefined;
                            }
                        }
                        if (!(await confirmDialog({ title: 'Delete this event?', message: `“${existing.title}” will be removed from your calendar.`, confirmLabel: 'Delete', danger: true })))
                            return undefined;
                        try {
                            await store.deleteEvent(existing.id);
                            toast('Event deleted');
                            return null;
                        }
                        catch (error) {
                            problem.show(errorText(error));
                            return undefined;
                        }
                    } }] : []),
            ...(existing?.moved && occurrenceKey ? [{ label: 'Undo move', kind: 'plain', run: async () => {
                        const occurrence = asOccurrence();
                        if (!occurrence)
                            return undefined;
                        try {
                            await store.changeOccurrence(occurrence, 'reset');
                            toast('Back at its usual time');
                            return null;
                        }
                        catch (error) {
                            problem.show(errorText(error));
                            return undefined;
                        }
                    } }] : []),
            { label: existing ? 'Save changes' : 'Create event', kind: 'primary', run: async () => {
                    const input = readInput();
                    if (!input)
                        return undefined;
                    try {
                        if (!existing)
                            return await store.createEvent(input);
                        const latest = store.masterOf(existing) ?? existing;
                        return await store.updateEvent(existing.id, { ...input, version: latest.version });
                    }
                    catch (error) {
                        problem.show(errorText(error));
                        return undefined;
                    }
                } },
        ],
    });
    return handle;
}
export function openTaskDialog(ctx, options = {}) {
    const { store } = ctx;
    const existing = options.task;
    const problem = banner();
    const title = text('task-title', 'Title', existing?.title ?? options.title ?? '', 180, 'What needs doing?');
    const description = h('textarea', { id: 'task-description', rows: 3, placeholder: 'Details, links, next steps', attrs: { maxlength: 5000 } });
    description.value = existing?.description ?? '';
    const priority = select('task-priority', PRIORITIES.map((p) => ({ value: p, label: PRIORITY_LABEL[p] })), existing?.priority ?? options.priority ?? 'normal');
    const date = h('input', { type: 'date', value: existing ? dueDateInput(existing) : options.day ? isoDate(options.day) : '', attrs: { 'aria-label': 'Due date' } });
    const time = h('input', { type: 'time', value: existing ? dueTimeInput(existing) : '', attrs: { 'aria-label': 'Due time (optional)', step: '60' } });
    const done = h('input', { type: 'checkbox', checked: existing?.status === 'done' });
    const clearDue = h('button', { class: 'btn small ghost', type: 'button', text: 'Clear', on: { click: () => { date.value = ''; time.value = ''; } } });
    return openDialog({
        title: existing ? 'Edit task' : 'New task', eyebrow: existing ? 'TASKS' : 'QUICK CREATE', focus: () => title,
        body: [
            problem.el,
            labelled('Title', title),
            h('div', { class: 'fields' }, labelled('Priority', priority), h('div', { class: 'field' }, h('span', { class: 'label', text: 'Due' }), h('div', { class: 'row nowrap' }, date, time, clearDue))),
            existing ? h('label', { class: 'switch' }, done, h('span', { text: 'Completed' })) : null,
            labelled('Details', description),
        ],
        actions: [
            ...(existing ? [{ label: 'Delete', kind: 'danger', run: async () => {
                        if (!(await confirmDialog({ title: 'Delete this task?', message: `“${existing.title}” will be removed.`, confirmLabel: 'Delete', danger: true })))
                            return undefined;
                        try {
                            await store.deleteTask(existing.id);
                            toast('Task deleted');
                            return null;
                        }
                        catch (error) {
                            problem.show(errorText(error));
                            return undefined;
                        }
                    } }] : []),
            { label: existing ? 'Save changes' : 'Add task', kind: 'primary', run: async () => {
                    problem.clear();
                    const name = title.value.trim();
                    if (!name) {
                        title.setAttribute('aria-invalid', 'true');
                        problem.show('Give the task a title.');
                        title.focus();
                        return undefined;
                    }
                    if (!date.value && time.value) {
                        problem.show('Choose a due date to go with that time.');
                        date.focus();
                        return undefined;
                    }
                    const input = { title: name, description: description.value, priority: priority.value, due_at: dueFromDate(date.value, time.value) };
                    try {
                        if (!existing)
                            return await store.createTask({ ...input, status: 'open' });
                        return await store.updateTask(existing.id, { ...input, status: done.checked ? 'done' : 'open', version: existing.version });
                    }
                    catch (error) {
                        problem.show(errorText(error));
                        return undefined;
                    }
                } },
        ],
    });
}
// ---------------------------------------------------------------------------------------------------------------- reminders
export function openReminderDialog(ctx, reminder) {
    const { store } = ctx;
    const problem = banner();
    const due = reminder ? parseInstant(reminder.due_at) : nextSlot(store.now(), 15);
    const title = text('rem-title', 'Title', reminder?.title ?? '', 180, 'What should we remind you about?');
    const date = h('input', { type: 'date', value: isoDate(due), attrs: { 'aria-label': 'Date' } });
    const time = h('input', { type: 'time', value: toLocalInput(due).slice(11), attrs: { 'aria-label': 'Time', step: '60' } });
    const linked = reminder?.entity_type === 'event';
    return openDialog({
        title: reminder ? 'Edit reminder' : 'New reminder', eyebrow: reminder ? 'REMINDERS' : 'QUICK CREATE', focus: () => title,
        body: [
            problem.el,
            linked ? h('p', { class: 'muted', text: 'This alert belongs to an event. Change its time or turn it off by editing the event.' }) : null,
            labelled('Title', title),
            linked ? null : h('div', { class: 'field' }, h('span', { class: 'label', text: 'When' }), h('div', { class: 'field-row dt-row' }, date, time)),
        ],
        actions: [
            ...(reminder ? [{ label: 'Delete', kind: 'danger', run: async () => {
                        try {
                            await store.deleteReminder(reminder.id);
                            toast('Reminder deleted');
                            return null;
                        }
                        catch (error) {
                            problem.show(errorText(error));
                            return undefined;
                        }
                    } }] : []),
            { label: reminder ? 'Save changes' : 'Add reminder', kind: 'primary', run: async () => {
                    problem.clear();
                    const name = title.value.trim();
                    if (!name) {
                        problem.show('Give the reminder a title.');
                        title.focus();
                        return undefined;
                    }
                    const when = fromLocalInput(`${date.value}T${time.value}`);
                    if (!linked && !when) {
                        problem.show('Choose a date and time.');
                        date.focus();
                        return undefined;
                    }
                    try {
                        if (!reminder)
                            return await store.createReminder(name, toInstant(when));
                        return await store.changeReminder(reminder.id, linked ? { title: name } : { title: name, due_at: toInstant(when) });
                    }
                    catch (error) {
                        problem.show(errorText(error));
                        return undefined;
                    }
                } },
        ],
    });
}
// ---------------------------------------------------------------------------------------------------------------- create menu
/** The topbar's "Create new" menu and every "+" shortcut share these actions. */
export function createEntries(ctx) {
    return [
        { label: 'Calendar event', icon: 'calendar', run: () => { openEventDialog(ctx, { start: defaultStart(ctx) }); } },
        { label: 'Task', icon: 'check-square', run: () => { openTaskDialog(ctx); } },
        { label: 'Note', icon: 'note', run: () => ctx.ui.go({ view: 'notes', id: 'new' }) },
        { label: 'Reminder', icon: 'bell', run: () => { openReminderDialog(ctx); } },
        { label: 'Sticky note', icon: 'sticky', run: () => { ctx.ui.go({ view: 'board', id: 'new' }); } },
    ];
}
/** A new event starts at the next half hour today, or at 09:00 on the day being viewed when that is another day. */
export function defaultStart(ctx) {
    const focus = ctx.ui.focus;
    const today = startOfDay(ctx.store.now());
    if (startOfDay(focus).getTime() === today.getTime())
        return nextSlot(ctx.store.now());
    return new Date(focus.getFullYear(), focus.getMonth(), focus.getDate(), 9, 0);
}
export function showCreateMenu(ctx, anchor) { openMenu(anchor, createEntries(ctx), { label: 'Create new', align: 'end' }); }
//# sourceMappingURL=editors.js.map