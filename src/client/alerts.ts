/** Reminder alerts while the page is open: polling, the in-app toast, the sound and (if allowed) a browser notification, plus the
 *  reminders panel behind the bell. SPlanner has no background service, so nothing can alert while the page is closed; the README says so. */
import { Emitter, announce, glyph, h, openDialog, openMenu, replace, toast, type MenuEntry } from './kit.js';
import { errorText, type Store } from './store.js';
import type { EditorContext } from './editors.js';
import { openReminderDialog } from './editors.js';
import { moreButton } from './widgets.js';
import { SNOOZE_MINUTES, SNOOZE_UNITS, alertKey, customSnooze, dueNow, pending, reminderTime, snoozeLabel, type SnoozeUnit } from '../core/reminders.js';
import { formatTime, parseInstant } from '../core/dates.js';
import type { Reminder } from '../core/types.js';

export class Alerts {
  readonly changed = new Emitter<void>();
  due: Reminder[] = [];
  private readonly seen = new Set<string>();
  private timer: number | undefined;
  private firstPoll = true;
  private audio: HTMLAudioElement | null = null;

  constructor(private readonly ctx: EditorContext) {
    ctx.store.changed.on((kind) => { if (kind === 'reminders' || kind === 'all' || kind === 'events') this.recompute(); });
  }

  start(): void {
    void this.poll();
    this.timer = window.setInterval(() => { void this.poll(); }, Math.max(5, this.ctx.store.boot.settings.reminderPollSeconds) * 1000);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void this.poll(); });
  }

  stop(): void { if (this.timer !== undefined) window.clearInterval(this.timer); }

  private recompute(): void {
    this.due = dueNow(this.ctx.store.data.reminders, this.ctx.store.now());
    this.changed.emit();
  }

  async poll(): Promise<void> {
    let due: Reminder[];
    try { due = await this.ctx.store.due(); } catch { return; }
    this.ctx.store.offline.emit(null);
    this.due = due;
    const fresh = due.filter((r) => !this.seen.has(alertKey(r)));
    for (const r of fresh) this.seen.add(alertKey(r));
    this.changed.emit();
    if (fresh.length > 0) this.announce(fresh);
    this.firstPoll = false;
  }

  private announce(fresh: Reminder[]): void {
    const { store } = this.ctx;
    const first = fresh[0] as Reminder;
    const label = fresh.length === 1 ? `${first.title} · ${reminderTime(first, store.now(), store.prefs.time_format)}` : `${fresh.length} reminders are due`;
    toast(label, { timeoutMs: 15_000, action: { label: 'View', run: () => openRemindersDialog(this.ctx, this) } });
    announce(`Reminder: ${label}`, 'assertive');
    if (store.prefs.sound) this.playSound();
    if (store.prefs.browser_notifications && 'Notification' in window && Notification.permission === 'granted') {
      for (const r of fresh.slice(0, 3)) {
        try { new Notification(r.title, { body: reminderTime(r, store.now(), store.prefs.time_format), tag: alertKey(r), silent: true }); } catch { /* some browsers refuse outside a service worker */ }
      }
    }
  }

  /** Play the chosen sound (also used by the settings page to preview). Browsers may refuse until the person has clicked on the page. */
  playSound(file = this.ctx.store.prefs.sound_file, volume = this.ctx.store.prefs.sound_volume): void {
    try {
      this.audio?.pause();
      this.audio = new Audio(`/assets/audio/${file}.mp3`);
      this.audio.volume = Math.min(1, Math.max(0, volume));
      void this.audio.play().catch(() => undefined);
    } catch { /* no audio available */ }
  }

  get firstPollDone(): boolean { return !this.firstPoll; }
}

/** Snooze a reminder for `minutes` and say when it will alert again. */
function snooze(ctx: EditorContext, reminder: Reminder, minutes: number, after?: () => void): Promise<Reminder> {
  return ctx.store.changeReminder(reminder.id, { action: 'snooze', minutes }).then((changed) => {
    toast(`Snoozed until ${formatTime(parseInstant(changed.due_at), ctx.store.prefs.time_format)}`);
    after?.();
    return changed;
  });
}

/** "Snooze for ..." with a number and a unit; shows the time it will alert again as you type. */
export function openSnoozeDialog(ctx: EditorContext, reminder: Reminder, after?: () => void): void {
  const amount = h('input', { type: 'number', id: 'snooze-amount', value: '20', attrs: { min: '1', max: '43200', step: '1', 'aria-label': 'Snooze for' } });
  const unit = h('select', { id: 'snooze-unit', attrs: { 'aria-label': 'Unit' } }, SNOOZE_UNITS.map((u) => h('option', { value: u.unit, text: u.label })));
  const preview = h('p', { class: 'muted small', attrs: { 'aria-live': 'polite' } });
  const problem = h('p', { class: 'error small', attrs: { role: 'alert' }, hidden: true });
  const read = (): { minutes: number } | { error: string } => customSnooze(Number(amount.value), unit.value as SnoozeUnit);
  const update = (): void => {
    const result = read();
    preview.textContent = 'minutes' in result ? `Alerts again ${formatTime(new Date(ctx.store.now().getTime() + result.minutes * 60_000), ctx.store.prefs.time_format)}${result.minutes >= 1440 ? ` on ${new Date(ctx.store.now().getTime() + result.minutes * 60_000).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}` : ''}.` : '';
    problem.hidden = true;
  };
  for (const control of [amount, unit]) { control.addEventListener('input', update); control.addEventListener('change', update); }
  update();
  openDialog<true>({
    title: 'Snooze', eyebrow: reminder.title.toUpperCase().slice(0, 40), size: 'narrow', focus: () => amount,
    body: [h('div', { class: 'field' }, h('span', { class: 'label', text: 'Snooze for' }), h('div', { class: 'row nowrap' }, amount, unit)), preview, problem],
    actions: [{ label: 'Snooze', kind: 'primary', run: async () => {
      const result = read();
      if ('error' in result) { problem.textContent = result.error; problem.hidden = false; announce(result.error, 'assertive'); amount.focus(); return undefined; }
      try { await snooze(ctx, reminder, result.minutes, after); return true; } catch (error) { problem.textContent = errorText(error); problem.hidden = false; return undefined; }
    } }],
  });
}

/** Snooze choices as menu entries: +1, +5, +10, +15, +30, +60 minutes, and a custom length. */
export function snoozeEntries(ctx: EditorContext, reminder: Reminder, after?: () => void): MenuEntry[] {
  const fail = (error: unknown): void => { toast(errorText(error), { kind: 'error' }); };
  return [
    ...SNOOZE_MINUTES.map((minutes): MenuEntry => ({ label: `Snooze ${snoozeLabel(minutes)}`, icon: 'clock', run: () => { snooze(ctx, reminder, minutes, after).catch(fail); } })),
    { label: 'Snooze custom…', icon: 'clock', run: () => openSnoozeDialog(ctx, reminder, after) },
  ];
}

/** The actions for one reminder, as menu entries (used by the dashboard, the panel and the bell). */
export function reminderEntries(ctx: EditorContext, reminder: Reminder, after?: () => void): MenuEntry[] {
  const run = (work: () => Promise<unknown>, done?: string): void => {
    work().then(() => { if (done) toast(done); after?.(); }).catch((error: unknown) => toast(errorText(error), { kind: 'error' }));
  };
  return [
    { label: 'Dismiss', icon: 'check', run: () => run(() => ctx.store.changeReminder(reminder.id, { action: 'dismiss' })) },
    ...snoozeEntries(ctx, reminder, after),
    'separator',
    { label: 'Edit', icon: 'edit', run: () => { void openReminderDialog(ctx, reminder).result.then(() => after?.()); } },
    ...(reminder.entity_type === 'event' ? [] : [{ label: 'Delete', icon: 'trash', danger: true, run: () => run(() => ctx.store.deleteReminder(reminder.id), 'Reminder deleted') } as MenuEntry]),
  ];
}

/** A visible "Snooze" button for a reminder that is due, so it is one click to put it off. */
function snoozeButton(ctx: EditorContext, reminder: Reminder): HTMLButtonElement {
  const button: HTMLButtonElement = h('button', { class: 'btn small ghost snooze-btn', type: 'button', attrs: { 'aria-haspopup': 'menu', 'aria-label': `Snooze ${reminder.title}` },
    on: { click: (event) => { event.stopPropagation(); openMenu(button, snoozeEntries(ctx, reminder), { label: 'Snooze', align: 'end' }); } } }, glyph('clock'), 'Snooze');
  return button;
}

export function reminderRow(ctx: EditorContext, reminder: Reminder, options: { due?: boolean } = {}): HTMLElement {
  const { store } = ctx;
  const now = store.now();
  const overdue = parseInstant(reminder.due_at).getTime() <= now.getTime();
  return h('li', { class: `reminder-row${overdue ? ' is-due' : ''}` },
    h('span', { class: 'reminder-icon', attrs: { 'aria-hidden': 'true' } }, glyph(overdue ? 'bell' : 'clock')),
    h('div', { class: 'grow clip' }, h('span', { class: 'reminder-title clip', text: reminder.title }),
      h('span', { class: 'muted reminder-when', text: ` · ${reminderTime(reminder, now, store.prefs.time_format)}${options.due && overdue ? ' · due' : ''}` })),
    overdue ? snoozeButton(ctx, reminder) : null,
    moreButton(`Actions for ${reminder.title}`, () => reminderEntries(ctx, reminder)),
  );
}

export function openRemindersDialog(ctx: EditorContext, alerts: Alerts): void {
  const list = h('ul', { class: 'plain-list reminder-list' });
  const draw = (): void => {
    const items = pending(ctx.store.data.reminders);
    replace(list, items.length ? items.map((r) => reminderRow(ctx, r, { due: true })) : h('li', { class: 'empty', text: 'No reminders waiting. Add one to be nudged at the right time.' }));
  };
  draw();
  const off = ctx.store.changed.on((kind) => { if (kind === 'reminders' || kind === 'all') draw(); });
  const dialog = openDialog<true>({
    title: 'Reminders', eyebrow: alerts.due.length ? `${alerts.due.length} DUE NOW` : 'COMING UP', size: 'normal', noCancel: false, cancelLabel: 'Close',
    body: [list, h('p', { class: 'muted small', text: 'SPlanner alerts you while it is open in your browser. Reminders do not ring when SPlanner is closed.' })],
    actions: [{ label: 'Add reminder', kind: 'primary', run: () => { void openReminderDialog(ctx); return undefined; } }],
  });
  void dialog.result.then(off);
}
