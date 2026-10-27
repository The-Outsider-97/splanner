/** Reminder helpers: what is due, what is coming, and how to label it. Pure functions over the records the API returns. */
import { dayDiff, formatTime, parseInstant, relativeDay } from './dates.js';
import type { Reminder, TimeFormat } from './types.js';

/** The quick snooze lengths, in minutes: +1, +5, +10, +15, +30, +60. */
export const SNOOZE_MINUTES = [1, 5, 10, 15, 30, 60] as const;
/** The longest snooze the server accepts (30 days). */
export const MAX_SNOOZE_MINUTES = 43_200;
export type SnoozeUnit = 'minutes' | 'hours' | 'days';
export const SNOOZE_UNITS: ReadonlyArray<{ unit: SnoozeUnit; label: string; minutes: number }> = [
  { unit: 'minutes', label: 'minutes', minutes: 1 }, { unit: 'hours', label: 'hours', minutes: 60 }, { unit: 'days', label: 'days', minutes: 1440 },
];

/** A custom snooze (`amount` of `unit`) in minutes, or why it can not be used. */
export function customSnooze(amount: number, unit: SnoozeUnit): { minutes: number } | { error: string } {
  if (!Number.isFinite(amount) || amount <= 0) return { error: 'Enter how long to snooze for, for example 20 minutes.' };
  if (!Number.isInteger(amount)) return { error: 'Use a whole number.' };
  const minutes = amount * (SNOOZE_UNITS.find((u) => u.unit === unit)?.minutes ?? 1);
  if (minutes > MAX_SNOOZE_MINUTES) return { error: 'A reminder can be snoozed for 30 days at most.' };
  return { minutes };
}

/** `+5 min`, `+60 min`, `+2 h`, `+3 d` (for menus and toasts). */
export const snoozeLabel = (minutes: number): string =>
  minutes <= 60 ? `+${minutes} min` : minutes % 1440 === 0 ? `+${minutes / 1440} d` : minutes % 60 === 0 ? `+${minutes / 60} h` : `+${minutes} min`;

export const REMIND_BEFORE_CHOICES: ReadonlyArray<{ label: string; value: number | null }> = [
  { label: 'No reminder', value: null }, { label: 'At the start', value: 0 }, { label: '5 minutes before', value: 5 }, { label: '10 minutes before', value: 10 },
  { label: '15 minutes before', value: 15 }, { label: '30 minutes before', value: 30 }, { label: '1 hour before', value: 60 }, { label: '1 day before', value: 1440 },
];

export const pending = (reminders: Reminder[]): Reminder[] => reminders.filter((r) => r.status === 'pending').sort((a, b) => a.due_at.localeCompare(b.due_at));

export function isDue(reminder: Reminder, now: Date): boolean { return reminder.status === 'pending' && parseInstant(reminder.due_at).getTime() <= now.getTime(); }

export const dueNow = (reminders: Reminder[], now: Date): Reminder[] => pending(reminders).filter((r) => isDue(r, now));

/** Pending reminders to show on the dashboard: everything overdue plus what is coming, soonest first. */
export const upcoming = (reminders: Reminder[], limit: number): Reminder[] => pending(reminders).slice(0, limit);

/** `14:00` today, `Tomorrow 09:00`, `Wed, Oct 28 09:00`. */
export function reminderTime(reminder: Reminder, now: Date, format: TimeFormat, locale?: string): string {
  const due = parseInstant(reminder.due_at);
  return dayDiff(now, due) === 0 ? formatTime(due, format) : `${relativeDay(due, now, locale)} ${formatTime(due, format)}`;
}

/** A key that changes when a reminder is snoozed or moved, so each due time alerts once (and again after a snooze). */
export const alertKey = (reminder: Reminder): string => `${reminder.id}@${reminder.due_at}`;
