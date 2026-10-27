/** The client's copy of the workspace, and the only module that talks to the server. Views read `store.data` and listen to `changed`;
 *  they never call `fetch`. Every mutation returns the server's record and updates the local copy, so what is on screen is what is saved. */
import { ApiError, Emitter, createClient, type Client } from './kit.js';
import { DAY, browserZone, parseInstant, toInstant } from '../core/dates.js';
import type {
  Bootstrap, CalEvent, Color, Counts, Exceptions, Note, NoteSummary, PlannerEvent, Prefs, Priority, Reminder, RepeatRule, SearchHit, Snapshot, StatusInfo, Sticky, Task,
} from '../core/types.js';

export type ChangeKind = 'events' | 'tasks' | 'notes' | 'reminders' | 'stickies' | 'prefs' | 'all';

export interface EventInput {
  title?: string; start_at?: string; end_at?: string; time_zone?: string; all_day?: boolean; description?: string; location?: string;
  color?: Color; remind_before?: number | null; recurrence?: RepeatRule | null; exceptions?: Exceptions; version?: number;
}
export interface TaskInput { title?: string; description?: string; status?: 'open' | 'done'; priority?: Priority; due_at?: string | null; version?: number }
export interface NoteInput { title?: string; content?: string; pinned?: boolean; version?: number }
export interface StickyInput { x?: number; y?: number; width?: number | null; height?: number | null; color?: Color; content?: string; version?: number }
export type ReminderChange = { action?: 'dismiss' | 'snooze' | 'restore'; minutes?: number; title?: string; due_at?: string };

const byStart = (a: PlannerEvent, b: PlannerEvent): number => a.start_at.localeCompare(b.start_at) || a.end_at.localeCompare(b.end_at);

function upsert<T extends { id: string }>(list: T[], item: T): T[] {
  const at = list.findIndex((x) => x.id === item.id);
  if (at === -1) return [item, ...list];
  const next = list.slice();
  next[at] = item;
  return next;
}

export class Store {
  readonly api: Client;
  readonly changed = new Emitter<ChangeKind>();
  readonly offline = new Emitter<ApiError | null>();
  boot!: Bootstrap;
  data!: Snapshot;
  prefs!: Prefs;
  counts!: Counts;
  readonly zone = browserZone();
  /** Server clock minus browser clock, so a due reminder is judged on the server's time even if this computer's clock is off. */
  private skewMs = 0;

  constructor() {
    this.api = createClient({ slug: 'splanner', onError: (error) => { if (error.code === 'offline') this.offline.emit(error); } });
  }

  now(): Date { return new Date(Date.now() + this.skewMs); }

  async load(): Promise<void> {
    this.boot = await this.api.get<Bootstrap>('/bootstrap');
    this.skewMs = new Date(this.boot.now).getTime() - Date.now();
    this.data = this.boot.data;
    this.prefs = this.boot.prefs;
    this.counts = this.boot.counts;
    this.sort();
    this.changed.emit('all');
  }

  /** Re-read everything (after a conflict, or when the page returns to the foreground). */
  async refresh(): Promise<void> {
    const window = this.data?.window;
    const fresh = await this.api.get<{ data: Snapshot; counts: Counts }>('/snapshot', window ? { from: window.from, to: window.to } : undefined);
    this.data = fresh.data;
    this.counts = fresh.counts;
    this.sort();
    this.changed.emit('all');
  }

  private sort(): void { this.data.events.sort(byStart); }

  private async recount(): Promise<void> { this.counts = await this.api.get<Counts>('/counts'); }

  private async afterConflict<T>(work: () => Promise<T>): Promise<T> {
    try { return await work(); } catch (error) {
      if (error instanceof ApiError && error.status === 409) await this.refresh().catch(() => undefined);
      throw error;
    }
  }

  // ------------------------------------------------------------------------------------------------------------ events
  /** Everything the calendar draws: events that do not repeat, and every loaded occurrence of those that do. */
  calendarEvents(): CalEvent[] {
    return [...this.data.events.filter((e) => !e.recurrence), ...this.data.occurrences];
  }

  /** The master record of a repeating event, for an occurrence the calendar is showing (or an event that does not repeat). */
  masterOf(event: Pick<CalEvent, 'id'>): PlannerEvent | undefined { return this.data.events.find((e) => e.id === event.id); }

  private windowLoad: Promise<void> | null = null;

  private async loadOccurrences(from: string, to: string): Promise<void> {
    const loaded = await this.api.get<{ occurrences: CalEvent[]; window: { from: string; to: string } }>('/occurrences', { from, to });
    this.data.occurrences = loaded.occurrences;
    this.data.window = loaded.window;
  }

  /** Repeating events are expanded by the server for a window around today. Looking at days outside it loads a new window (once, in the
   *  background); the views draw again when it arrives, so a person only ever notices this when jumping years ahead. */
  ensureWindow(first: Date, last: Date): void {
    const window = this.data.window;
    const from = first.getTime();
    const to = last.getTime() + DAY;
    if ((from >= parseInstant(window.from).getTime() && to <= parseInstant(window.to).getTime()) || this.windowLoad || !this.data.events.some((e) => e.recurrence)) return;
    this.windowLoad = this.loadOccurrences(toInstant(new Date(from - 300 * DAY)), toInstant(new Date(to + 400 * DAY)))
      .then(() => this.changed.emit('events'), () => undefined)
      .finally(() => { this.windowLoad = null; });
  }

  /** Re-read the occurrences of the repeating events for the window already loaded. */
  async refreshOccurrences(): Promise<void> { await this.loadOccurrences(this.data.window.from, this.data.window.to); }

  async createEvent(input: EventInput): Promise<PlannerEvent> {
    const created = await this.api.post<PlannerEvent>('/events', { time_zone: this.zone, ...input });
    this.data.events = upsert(this.data.events, created);
    this.sort();
    if (created.recurrence) await this.refreshOccurrences();
    await this.refreshReminders();
    this.counts.events += 1;
    this.changed.emit('events');
    return created;
  }

  async updateEvent(id: string, input: EventInput): Promise<PlannerEvent> {
    const before = this.data.events.find((e) => e.id === id);
    const updated = await this.afterConflict(() => this.api.patch<PlannerEvent>(`/events/${id}`, input));
    this.data.events = upsert(this.data.events, updated);
    this.sort();
    if (updated.recurrence || before?.recurrence) await this.refreshOccurrences();
    await this.refreshReminders();
    this.changed.emit('events');
    return updated;
  }

  async deleteEvent(id: string): Promise<void> {
    await this.api.del(`/events/${id}`);
    this.data.events = this.data.events.filter((e) => e.id !== id);
    this.data.occurrences = this.data.occurrences.filter((o) => o.id !== id);
    this.data.reminders = this.data.reminders.filter((r) => !(r.entity_type === 'event' && r.entity_id === id));
    this.counts.events -= 1;
    this.changed.emit('events');
    this.changed.emit('reminders');
  }

  private readonly moves = new Map<string, Promise<void>>();

  /** Move or resize with no round trip to wait for: the block follows the pointer at once, and a refusal puts the server's truth back.
   *  Moves of one event run one after another, each with the version the previous one produced, so holding an arrow key never
   *  sends a stale version and never turns into a conflict with ourselves. */
  moveEvent(event: CalEvent, start: Date, end: Date): Promise<void> {
    if (event.occurrence !== undefined) return this.changeOccurrence(event, { start, end });
    const startAt = toInstant(start);
    const endAt = toInstant(end);
    const current = this.data.events.find((e) => e.id === event.id) ?? (event as PlannerEvent);           // never overwrite a newer version with the block's older copy
    this.data.events = upsert(this.data.events, { ...current, start_at: startAt, end_at: endAt });
    this.sort();
    this.changed.emit('events');
    const run = (this.moves.get(event.id) ?? Promise.resolve()).catch(() => undefined).then(async () => {
      const latest = this.data.events.find((e) => e.id === event.id);
      try {
        await this.updateEvent(event.id, { start_at: startAt, end_at: endAt, version: latest?.version ?? event.version });
      } catch (error) {
        await this.refresh().catch(() => { this.data.events = upsert(this.data.events, event as PlannerEvent); this.sort(); this.changed.emit('events'); });
        throw error;
      }
    });
    this.moves.set(event.id, run);
    const forget = (): void => { if (this.moves.get(event.id) === run) this.moves.delete(event.id); };
    run.then(forget, forget);
    return run;
  }

  /** Change one occurrence of a repeating event and nothing else: move it (`start` and `end`), cancel it (`skip`) or put it back (`reset`).
   *  The change is a single entry in the event's `exceptions`; the server keeps the rest of the series as it was. Changes to one series
   *  run one after another, each with the version the previous one produced, like moves of a single event. */
  changeOccurrence(event: CalEvent, change: { start: Date; end: Date } | 'skip' | 'reset'): Promise<void> {
    const key = event.occurrence as string;
    if (typeof change !== 'string') {                                                           // show the move at once
      const startAt = toInstant(change.start);
      const endAt = toInstant(change.end);
      this.data.occurrences = this.data.occurrences.map((o) => (o.id === event.id && o.occurrence === key ? { ...o, start_at: startAt, end_at: endAt, moved: true } : o));
      this.changed.emit('events');
    }
    const run = (this.moves.get(event.id) ?? Promise.resolve()).catch(() => undefined).then(async () => {
      const master = this.data.events.find((e) => e.id === event.id);
      if (!master) return;
      const next: Exceptions = { ...master.exceptions };
      if (change === 'skip') next[key] = null;
      else if (change === 'reset') delete next[key];
      else {
        const startAt = toInstant(change.start);
        const endAt = toInstant(change.end);
        const naturalLength = parseInstant(master.end_at).getTime() - parseInstant(master.start_at).getTime();
        if (startAt === key && change.end.getTime() - change.start.getTime() === naturalLength) delete next[key];          // moved back to where it was
        else next[key] = { start_at: startAt, end_at: endAt };
      }
      try {
        await this.updateEvent(event.id, { exceptions: next, version: master.version });
      } catch (error) {
        await this.refresh().catch(() => undefined);
        throw error;
      }
    });
    this.moves.set(event.id, run);
    const forget = (): void => { if (this.moves.get(event.id) === run) this.moves.delete(event.id); };
    run.then(forget, forget);
    return run;
  }

  // ------------------------------------------------------------------------------------------------------------ tasks
  async createTask(input: TaskInput): Promise<Task> {
    const created = await this.api.post<Task>('/tasks', input);
    this.data.tasks = upsert(this.data.tasks, created);
    await this.recount();
    this.changed.emit('tasks');
    return created;
  }

  async updateTask(id: string, input: TaskInput): Promise<Task> {
    const updated = await this.afterConflict(() => this.api.patch<Task>(`/tasks/${id}`, input));
    this.data.tasks = upsert(this.data.tasks, updated);
    await this.recount();
    this.changed.emit('tasks');
    return updated;
  }

  /** Tick or untick: the checkbox changes immediately, and goes back if the server says no. */
  async toggleTask(task: Task): Promise<void> {
    const status = task.status === 'done' ? 'open' : 'done';
    const optimistic: Task = { ...task, status, completed_at: status === 'done' ? toInstant(this.now()) : null };
    this.data.tasks = upsert(this.data.tasks, optimistic);
    this.counts.openTasks += status === 'done' ? -1 : 1;
    this.changed.emit('tasks');
    try {
      const saved = await this.api.patch<Task>(`/tasks/${task.id}`, { status });
      this.data.tasks = upsert(this.data.tasks, saved);
      this.changed.emit('tasks');
    } catch (error) {
      this.data.tasks = upsert(this.data.tasks, task);
      this.counts.openTasks += status === 'done' ? 1 : -1;
      this.changed.emit('tasks');
      throw error;
    }
  }

  async deleteTask(id: string): Promise<void> {
    await this.api.del(`/tasks/${id}`);
    this.data.tasks = this.data.tasks.filter((t) => t.id !== id);
    await this.recount();
    this.changed.emit('tasks');
  }

  async clearCompleted(): Promise<number> {
    const { deleted } = await this.api.post<{ deleted: number }>('/tasks/clear-completed');
    this.data.tasks = this.data.tasks.filter((t) => t.status !== 'done');
    await this.recount();
    this.changed.emit('tasks');
    return deleted;
  }

  // ------------------------------------------------------------------------------------------------------------ notes
  getNote(id: string): Promise<Note> { return this.api.get<Note>(`/notes/${id}`); }

  private noteSummary(note: Note): NoteSummary {
    const { content: _content, editable: _editable, ...summary } = note;
    return summary;
  }

  async createNote(input: NoteInput): Promise<Note> {
    const created = await this.api.post<Note>('/notes', input);
    this.data.notes = upsert(this.data.notes, this.noteSummary(created));
    this.sortNotes();
    this.counts.notes += 1;
    this.changed.emit('notes');
    return created;
  }

  async updateNote(id: string, input: NoteInput): Promise<Note> {
    const updated = await this.afterConflict(() => this.api.patch<Note>(`/notes/${id}`, input));
    this.data.notes = upsert(this.data.notes, this.noteSummary(updated));
    this.sortNotes();
    this.changed.emit('notes');
    return updated;
  }

  async deleteNote(id: string): Promise<void> {
    await this.api.del(`/notes/${id}`);
    this.data.notes = this.data.notes.filter((n) => n.id !== id);
    this.counts.notes -= 1;
    this.changed.emit('notes');
  }

  private sortNotes(): void {
    this.data.notes.sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updated_at.localeCompare(a.updated_at));
  }

  // ------------------------------------------------------------------------------------------------------------ reminders
  async refreshReminders(): Promise<void> {
    this.data.reminders = await this.api.get<Reminder[]>('/reminders');
    this.changed.emit('reminders');
  }

  async createReminder(title: string, dueAt: string): Promise<Reminder> {
    const created = await this.api.post<Reminder>('/reminders', { title, due_at: dueAt });
    this.data.reminders = upsert(this.data.reminders, created);
    this.changed.emit('reminders');
    return created;
  }

  async changeReminder(id: string, change: ReminderChange): Promise<Reminder> {
    const updated = await this.api.patch<Reminder>(`/reminders/${id}`, change);
    this.data.reminders = upsert(this.data.reminders, updated);
    this.changed.emit('reminders');
    return updated;
  }

  async deleteReminder(id: string): Promise<void> {
    await this.api.del(`/reminders/${id}`);
    await this.refresh();           // an event's own alert also clears that event's "remind me" setting
  }

  /** What the server says is due now, with the server's clock. */
  async due(): Promise<Reminder[]> {
    const { due, now } = await this.api.get<{ due: Reminder[]; now: string }>('/reminders/due');
    this.skewMs = new Date(now).getTime() - Date.now();
    return due;
  }

  // ------------------------------------------------------------------------------------------------------------ stickies
  async createSticky(input: StickyInput): Promise<Sticky> {
    const created = await this.api.post<Sticky>('/stickies', input);
    this.data.stickies = [...this.data.stickies, created];
    this.counts.stickies += 1;
    this.changed.emit('stickies');
    return created;
  }

  async updateSticky(id: string, input: StickyInput, options: { silent?: boolean } = {}): Promise<Sticky> {
    const updated = await this.afterConflict(() => this.api.patch<Sticky>(`/stickies/${id}`, input));
    this.data.stickies = this.data.stickies.map((s) => (s.id === id ? updated : s));
    if (!options.silent) this.changed.emit('stickies');
    return updated;
  }

  async deleteSticky(id: string): Promise<void> {
    await this.api.del(`/stickies/${id}`);
    this.data.stickies = this.data.stickies.filter((s) => s.id !== id);
    this.counts.stickies -= 1;
    this.changed.emit('stickies');
  }

  // ------------------------------------------------------------------------------------------------------------ preferences, search, data
  async savePrefs(patch: Partial<Prefs>): Promise<Prefs> {
    this.prefs = await this.api.patch<Prefs>('/prefs', patch);
    this.changed.emit('prefs');
    return this.prefs;
  }

  search(query: string): Promise<SearchHit[]> { return this.api.get<SearchHit[]>('/search', { q: query }); }
  status(): Promise<StatusInfo> { return this.api.get<StatusInfo>('/status'); }
  backup(): Promise<{ name: string; folder: string; bytes: number }> { return this.api.post('/backup'); }
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.friendly;
  return error instanceof Error ? error.message : 'Something went wrong.';
}
