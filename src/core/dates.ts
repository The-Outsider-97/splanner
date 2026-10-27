/** Date and time handling for the planner, as pure functions.
 *
 * The server stores every instant as UTC with an explicit offset (`2026-10-27T09:00:00+00:00`). The browser shows and edits them in its
 * own time zone. All-day events are the exception: their calendar date is the one in the event's own time zone, so a "27 October" event
 * stays on the 27th wherever the page is opened. Weeks start on Monday or Sunday as the person chose; nothing here reads a global. */
import type { PlannerEvent, TimeFormat, WeekStart } from './types.js';

export const MINUTE = 60_000;
export const HOUR = 60 * MINUTE;
export const DAY = 24 * HOUR;

export function browserZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
}

/** `2026-10-27T09:00:00+00:00` as a Date (an unparseable value becomes an invalid Date, never an exception). */
export function parseInstant(value: string): Date { return new Date(value); }

export function toInstant(date: Date): string {
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}+00:00`;
}

const pad2 = (n: number): string => String(n).padStart(2, '0');

/** Local calendar date as `YYYY-MM-DD`. */
export function isoDate(date: Date): string { return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`; }

/** `YYYY-MM-DD` as local midnight. */
export function fromIsoDate(value: string): Date {
  const [y, m, d] = value.split('-').map(Number);
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

export function startOfDay(date: Date): Date { return new Date(date.getFullYear(), date.getMonth(), date.getDate()); }

/** Calendar-day arithmetic that survives daylight-saving changes (adds days, not 24-hour blocks). */
export function addDays(date: Date, days: number): Date { return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days, date.getHours(), date.getMinutes(), date.getSeconds()); }

export function addMonths(date: Date, months: number): Date {
  const first = new Date(date.getFullYear(), date.getMonth() + months, 1);
  const last = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate();
  return new Date(first.getFullYear(), first.getMonth(), Math.min(date.getDate(), last), date.getHours(), date.getMinutes());
}

export function sameDay(a: Date, b: Date): boolean { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }

export function dayDiff(a: Date, b: Date): number { return Math.round((Date.UTC(b.getFullYear(), b.getMonth(), b.getDate()) - Date.UTC(a.getFullYear(), a.getMonth(), a.getDate())) / DAY); }

export function startOfWeek(date: Date, weekStart: WeekStart): Date {
  const first = weekStart === 'monday' ? 1 : 0;
  const back = (date.getDay() - first + 7) % 7;
  return startOfDay(addDays(date, -back));
}

export function weekDays(anchor: Date, weekStart: WeekStart): Date[] {
  const start = startOfWeek(anchor, weekStart);
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}

/** Six rows of seven days that cover the month of `anchor`, for the mini calendar and the month view. */
export function monthGrid(anchor: Date, weekStart: WeekStart): Date[] {
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const start = startOfWeek(first, weekStart);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

export function weekdayNames(weekStart: WeekStart, style: 'short' | 'narrow' | 'long' = 'short', locale?: string): string[] {
  const base = startOfWeek(new Date(2026, 9, 28), weekStart);                  // any date works: only the weekday sequence matters
  return Array.from({ length: 7 }, (_, i) => addDays(base, i).toLocaleDateString(locale, { weekday: style }));
}

/** The day in `zone` that an instant falls on, as `YYYY-MM-DD`. */
export function zonedDate(instant: Date | string, zone: string): string {
  const date = typeof instant === 'string' ? parseInstant(instant) : instant;
  try {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(date);
    const get = (type: string): string => parts.find((p) => p.type === type)?.value ?? '';
    return `${get('year')}-${get('month')}-${get('day')}`;
  } catch { return isoDate(date); }
}

/** First and last (inclusive) calendar days an all-day event covers, in its own zone. The stored end is exclusive. */
export function allDaySpan(event: Pick<PlannerEvent, 'start_at' | 'end_at' | 'time_zone'>): { first: Date; last: Date } {
  const first = fromIsoDate(zonedDate(event.start_at, event.time_zone));
  const endExclusive = fromIsoDate(zonedDate(event.end_at, event.time_zone));
  const last = dayDiff(first, endExclusive) >= 1 ? addDays(endExclusive, -1) : first;
  return { first, last };
}

/** Local-midnight bounds, as instants, for an all-day event on the given local dates (inclusive). */
export function allDayInstants(firstDay: string, lastDay: string): { start_at: string; end_at: string } {
  return { start_at: toInstant(fromIsoDate(firstDay)), end_at: toInstant(addDays(fromIsoDate(lastDay), 1)) };
}

/** Does the event touch the local calendar day? (Timed events by instant overlap, all-day events by their own-zone dates.) */
export function occursOn(event: Pick<PlannerEvent, 'all_day' | 'start_at' | 'end_at' | 'time_zone'>, day: Date): boolean {
  if (event.all_day) {
    const { first, last } = allDaySpan(event);
    return dayDiff(first, day) >= 0 && dayDiff(day, last) >= 0;
  }
  const dayStart = startOfDay(day).getTime();
  const dayEnd = startOfDay(addDays(day, 1)).getTime();
  return parseInstant(event.start_at).getTime() < dayEnd && parseInstant(event.end_at).getTime() > dayStart;
}

// ---------------------------------------------------------------------------------------------------------------- formatting
export function formatTime(date: Date, format: TimeFormat): string {
  if (format === '24h') return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
  const hours = date.getHours() % 12 || 12;
  const suffix = date.getHours() < 12 ? 'AM' : 'PM';
  return date.getMinutes() === 0 ? `${hours} ${suffix}` : `${hours}:${pad2(date.getMinutes())} ${suffix}`;
}

export function formatHour(hour: number, format: TimeFormat): string {
  return format === '24h' ? `${pad2(hour % 24)}:00` : `${hour % 12 || 12} ${hour % 24 < 12 ? 'AM' : 'PM'}`;
}

/** `09:00 – 10:30`, the label the week grid puts over an event's title. */
export function formatRange(start: Date, end: Date, format: TimeFormat): string { return `${formatTime(start, format)} – ${formatTime(end, format)}`; }

export function formatDayLong(date: Date, locale?: string): string { return date.toLocaleDateString(locale, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }); }
export function formatDayShort(date: Date, locale?: string): string { return date.toLocaleDateString(locale, { weekday: 'short', month: 'short', day: 'numeric' }); }
export function formatMonthYear(date: Date, locale?: string): string { return date.toLocaleDateString(locale, { month: 'long', year: 'numeric' }); }

/** `October 26 – November 1, 2026` (the year and month are shown once when they match). */
export function formatWeekRange(days: Date[], locale?: string): string {
  const first = days[0] ?? new Date();
  const last = days[days.length - 1] ?? first;
  return new Intl.DateTimeFormat(locale, { month: 'long', day: 'numeric', year: 'numeric' }).formatRange(first, last).replace(/[\u2009\u202f]/g, ' ');
}

/** A date for lists: "Today", "Tomorrow", "Yesterday", "Wed, Oct 28", or with the year when it is not this year. */
export function relativeDay(date: Date, now: Date, locale?: string): string {
  const diff = dayDiff(now, date);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  return date.toLocaleDateString(locale, { weekday: 'short', month: 'short', day: 'numeric', ...(date.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }) });
}

export function formatWhen(instant: string, now: Date, format: TimeFormat, locale?: string): string {
  const date = parseInstant(instant);
  const day = dayDiff(now, date) === 0 ? '' : `${relativeDay(date, now, locale)} `;
  return `${day}${formatTime(date, format)}`.trim();
}

export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

// ---------------------------------------------------------------------------------------------------------------- <input> values
/** `YYYY-MM-DDTHH:mm`, the value format of `<input type="datetime-local">` (in the browser's zone). */
export function toLocalInput(date: Date): string { return `${isoDate(date)}T${pad2(date.getHours())}:${pad2(date.getMinutes())}`; }

/** The inverse; `null` for an empty or malformed value. */
export function fromLocalInput(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const [, y, mo, d, h, mi] = match.map(Number) as [number, number, number, number, number, number];
  const date = new Date(y, mo - 1, d, h, mi);
  return date.getFullYear() === y && date.getMonth() === mo - 1 && date.getDate() === d ? date : null;
}

/** Round a date to the nearest `step` minutes (default 15), for dragging and clicking in the grid. */
export function snapMinutes(date: Date, step = 15): Date {
  const ms = step * MINUTE;
  return new Date(Math.round(date.getTime() / ms) * ms);
}

/** Next quarter hour after `now`, a sensible start for "Add event". */
export function nextSlot(now: Date, step = 30): Date {
  const ms = step * MINUTE;
  return new Date(Math.ceil((now.getTime() + 1) / ms) * ms);
}

export function minutesSinceMidnight(date: Date): number { return date.getHours() * 60 + date.getMinutes(); }
