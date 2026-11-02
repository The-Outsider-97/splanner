/** SPlanner's persistent ringing alarm UI, polling and reminder controls. */
import { Emitter, announce, glyph, h, openDialog, openMenu, replace, toast, type MenuEntry } from './kit.js';
import { errorText } from './store.js';
import type { EditorContext } from './editors.js';
import { openReminderDialog } from './editors.js';
import { moreButton } from './widgets.js';
import { SNOOZE_MINUTES, SNOOZE_UNITS, alertKey, customSnooze, dueNow, pending, reminderTime, snoozeLabel, type SnoozeUnit } from '../core/reminders.js';
import { formatTime, parseInstant } from '../core/dates.js';
import type { Reminder } from '../core/types.js';

/** Persistent alarms use server-authoritative lifecycle state. Closing SPlanner
 *  does not provide OS-level background audio; the backend catches up on reopen. */
export class Alerts {
  readonly changed = new Emitter<void>();
  due: Reminder[] = [];
  private readonly seen = new Set<string>();
  private timer: number | undefined;
  private wakeTimer: number | undefined;
  private activeTimer: number | undefined;
  private firstPoll = true;
  private audio: HTMLAudioElement | null = null;
  private audioBlocked = false;
  private host: HTMLElement | null = null;
  private polling = false;
  private stopped = false;

  constructor(private readonly ctx: EditorContext) {
    ctx.store.changed.on((kind) => {
      if (kind === 'reminders' || kind === 'all' || kind === 'events') {
        this.recompute();
        this.scheduleWake();
      }
    });
  }

  start(): void {
    this.stopped = false;
    this.host = h('section', { class: 'alarm-stack', attrs: { 'aria-label': 'Ringing alarms', 'aria-live': 'polite' } });
    document.body.append(this.host);
    void this.poll();
    this.timer = window.setInterval(() => { void this.poll(); }, Math.max(5, this.ctx.store.boot.settings.reminderPollSeconds) * 1000);
    document.addEventListener('visibilitychange', this.onVisibility);
    this.scheduleWake();
  }

  private readonly onVisibility = (): void => {
    if (document.visibilityState === 'visible') void this.poll();
  };

  stop(): void {
    this.stopped = true;
    if (this.timer !== undefined) window.clearInterval(this.timer);
    if (this.wakeTimer !== undefined) window.clearTimeout(this.wakeTimer);
    if (this.activeTimer !== undefined) window.clearInterval(this.activeTimer);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.stopSound();
    this.host?.remove();
    this.host = null;
  }

  private recompute(): void {
    this.due = dueNow(this.ctx.store.data.reminders, this.ctx.store.now())
      .filter((r) => r.alarm_state === 'ringing');
    this.render();
    this.changed.emit();
  }

  private scheduleWake(): void {
    if (this.wakeTimer !== undefined) window.clearTimeout(this.wakeTimer);
    if (this.stopped || !this.ctx.store.data) return;
    const next = this.ctx.store.data.reminders
      .filter((r) => r.status === 'pending' && r.alarm_state !== 'ringing')
      .map((r) => parseInstant(r.due_at).getTime() - this.ctx.store.now().getTime())
      .filter((ms) => ms > 0)
      .sort((a, b) => a - b)[0];
    if (next !== undefined) this.wakeTimer = window.setTimeout(() => { void this.poll(); }, Math.max(50, Math.min(2_147_000_000, next + 100)));
  }

  async poll(): Promise<void> {
    if (this.polling || this.stopped) return;
    this.polling = true;
    try {
      const due = await this.ctx.store.due();
      this.ctx.store.offline.emit(null);
      this.due = due;
      // Synchronize client-side reminder state too: otherwise the dashboard
      // shows obsolete due dates after an automatic snooze.
      await this.ctx.store.refreshReminders();
      const fresh = due.filter((r) => !this.seen.has(alertKey(r)));
      for (const r of fresh) this.seen.add(alertKey(r));
      if (this.seen.size > 5000) this.seen.clear();
      this.render();
      this.changed.emit();
      if (fresh.length) this.announce(fresh);
      this.updateAudio();
      if (this.activeTimer !== undefined && this.due.length === 0) {
        window.clearInterval(this.activeTimer); this.activeTimer = undefined;
      } else if (this.activeTimer === undefined && this.due.length > 0) {
        // A one-second active check makes the two-minute cutoff visible at
        // the correct instant while the page is not browser-throttled.
        this.activeTimer = window.setInterval(() => { void this.poll(); }, 1000);
      }
    } catch {
      // Offline is already surfaced by the shared API client. Keep the
      // existing persistent alert visible until the server can be consulted.
    } finally {
      this.polling = false;
      this.firstPoll = false;
      this.scheduleWake();
    }
  }

  private async act(reminder: Reminder, action: 'dismiss' | 'snooze'): Promise<void> {
    this.stopSound();
    try {
      await this.ctx.store.changeReminder(reminder.id, {
        action, ...(action === 'snooze' ? { minutes: 5 } : {}), alarm_revision: reminder.alarm_revision,
      });
      await this.poll();
    } catch (error) {
      toast(errorText(error), { kind: 'error' });
      await this.poll();
    }
  }

  private render(): void {
    if (!this.host) return;
    replace(this.host, this.due.map((r) =>
      h('article', { class: 'persistent-alarm card', attrs: { role: 'alert', 'aria-label': `Alarm: ${r.title}` } },
        h('div', { class: 'eyebrow', text: 'ALARM · RINGING' }),
        h('strong', { class: 'persistent-alarm-title', text: r.title }),
        h('div', { class: 'muted small', text: reminderTime(r, this.ctx.store.now(), this.ctx.store.prefs.time_format) }),
        h('div', { class: 'row nowrap' },
          h('button', { class: 'btn small', type: 'button', text: 'Snooze 5 min', on: { click: () => { void this.act(r, 'snooze'); } } }),
          h('button', { class: 'btn primary small', type: 'button', text: 'Dismiss', on: { click: () => { void this.act(r, 'dismiss'); } } }),
          this.audioBlocked ? h('button', { class: 'btn small ghost', type: 'button', text: 'Enable sound', on: { click: () => { this.audioBlocked = false; this.updateAudio(); } } }) : null,
        ),
      )));
  }

  private announce(fresh: Reminder[]): void {
    const first = fresh[0] as Reminder;
    announce(`Alarm: ${first.title}`, 'assertive');
    if (this.ctx.store.prefs.browser_notifications && 'Notification' in window && Notification.permission === 'granted') {
      for (const r of fresh.slice(0, 3)) {
        try { new Notification(r.title, { body: 'Alarm is ringing. Open SPlanner to snooze or dismiss.', tag: alertKey(r), silent: true }); }
        catch { /* OS/browser notifications may not be available */ }
      }
    }
  }

  private stopSound(): void {
    if (this.audio) {
      this.audio.pause();
      this.audio.currentTime = 0;
      this.audio.src = '';
      this.audio.load();
      this.audio = null;
    }
  }

  private updateAudio(): void {
    if (!this.due.length || !this.ctx.store.prefs.sound || this.audioBlocked) { this.stopSound(); return; }
    if (this.audio) return;
    this.playSound(this.ctx.store.prefs.sound_file, this.ctx.store.prefs.sound_volume, true);
  }

  /** Used by Appearance for a single preview; looping is reserved for alarms. */
  playSound(file = this.ctx.store.prefs.sound_file, volume = this.ctx.store.prefs.sound_volume, loop = false): void {
    try {
      this.stopSound();
      const audio = new Audio(`/assets/audio/${file}.mp3`);
      audio.volume = Math.min(1, Math.max(0, volume));
      audio.loop = loop;
      this.audio = audio;
      void audio.play().catch(() => {
        if (loop && this.audio === audio) { this.audioBlocked = true; this.stopSound(); this.render(); }
      });
    } catch { if (loop) { this.audioBlocked = true; this.render(); } }
  }

  get firstPollDone(): boolean { return !this.firstPoll; }
}


/** Snooze a reminder for `minutes` and say when it will alert again. */
function snooze(ctx: EditorContext, reminder: Reminder, minutes: number, after?: () => void): Promise<Reminder> {
  return ctx.store.changeReminder(reminder.id, { action: 'snooze', minutes, alarm_revision: reminder.alarm_revision }).then((changed) => {
    toast(`Snoozed until ${formatTime(parseInstant(changed.due_at), ctx.store.prefs.time_format)}`);
    after?.();
    return changed;
  });
}

/** "Snooze for ..." with a number and a unit; shows when it will alert again. */
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

/** Snooze choices: 1, 5, 10, 15, 30, 60 minutes and custom. */
export function snoozeEntries(ctx: EditorContext, reminder: Reminder, after?: () => void): MenuEntry[] {
  const fail = (error: unknown): void => { toast(errorText(error), { kind: 'error' }); };
  return [
    ...SNOOZE_MINUTES.map((minutes): MenuEntry => ({ label: `Snooze ${snoozeLabel(minutes)}`, icon: 'clock', run: () => { snooze(ctx, reminder, minutes, after).catch(fail); } })),
    { label: 'Snooze custom…', icon: 'clock', run: () => openSnoozeDialog(ctx, reminder, after) },
  ];
}

/** Actions for one reminder (dashboard, panel, bell). */
export function reminderEntries(ctx: EditorContext, reminder: Reminder, after?: () => void): MenuEntry[] {
  const run = (work: () => Promise<unknown>, done?: string): void => {
    work().then(() => { if (done) toast(done); after?.(); }).catch((error: unknown) => toast(errorText(error), { kind: 'error' }));
  };
  return [
    { label: 'Dismiss', icon: 'check', run: () => run(() => ctx.store.changeReminder(reminder.id, { action: 'dismiss', alarm_revision: reminder.alarm_revision })) },
    ...snoozeEntries(ctx, reminder, after),
    'separator',
    { label: 'Edit', icon: 'edit', run: () => { void openReminderDialog(ctx, reminder).result.then(() => after?.()); } },
    ...(reminder.entity_type === 'event' ? [] : [{ label: 'Delete', icon: 'trash', danger: true, run: () => run(() => ctx.store.deleteReminder(reminder.id), 'Reminder deleted') } as MenuEntry]),
  ];
}

/** Visible snooze action on a due reminder. */
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
