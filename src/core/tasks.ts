/** Task ordering, grouping and labels as pure functions. */
import { addDays, dayDiff, fromLocalInput, parseInstant, relativeDay, startOfDay, formatTime, toInstant, toLocalInput } from './dates.js';
import type { Priority, Task, TimeFormat } from './types.js';

const RANK: Record<Priority, number> = { high: 0, normal: 1, low: 2 };

/** A task due "on a day" with no particular time is stored as 23:59 local time; this is the one place that convention lives. */
export const END_OF_DAY = { hours: 23, minutes: 59 } as const;

export function dueFromDate(day: string, time?: string): string | null {
  if (!day) return null;
  const value = fromLocalInput(`${day}T${time || `${END_OF_DAY.hours}:${END_OF_DAY.minutes}`}`);
  return value ? toInstant(value) : null;
}

export function isDateOnly(task: Pick<Task, 'due_at'>): boolean {
  if (!task.due_at) return false;
  const due = parseInstant(task.due_at);
  return due.getHours() === END_OF_DAY.hours && due.getMinutes() === END_OF_DAY.minutes;
}

export function dueDateInput(task: Pick<Task, 'due_at'>): string { return task.due_at ? toLocalInput(parseInstant(task.due_at)).slice(0, 10) : ''; }
export function dueTimeInput(task: Pick<Task, 'due_at'>): string { return task.due_at && !isDateOnly(task) ? toLocalInput(parseInstant(task.due_at)).slice(11) : ''; }

/** Open tasks first by priority then by due date (undated last), then newest; done tasks by completion time, latest first. */
export function compareTasks(a: Task, b: Task): number {
  if (a.status !== b.status) return a.status === 'open' ? -1 : 1;
  if (a.status === 'done') return (b.completed_at ?? '').localeCompare(a.completed_at ?? '');
  if (RANK[a.priority] !== RANK[b.priority]) return RANK[a.priority] - RANK[b.priority];
  if (a.due_at !== b.due_at) { if (!a.due_at) return 1; if (!b.due_at) return -1; return a.due_at.localeCompare(b.due_at); }
  return b.created_at.localeCompare(a.created_at);
}

/** Sorted by due date first (soonest, undated last), then priority: the order inside a group on the Tasks page. */
export function compareByDue(a: Task, b: Task): number {
  if (a.due_at !== b.due_at) { if (!a.due_at) return 1; if (!b.due_at) return -1; return a.due_at.localeCompare(b.due_at); }
  return RANK[a.priority] - RANK[b.priority] || b.created_at.localeCompare(a.created_at);
}

export function priorities(tasks: Task[], limit: number): Task[] { return tasks.filter((t) => t.status === 'open').sort(compareTasks).slice(0, limit); }

export type TaskGroupKey = 'overdue' | 'today' | 'upcoming' | 'someday' | 'done';
export interface TaskGroup { key: TaskGroupKey; label: string; tasks: Task[] }
const LABEL: Record<TaskGroupKey, string> = { overdue: 'Overdue', today: 'Today', upcoming: 'Upcoming', someday: 'No due date', done: 'Completed' };

export function groupTasks(tasks: Task[], now: Date): TaskGroup[] {
  const buckets: Record<TaskGroupKey, Task[]> = { overdue: [], today: [], upcoming: [], someday: [], done: [] };
  const today = startOfDay(now);
  for (const task of tasks) {
    if (task.status === 'done') { buckets.done.push(task); continue; }
    if (!task.due_at) { buckets.someday.push(task); continue; }
    const due = parseInstant(task.due_at);
    const days = dayDiff(today, due);
    // A task with a time that has already passed today is overdue as well; a date-only task is overdue only from the next day.
    if (days < 0 || (days === 0 && !isDateOnly(task) && due.getTime() < now.getTime())) buckets.overdue.push(task);
    else if (days === 0) buckets.today.push(task);
    else buckets.upcoming.push(task);
  }
  buckets.done.sort(compareTasks);
  for (const key of ['overdue', 'today', 'upcoming', 'someday'] as const) buckets[key].sort(compareByDue);
  return (['overdue', 'today', 'upcoming', 'someday', 'done'] as const).filter((key) => buckets[key].length > 0).map((key) => ({ key, label: LABEL[key], tasks: buckets[key] }));
}

export function dueLabel(task: Task, now: Date, format: TimeFormat, locale?: string): string {
  if (!task.due_at) return '';
  const due = parseInstant(task.due_at);
  const day = relativeDay(due, now, locale);
  return isDateOnly(task) ? day : `${day} ${formatTime(due, format)}`;
}

export function isOverdue(task: Task, now: Date): boolean {
  if (task.status === 'done' || !task.due_at) return false;
  return groupTasks([task], now)[0]?.key === 'overdue';
}

export function openCountDueBy(tasks: Task[], day: Date): number {
  const end = startOfDay(addDays(day, 1)).getTime();
  return tasks.filter((t) => t.status === 'open' && t.due_at && parseInstant(t.due_at).getTime() < end).length;
}
