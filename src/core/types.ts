/** Records as the SPlanner API returns them (snake_case, exactly the server's field names). */

export type Color = 'blue' | 'violet' | 'green' | 'pink' | 'amber';
export const COLORS: readonly Color[] = ['blue', 'violet', 'green', 'pink', 'amber'];
export const COLOR_LABEL: Record<Color, string> = { blue: 'Ocean blue', violet: 'Violet', green: 'Mint', pink: 'Rose', amber: 'Sunshine' };

export type Priority = 'low' | 'normal' | 'high';
export const PRIORITIES: readonly Priority[] = ['high', 'normal', 'low'];
export const PRIORITY_LABEL: Record<Priority, string> = { high: 'High', normal: 'Normal', low: 'Low' };

/** How an event repeats. The server expands it; the client only edits and describes it (see `recurrence.ts`). */
export type RepeatFreq = 'daily' | 'weekly' | 'monthly' | 'yearly';
export interface RepeatRule {
  freq: RepeatFreq; interval: number;
  /** Weekly only: Monday = 1 ... Sunday = 7. Empty means the weekday of the event's start. */
  weekdays?: number[];
  /** Monthly only: the same date each month, or the same weekday ("the second Tuesday"). */
  month_mode?: 'date' | 'weekday';
  until: string | null; count: number | null;
}
/** Single changes to a repeating event, by the occurrence's original start: `null` cancels it, a time moves it. */
export type Exceptions = Record<string, null | { start_at: string; end_at: string }>;

export interface PlannerEvent {
  id: string; title: string; start_at: string; end_at: string; time_zone: string; all_day: boolean; description: string; location: string;
  color: Color; note_id: string | null; remind_before: number | null; recurrence: RepeatRule | null; exceptions: Exceptions;
  version: number; created_at: string; updated_at: string;
}

/** What the calendar draws: an event that does not repeat, or one occurrence of a repeating one (`occurrence` is its original start). */
export interface CalEvent extends Omit<PlannerEvent, 'exceptions'> { exceptions?: Exceptions; occurrence?: string; moved?: boolean }

export interface Task {
  id: string; title: string; description: string; status: 'open' | 'done'; priority: Priority; due_at: string | null; completed_at: string | null;
  note_id: string | null; version: number; created_at: string; updated_at: string;
}

export interface NoteSummary {
  id: string; title: string; kind: 'note' | 'sticky'; excerpt: string; pinned: boolean; version: number; created_at: string; updated_at: string;
}
export interface Note extends NoteSummary { content: string; editable: boolean }

export interface Reminder {
  id: string; title: string; due_at: string; status: 'pending' | 'dismissed'; entity_type: string | null; entity_id: string | null;
  occurrence_at: string | null; created_at: string; updated_at: string;
}

/** `width` and `height` are in pixels; `null` is the default size. */
export interface Sticky { id: string; note_id: string; x: number; y: number; width: number | null; height: number | null; color: Color; version: number; text: string }

export type WeekStart = 'monday' | 'sunday';
export type TimeFormat = '24h' | '12h';
export type ViewName = 'today' | 'calendar' | 'tasks' | 'notes' | 'board';
export const VIEWS: readonly ViewName[] = ['today', 'calendar', 'tasks', 'notes', 'board'];

export interface Prefs {
  theme: 'system' | 'light' | 'dark' | 'contrast'; scale: number; motion: 'system' | 'reduce'; workspace_name: string; initials: string;
  week_start: WeekStart; time_format: TimeFormat; day_start: number; day_end: number; default_view: ViewName; default_duration: number;
  default_remind: number; sound: boolean; sound_file: string; sound_volume: number; browser_notifications: boolean;
}

export interface Snapshot {
  events: PlannerEvent[]; tasks: Task[]; notes: NoteSummary[]; reminders: Reminder[]; stickies: Sticky[];
  /** The occurrences of the repeating events inside `window` (events that do not repeat are in `events` only). */
  occurrences: CalEvent[]; window: { from: string; to: string };
}
export interface Counts { events: number; tasks: number; openTasks: number; notes: number; stickies: number; reminders: number }
export interface Notice { kind: 'info' | 'warn'; text: string }

export interface Bootstrap {
  app: { name: string; version: string; slai: { slaiRoot: string | null; sldc: { available: boolean; reason?: string; module?: string }; assets: string } };
  settings: { reminderPollSeconds: number };
  prefs: Prefs; sounds: string[]; notices: Notice[];
  capabilities: { sldc: boolean; recurrence: boolean; sharing: boolean; agentSuggestions: boolean; backgroundAlerts: boolean };
  storage: { dataDir: string; backupsDir: string };
  counts: Counts; data: Snapshot; now: string;
}

export interface SearchHit { kind: 'event' | 'task' | 'note' | 'sticky' | 'reminder'; id: string; title: string; detail: string; at: string | null }

export interface StatusInfo {
  version: string; python: string; schemaVersion: number; integrity: string; databaseBytes: number; dataDir: string; counts: Counts;
  backups: Array<{ name: string; bytes: number; at: string }>; slai: Bootstrap['app']['slai']; uptimeSeconds: number;
}
