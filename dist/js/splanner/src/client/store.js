/** The client's copy of the workspace, and the only module that talks to the server. Views read `store.data` and listen to `changed`;
 *  they never call `fetch`. Every mutation returns the server's record and updates the local copy, so what is on screen is what is saved. */
import { ApiError, Emitter, createClient } from './kit.js';
import { DAY, browserZone, parseInstant, toInstant } from '../core/dates.js';
const byStart = (a, b) => a.start_at.localeCompare(b.start_at) || a.end_at.localeCompare(b.end_at);
function upsert(list, item) {
    const at = list.findIndex((x) => x.id === item.id);
    if (at === -1)
        return [item, ...list];
    const next = list.slice();
    next[at] = item;
    return next;
}
export class Store {
    api;
    changed = new Emitter();
    offline = new Emitter();
    boot;
    data;
    prefs;
    counts;
    zone = browserZone();
    /** Server clock minus browser clock, so a due reminder is judged on the server's time even if this computer's clock is off. */
    skewMs = 0;
    constructor() {
        this.api = createClient({ slug: 'splanner', onError: (error) => { if (error.code === 'offline')
                this.offline.emit(error); } });
    }
    now() { return new Date(Date.now() + this.skewMs); }
    async load() {
        this.boot = await this.api.get('/bootstrap');
        this.skewMs = new Date(this.boot.now).getTime() - Date.now();
        this.data = this.boot.data;
        this.prefs = this.boot.prefs;
        this.counts = this.boot.counts;
        this.sort();
        this.changed.emit('all');
    }
    /** Re-read everything (after a conflict, or when the page returns to the foreground). */
    async refresh() {
        const window = this.data?.window;
        const fresh = await this.api.get('/snapshot', window ? { from: window.from, to: window.to } : undefined);
        this.data = fresh.data;
        this.counts = fresh.counts;
        this.sort();
        this.changed.emit('all');
    }
    sort() { this.data.events.sort(byStart); }
    async recount() { this.counts = await this.api.get('/counts'); }
    async afterConflict(work) {
        try {
            return await work();
        }
        catch (error) {
            if (error instanceof ApiError && error.status === 409)
                await this.refresh().catch(() => undefined);
            throw error;
        }
    }
    // ------------------------------------------------------------------------------------------------------------ events
    /** Everything the calendar draws: events that do not repeat, and every loaded occurrence of those that do. */
    calendarEvents() {
        return [...this.data.events.filter((e) => !e.recurrence), ...this.data.occurrences];
    }
    /** The master record of a repeating event, for an occurrence the calendar is showing (or an event that does not repeat). */
    masterOf(event) { return this.data.events.find((e) => e.id === event.id); }
    windowLoad = null;
    async loadOccurrences(from, to) {
        const loaded = await this.api.get('/occurrences', { from, to });
        this.data.occurrences = loaded.occurrences;
        this.data.window = loaded.window;
    }
    /** Repeating events are expanded by the server for a window around today. Looking at days outside it loads a new window (once, in the
     *  background); the views draw again when it arrives, so a person only ever notices this when jumping years ahead. */
    ensureWindow(first, last) {
        const window = this.data.window;
        const from = first.getTime();
        const to = last.getTime() + DAY;
        if ((from >= parseInstant(window.from).getTime() && to <= parseInstant(window.to).getTime()) || this.windowLoad || !this.data.events.some((e) => e.recurrence))
            return;
        this.windowLoad = this.loadOccurrences(toInstant(new Date(from - 300 * DAY)), toInstant(new Date(to + 400 * DAY)))
            .then(() => this.changed.emit('events'), () => undefined)
            .finally(() => { this.windowLoad = null; });
    }
    /** Re-read the occurrences of the repeating events for the window already loaded. */
    async refreshOccurrences() { await this.loadOccurrences(this.data.window.from, this.data.window.to); }
    async createEvent(input) {
        const created = await this.api.post('/events', { time_zone: this.zone, ...input });
        this.data.events = upsert(this.data.events, created);
        this.sort();
        if (created.recurrence)
            await this.refreshOccurrences();
        await this.refreshReminders();
        this.counts.events += 1;
        this.changed.emit('events');
        return created;
    }
    async updateEvent(id, input) {
        const before = this.data.events.find((e) => e.id === id);
        const updated = await this.afterConflict(() => this.api.patch(`/events/${id}`, input));
        this.data.events = upsert(this.data.events, updated);
        this.sort();
        if (updated.recurrence || before?.recurrence)
            await this.refreshOccurrences();
        await this.refreshReminders();
        this.changed.emit('events');
        return updated;
    }
    async deleteEvent(id) {
        await this.api.del(`/events/${id}`);
        this.data.events = this.data.events.filter((e) => e.id !== id);
        this.data.occurrences = this.data.occurrences.filter((o) => o.id !== id);
        this.data.reminders = this.data.reminders.filter((r) => !(r.entity_type === 'event' && r.entity_id === id));
        this.counts.events -= 1;
        this.changed.emit('events');
        this.changed.emit('reminders');
    }
    moves = new Map();
    /** Move or resize with no round trip to wait for: the block follows the pointer at once, and a refusal puts the server's truth back.
     *  Moves of one event run one after another, each with the version the previous one produced, so holding an arrow key never
     *  sends a stale version and never turns into a conflict with ourselves. */
    moveEvent(event, start, end) {
        if (event.occurrence !== undefined)
            return this.changeOccurrence(event, { start, end });
        const startAt = toInstant(start);
        const endAt = toInstant(end);
        const current = this.data.events.find((e) => e.id === event.id) ?? event; // never overwrite a newer version with the block's older copy
        this.data.events = upsert(this.data.events, { ...current, start_at: startAt, end_at: endAt });
        this.sort();
        this.changed.emit('events');
        const run = (this.moves.get(event.id) ?? Promise.resolve()).catch(() => undefined).then(async () => {
            const latest = this.data.events.find((e) => e.id === event.id);
            try {
                await this.updateEvent(event.id, { start_at: startAt, end_at: endAt, version: latest?.version ?? event.version });
            }
            catch (error) {
                await this.refresh().catch(() => { this.data.events = upsert(this.data.events, event); this.sort(); this.changed.emit('events'); });
                throw error;
            }
        });
        this.moves.set(event.id, run);
        const forget = () => { if (this.moves.get(event.id) === run)
            this.moves.delete(event.id); };
        run.then(forget, forget);
        return run;
    }
    /** Change one occurrence of a repeating event and nothing else: move it (`start` and `end`), cancel it (`skip`) or put it back (`reset`).
     *  The change is a single entry in the event's `exceptions`; the server keeps the rest of the series as it was. Changes to one series
     *  run one after another, each with the version the previous one produced, like moves of a single event. */
    changeOccurrence(event, change) {
        const key = event.occurrence;
        if (typeof change !== 'string') { // show the move at once
            const startAt = toInstant(change.start);
            const endAt = toInstant(change.end);
            this.data.occurrences = this.data.occurrences.map((o) => (o.id === event.id && o.occurrence === key ? { ...o, start_at: startAt, end_at: endAt, moved: true } : o));
            this.changed.emit('events');
        }
        const run = (this.moves.get(event.id) ?? Promise.resolve()).catch(() => undefined).then(async () => {
            const master = this.data.events.find((e) => e.id === event.id);
            if (!master)
                return;
            const next = { ...master.exceptions };
            if (change === 'skip')
                next[key] = null;
            else if (change === 'reset')
                delete next[key];
            else {
                const startAt = toInstant(change.start);
                const endAt = toInstant(change.end);
                const naturalLength = parseInstant(master.end_at).getTime() - parseInstant(master.start_at).getTime();
                if (startAt === key && change.end.getTime() - change.start.getTime() === naturalLength)
                    delete next[key]; // moved back to where it was
                else
                    next[key] = { start_at: startAt, end_at: endAt };
            }
            try {
                await this.updateEvent(event.id, { exceptions: next, version: master.version });
            }
            catch (error) {
                await this.refresh().catch(() => undefined);
                throw error;
            }
        });
        this.moves.set(event.id, run);
        const forget = () => { if (this.moves.get(event.id) === run)
            this.moves.delete(event.id); };
        run.then(forget, forget);
        return run;
    }
    // ------------------------------------------------------------------------------------------------------------ tasks
    async createTask(input) {
        const created = await this.api.post('/tasks', input);
        this.data.tasks = upsert(this.data.tasks, created);
        await this.recount();
        this.changed.emit('tasks');
        return created;
    }
    async updateTask(id, input) {
        const updated = await this.afterConflict(() => this.api.patch(`/tasks/${id}`, input));
        this.data.tasks = upsert(this.data.tasks, updated);
        await this.recount();
        this.changed.emit('tasks');
        return updated;
    }
    /** Tick or untick: the checkbox changes immediately, and goes back if the server says no. */
    async toggleTask(task) {
        const status = task.status === 'done' ? 'open' : 'done';
        const optimistic = { ...task, status, completed_at: status === 'done' ? toInstant(this.now()) : null };
        this.data.tasks = upsert(this.data.tasks, optimistic);
        this.counts.openTasks += status === 'done' ? -1 : 1;
        this.changed.emit('tasks');
        try {
            const saved = await this.api.patch(`/tasks/${task.id}`, { status });
            this.data.tasks = upsert(this.data.tasks, saved);
            this.changed.emit('tasks');
        }
        catch (error) {
            this.data.tasks = upsert(this.data.tasks, task);
            this.counts.openTasks += status === 'done' ? 1 : -1;
            this.changed.emit('tasks');
            throw error;
        }
    }
    async deleteTask(id) {
        await this.api.del(`/tasks/${id}`);
        this.data.tasks = this.data.tasks.filter((t) => t.id !== id);
        await this.recount();
        this.changed.emit('tasks');
    }
    async clearCompleted() {
        const { deleted } = await this.api.post('/tasks/clear-completed');
        this.data.tasks = this.data.tasks.filter((t) => t.status !== 'done');
        await this.recount();
        this.changed.emit('tasks');
        return deleted;
    }
    // ------------------------------------------------------------------------------------------------------------ notes
    getNote(id) { return this.api.get(`/notes/${id}`); }
    noteSummary(note) {
        const { content: _content, editable: _editable, ...summary } = note;
        return summary;
    }
    async createNote(input) {
        const created = await this.api.post('/notes', input);
        this.data.notes = upsert(this.data.notes, this.noteSummary(created));
        this.sortNotes();
        this.counts.notes += 1;
        this.changed.emit('notes');
        return created;
    }
    async updateNote(id, input) {
        const updated = await this.afterConflict(() => this.api.patch(`/notes/${id}`, input));
        this.data.notes = upsert(this.data.notes, this.noteSummary(updated));
        this.sortNotes();
        this.changed.emit('notes');
        return updated;
    }
    async deleteNote(id) {
        await this.api.del(`/notes/${id}`);
        this.data.notes = this.data.notes.filter((n) => n.id !== id);
        this.counts.notes -= 1;
        this.changed.emit('notes');
    }
    sortNotes() {
        this.data.notes.sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updated_at.localeCompare(a.updated_at));
    }
    // ------------------------------------------------------------------------------------------------------------ reminders
    async refreshReminders() {
        this.data.reminders = await this.api.get('/reminders');
        this.changed.emit('reminders');
    }
    async createReminder(title, dueAt) {
        const created = await this.api.post('/reminders', { title, due_at: dueAt });
        this.data.reminders = upsert(this.data.reminders, created);
        this.changed.emit('reminders');
        return created;
    }
    async changeReminder(id, change) {
        const updated = await this.api.patch(`/reminders/${id}`, change);
        this.data.reminders = upsert(this.data.reminders, updated);
        this.changed.emit('reminders');
        return updated;
    }
    async deleteReminder(id) {
        await this.api.del(`/reminders/${id}`);
        await this.refresh(); // an event's own alert also clears that event's "remind me" setting
    }
    /** What the server says is due now, with the server's clock. */
    async due() {
        const { due, now } = await this.api.get('/reminders/due');
        this.skewMs = new Date(now).getTime() - Date.now();
        return due;
    }
    // ------------------------------------------------------------------------------------------------------------ stickies
    async createSticky(input) {
        const created = await this.api.post('/stickies', input);
        this.data.stickies = [...this.data.stickies, created];
        this.counts.stickies += 1;
        this.changed.emit('stickies');
        return created;
    }
    async updateSticky(id, input, options = {}) {
        const updated = await this.afterConflict(() => this.api.patch(`/stickies/${id}`, input));
        this.data.stickies = this.data.stickies.map((s) => (s.id === id ? updated : s));
        if (!options.silent)
            this.changed.emit('stickies');
        return updated;
    }
    async deleteSticky(id) {
        await this.api.del(`/stickies/${id}`);
        this.data.stickies = this.data.stickies.filter((s) => s.id !== id);
        this.counts.stickies -= 1;
        this.changed.emit('stickies');
    }
    // ------------------------------------------------------------------------------------------------------------ preferences, search, data
    async savePrefs(patch) {
        this.prefs = await this.api.patch('/prefs', patch);
        this.changed.emit('prefs');
        return this.prefs;
    }
    search(query) { return this.api.get('/search', { q: query }); }
    status() { return this.api.get('/status'); }
    backup() { return this.api.post('/backup'); }
}
export function errorText(error) {
    if (error instanceof ApiError)
        return error.friendly;
    return error instanceof Error ? error.message : 'Something went wrong.';
}
//# sourceMappingURL=store.js.map