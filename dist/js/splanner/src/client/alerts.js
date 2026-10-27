/** Reminder alerts while the page is open: polling, the in-app toast, the sound and (if allowed) a browser notification, plus the
 *  reminders panel behind the bell. SPlanner has no background service, so nothing can alert while the page is closed; the README says so. */
import { Emitter, announce, glyph, h, openDialog, openMenu, replace, toast } from './kit.js';
import { errorText } from './store.js';
import { openReminderDialog } from './editors.js';
import { moreButton } from './widgets.js';
import { SNOOZE_MINUTES, SNOOZE_UNITS, alertKey, customSnooze, dueNow, pending, reminderTime, snoozeLabel } from '../core/reminders.js';
import { formatTime, parseInstant } from '../core/dates.js';
export class Alerts {
    ctx;
    changed = new Emitter();
    due = [];
    seen = new Set();
    timer;
    firstPoll = true;
    audio = null;
    constructor(ctx) {
        this.ctx = ctx;
        ctx.store.changed.on((kind) => { if (kind === 'reminders' || kind === 'all' || kind === 'events')
            this.recompute(); });
    }
    start() {
        void this.poll();
        this.timer = window.setInterval(() => { void this.poll(); }, Math.max(5, this.ctx.store.boot.settings.reminderPollSeconds) * 1000);
        document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible')
            void this.poll(); });
    }
    stop() { if (this.timer !== undefined)
        window.clearInterval(this.timer); }
    recompute() {
        this.due = dueNow(this.ctx.store.data.reminders, this.ctx.store.now());
        this.changed.emit();
    }
    async poll() {
        let due;
        try {
            due = await this.ctx.store.due();
        }
        catch {
            return;
        }
        this.ctx.store.offline.emit(null);
        this.due = due;
        const fresh = due.filter((r) => !this.seen.has(alertKey(r)));
        for (const r of fresh)
            this.seen.add(alertKey(r));
        this.changed.emit();
        if (fresh.length > 0)
            this.announce(fresh);
        this.firstPoll = false;
    }
    announce(fresh) {
        const { store } = this.ctx;
        const first = fresh[0];
        const label = fresh.length === 1 ? `${first.title} · ${reminderTime(first, store.now(), store.prefs.time_format)}` : `${fresh.length} reminders are due`;
        toast(label, { timeoutMs: 15_000, action: { label: 'View', run: () => openRemindersDialog(this.ctx, this) } });
        announce(`Reminder: ${label}`, 'assertive');
        if (store.prefs.sound)
            this.playSound();
        if (store.prefs.browser_notifications && 'Notification' in window && Notification.permission === 'granted') {
            for (const r of fresh.slice(0, 3)) {
                try {
                    new Notification(r.title, { body: reminderTime(r, store.now(), store.prefs.time_format), tag: alertKey(r), silent: true });
                }
                catch { /* some browsers refuse outside a service worker */ }
            }
        }
    }
    /** Play the chosen sound (also used by the settings page to preview). Browsers may refuse until the person has clicked on the page. */
    playSound(file = this.ctx.store.prefs.sound_file, volume = this.ctx.store.prefs.sound_volume) {
        try {
            this.audio?.pause();
            this.audio = new Audio(`/assets/audio/${file}.mp3`);
            this.audio.volume = Math.min(1, Math.max(0, volume));
            void this.audio.play().catch(() => undefined);
        }
        catch { /* no audio available */ }
    }
    get firstPollDone() { return !this.firstPoll; }
}
/** Snooze a reminder for `minutes` and say when it will alert again. */
function snooze(ctx, reminder, minutes, after) {
    return ctx.store.changeReminder(reminder.id, { action: 'snooze', minutes }).then((changed) => {
        toast(`Snoozed until ${formatTime(parseInstant(changed.due_at), ctx.store.prefs.time_format)}`);
        after?.();
        return changed;
    });
}
/** "Snooze for ..." with a number and a unit; shows the time it will alert again as you type. */
export function openSnoozeDialog(ctx, reminder, after) {
    const amount = h('input', { type: 'number', id: 'snooze-amount', value: '20', attrs: { min: '1', max: '43200', step: '1', 'aria-label': 'Snooze for' } });
    const unit = h('select', { id: 'snooze-unit', attrs: { 'aria-label': 'Unit' } }, SNOOZE_UNITS.map((u) => h('option', { value: u.unit, text: u.label })));
    const preview = h('p', { class: 'muted small', attrs: { 'aria-live': 'polite' } });
    const problem = h('p', { class: 'error small', attrs: { role: 'alert' }, hidden: true });
    const read = () => customSnooze(Number(amount.value), unit.value);
    const update = () => {
        const result = read();
        preview.textContent = 'minutes' in result ? `Alerts again ${formatTime(new Date(ctx.store.now().getTime() + result.minutes * 60_000), ctx.store.prefs.time_format)}${result.minutes >= 1440 ? ` on ${new Date(ctx.store.now().getTime() + result.minutes * 60_000).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}` : ''}.` : '';
        problem.hidden = true;
    };
    for (const control of [amount, unit]) {
        control.addEventListener('input', update);
        control.addEventListener('change', update);
    }
    update();
    openDialog({
        title: 'Snooze', eyebrow: reminder.title.toUpperCase().slice(0, 40), size: 'narrow', focus: () => amount,
        body: [h('div', { class: 'field' }, h('span', { class: 'label', text: 'Snooze for' }), h('div', { class: 'row nowrap' }, amount, unit)), preview, problem],
        actions: [{ label: 'Snooze', kind: 'primary', run: async () => {
                    const result = read();
                    if ('error' in result) {
                        problem.textContent = result.error;
                        problem.hidden = false;
                        announce(result.error, 'assertive');
                        amount.focus();
                        return undefined;
                    }
                    try {
                        await snooze(ctx, reminder, result.minutes, after);
                        return true;
                    }
                    catch (error) {
                        problem.textContent = errorText(error);
                        problem.hidden = false;
                        return undefined;
                    }
                } }],
    });
}
/** Snooze choices as menu entries: +1, +5, +10, +15, +30, +60 minutes, and a custom length. */
export function snoozeEntries(ctx, reminder, after) {
    const fail = (error) => { toast(errorText(error), { kind: 'error' }); };
    return [
        ...SNOOZE_MINUTES.map((minutes) => ({ label: `Snooze ${snoozeLabel(minutes)}`, icon: 'clock', run: () => { snooze(ctx, reminder, minutes, after).catch(fail); } })),
        { label: 'Snooze custom…', icon: 'clock', run: () => openSnoozeDialog(ctx, reminder, after) },
    ];
}
/** The actions for one reminder, as menu entries (used by the dashboard, the panel and the bell). */
export function reminderEntries(ctx, reminder, after) {
    const run = (work, done) => {
        work().then(() => { if (done)
            toast(done); after?.(); }).catch((error) => toast(errorText(error), { kind: 'error' }));
    };
    return [
        { label: 'Dismiss', icon: 'check', run: () => run(() => ctx.store.changeReminder(reminder.id, { action: 'dismiss' })) },
        ...snoozeEntries(ctx, reminder, after),
        'separator',
        { label: 'Edit', icon: 'edit', run: () => { void openReminderDialog(ctx, reminder).result.then(() => after?.()); } },
        ...(reminder.entity_type === 'event' ? [] : [{ label: 'Delete', icon: 'trash', danger: true, run: () => run(() => ctx.store.deleteReminder(reminder.id), 'Reminder deleted') }]),
    ];
}
/** A visible "Snooze" button for a reminder that is due, so it is one click to put it off. */
function snoozeButton(ctx, reminder) {
    const button = h('button', { class: 'btn small ghost snooze-btn', type: 'button', attrs: { 'aria-haspopup': 'menu', 'aria-label': `Snooze ${reminder.title}` },
        on: { click: (event) => { event.stopPropagation(); openMenu(button, snoozeEntries(ctx, reminder), { label: 'Snooze', align: 'end' }); } } }, glyph('clock'), 'Snooze');
    return button;
}
export function reminderRow(ctx, reminder, options = {}) {
    const { store } = ctx;
    const now = store.now();
    const overdue = parseInstant(reminder.due_at).getTime() <= now.getTime();
    return h('li', { class: `reminder-row${overdue ? ' is-due' : ''}` }, h('span', { class: 'reminder-icon', attrs: { 'aria-hidden': 'true' } }, glyph(overdue ? 'bell' : 'clock')), h('div', { class: 'grow clip' }, h('span', { class: 'reminder-title clip', text: reminder.title }), h('span', { class: 'muted reminder-when', text: ` · ${reminderTime(reminder, now, store.prefs.time_format)}${options.due && overdue ? ' · due' : ''}` })), overdue ? snoozeButton(ctx, reminder) : null, moreButton(`Actions for ${reminder.title}`, () => reminderEntries(ctx, reminder)));
}
export function openRemindersDialog(ctx, alerts) {
    const list = h('ul', { class: 'plain-list reminder-list' });
    const draw = () => {
        const items = pending(ctx.store.data.reminders);
        replace(list, items.length ? items.map((r) => reminderRow(ctx, r, { due: true })) : h('li', { class: 'empty', text: 'No reminders waiting. Add one to be nudged at the right time.' }));
    };
    draw();
    const off = ctx.store.changed.on((kind) => { if (kind === 'reminders' || kind === 'all')
        draw(); });
    const dialog = openDialog({
        title: 'Reminders', eyebrow: alerts.due.length ? `${alerts.due.length} DUE NOW` : 'COMING UP', size: 'normal', noCancel: false, cancelLabel: 'Close',
        body: [list, h('p', { class: 'muted small', text: 'SPlanner alerts you while it is open in your browser. Reminders do not ring when SPlanner is closed.' })],
        actions: [{ label: 'Add reminder', kind: 'primary', run: () => { void openReminderDialog(ctx); return undefined; } }],
    });
    void dialog.result.then(off);
}
//# sourceMappingURL=alerts.js.map