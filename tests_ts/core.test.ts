import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { addDays, addMonths, allDayInstants, allDaySpan, formatDuration, formatHour, formatRange, formatTime, formatWeekRange, fromIsoDate, fromLocalInput, isoDate, monthGrid, nextSlot, occursOn, relativeDay, snapMinutes, startOfWeek, toInstant, toLocalInput, weekDays, weekdayNames, zonedDate } from '../src/core/dates.js';
import { hiddenOutside, layoutColumns, spanOnDay } from '../src/core/layout.js';
import { compareTasks, dueDateInput, dueFromDate, dueLabel, dueTimeInput, groupTasks, isDateOnly, isOverdue, priorities } from '../src/core/tasks.js';
import { MAX_SNOOZE_MINUTES, REMIND_BEFORE_CHOICES, SNOOZE_MINUTES, alertKey, customSnooze, dueNow, isDue, pending, reminderTime, snoozeLabel, upcoming } from '../src/core/reminders.js';
import { PRESETS, describeRule, nthWeekdayOfMonth, ordinal, presetOf, ruleFromPreset, shortRepeat } from '../src/core/recurrence.js';
import type { PlannerEvent, Reminder, RepeatRule, Task } from '../src/core/types.js';

// These tests run in whatever zone the machine uses, so they build their dates in local time and avoid assuming a zone.
const local = (y: number, mo: number, d: number, h = 0, mi = 0): Date => new Date(y, mo - 1, d, h, mi);

function event(partial: Partial<PlannerEvent> & { start: Date; end: Date }): PlannerEvent {
  const { start, end, ...rest } = partial;
  return { id: 'e', title: 'Event', start_at: toInstant(start), end_at: toInstant(end), time_zone: 'UTC', all_day: false, description: '', location: '', color: 'blue', note_id: null,
    remind_before: null, recurrence: null, exceptions: {}, version: 1, created_at: '', updated_at: '', ...rest };
}
function task(partial: Partial<Task>): Task {
  return { id: 't', title: 'Task', description: '', status: 'open', priority: 'normal', due_at: null, completed_at: null, note_id: null, version: 1, created_at: '2026-10-01T00:00:00+00:00', updated_at: '', ...partial };
}

describe('dates', () => {
  it('round-trips instants as UTC with an explicit offset', () => {
    const d = new Date(Date.UTC(2026, 9, 27, 9, 5, 7));
    assert.equal(toInstant(d), '2026-10-27T09:05:07+00:00');
    assert.equal(new Date(toInstant(d)).getTime(), d.getTime());
  });

  it('weeks start on the chosen day', () => {
    const tue = local(2026, 10, 27);
    assert.equal(isoDate(startOfWeek(tue, 'monday')), '2026-10-26');
    assert.equal(isoDate(startOfWeek(tue, 'sunday')), '2026-10-25');
    assert.equal(isoDate(startOfWeek(local(2026, 10, 25), 'monday')), '2026-10-19');          // a Sunday belongs to the week that began on Monday
    assert.deepEqual(weekDays(tue, 'monday').map(isoDate), ['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01']);
    assert.equal(weekdayNames('monday', 'short', 'en-US')[0], 'Mon');
    assert.equal(weekdayNames('sunday', 'short', 'en-US')[0], 'Sun');
  });

  it('builds six-week month grids that contain the whole month', () => {
    const grid = monthGrid(local(2026, 10, 15), 'monday');
    assert.equal(grid.length, 42);
    assert.equal(isoDate(grid[0]!), '2026-09-28');
    assert.equal(isoDate(grid[41]!), '2026-11-08');
    assert.ok(grid.some((d) => isoDate(d) === '2026-10-31'));
    assert.equal(isoDate(monthGrid(local(2026, 2, 1), 'monday')[0]!), '2026-01-26');
  });

  it('adds calendar days and months without drifting across clock changes or short months', () => {
    assert.equal(isoDate(addDays(local(2026, 3, 28, 12), 3)), '2026-03-31');
    assert.equal(addDays(local(2026, 10, 24, 12), 2).getHours(), 12);                          // DST weekend: still noon
    assert.equal(isoDate(addMonths(local(2026, 1, 31), 1)), '2026-02-28');
    assert.equal(isoDate(addMonths(local(2026, 12, 15), 1)), '2027-01-15');
    assert.equal(isoDate(addMonths(local(2026, 1, 15), -2)), '2025-11-15');
  });

  it('parses and prints <input> values', () => {
    const d = local(2026, 10, 27, 9, 5);
    assert.equal(toLocalInput(d), '2026-10-27T09:05');
    assert.equal(fromLocalInput('2026-10-27T09:05')?.getTime(), d.getTime());
    assert.equal(fromLocalInput(''), null);
    assert.equal(fromLocalInput('2026-02-31T10:00'), null);
    assert.equal(fromLocalInput('2026-10-27'), null);
    assert.equal(isoDate(fromIsoDate('2026-10-27')), '2026-10-27');
  });

  it('formats times in 24-hour and 12-hour styles', () => {
    assert.equal(formatTime(local(2026, 10, 27, 9, 0), '24h'), '09:00');
    assert.equal(formatTime(local(2026, 10, 27, 0, 5), '12h'), '12:05 AM');
    assert.equal(formatTime(local(2026, 10, 27, 12, 0), '12h'), '12 PM');
    assert.equal(formatTime(local(2026, 10, 27, 15, 30), '12h'), '3:30 PM');
    assert.equal(formatHour(8, '24h'), '08:00');
    assert.equal(formatHour(13, '12h'), '1 PM');
    assert.equal(formatRange(local(2026, 10, 27, 9), local(2026, 10, 27, 10, 30), '24h'), '09:00 – 10:30');
    assert.equal(formatDuration(45), '45 min');
    assert.equal(formatDuration(90), '1 h 30 min');
    assert.equal(formatDuration(120), '2 h');
  });

  it('describes weeks and relative days', () => {
    assert.equal(formatWeekRange(weekDays(local(2026, 10, 27), 'monday'), 'en-US'), 'October 26 – November 1, 2026');
    assert.equal(formatWeekRange(weekDays(local(2026, 10, 14), 'monday'), 'en-US'), 'October 12 – 18, 2026');
    const now = local(2026, 10, 27, 10);
    assert.equal(relativeDay(local(2026, 10, 27, 23), now, 'en-US'), 'Today');
    assert.equal(relativeDay(local(2026, 10, 28), now, 'en-US'), 'Tomorrow');
    assert.equal(relativeDay(local(2026, 10, 26), now, 'en-US'), 'Yesterday');
    assert.equal(relativeDay(local(2026, 10, 30), now, 'en-US'), 'Fri, Oct 30');
    assert.equal(relativeDay(local(2027, 1, 5), now, 'en-US'), 'Tue, Jan 5, 2027');
  });

  it('snaps and finds the next slot', () => {
    assert.equal(snapMinutes(local(2026, 10, 27, 9, 8)).getMinutes(), 15);
    assert.equal(snapMinutes(local(2026, 10, 27, 9, 7)).getMinutes(), 0);
    assert.equal(nextSlot(local(2026, 10, 27, 9, 10)).getMinutes(), 30);
    assert.equal(nextSlot(local(2026, 10, 27, 9, 30)).getHours() * 60 + nextSlot(local(2026, 10, 27, 9, 30)).getMinutes(), 10 * 60);
  });

  it('keeps an all-day event on its own calendar days in its own zone', () => {
    assert.equal(zonedDate('2026-10-27T23:30:00+00:00', 'Europe/Amsterdam'), '2026-10-28');
    assert.equal(zonedDate('2026-10-27T23:30:00+00:00', 'America/Los_Angeles'), '2026-10-27');
    const e = event({ start: new Date(Date.UTC(2026, 9, 26, 23)), end: new Date(Date.UTC(2026, 9, 28, 23)), all_day: true, time_zone: 'Europe/Amsterdam' });   // 27 and 28 Oct (CET)
    const span = allDaySpan(e);
    assert.deepEqual([isoDate(span.first), isoDate(span.last)], ['2026-10-27', '2026-10-28']);
    assert.ok(occursOn(e, local(2026, 10, 28)) && !occursOn(e, local(2026, 10, 29)) && !occursOn(e, local(2026, 10, 26)));
    const made = allDayInstants('2026-10-27', '2026-10-28');
    assert.equal(new Date(made.end_at).getTime() - new Date(made.start_at).getTime() > 47 * 3600_000, true);
  });

  it('decides which day a timed event touches, including across midnight', () => {
    const e = event({ start: local(2026, 10, 27, 22), end: local(2026, 10, 28, 1) });
    assert.ok(occursOn(e, local(2026, 10, 27, 12)) && occursOn(e, local(2026, 10, 28, 12)) && !occursOn(e, local(2026, 10, 29)));
    const ends = event({ start: local(2026, 10, 27, 22), end: local(2026, 10, 28, 0) });
    assert.ok(!occursOn(ends, local(2026, 10, 28)));                                         // ends exactly at midnight: not on the 28th
  });
});

describe('layout', () => {
  it('cuts events at the day edges', () => {
    const e = event({ start: local(2026, 10, 27, 22), end: local(2026, 10, 28, 1) });
    assert.deepEqual(spanOnDay(e, local(2026, 10, 27)), { start: 22 * 60, end: 24 * 60 });
    assert.deepEqual(spanOnDay(e, local(2026, 10, 28)), { start: 0, end: 60 });
    assert.equal(spanOnDay(e, local(2026, 10, 29)), null);
  });

  it('puts overlapping events in side-by-side columns and leaves others full width', () => {
    const spans = [{ item: 'a', start: 540, end: 630 }, { item: 'b', start: 600, end: 720 }, { item: 'c', start: 600, end: 660 }, { item: 'd', start: 800, end: 860 }];
    const placed = Object.fromEntries(layoutColumns(spans).map((p) => [p.item, p]));
    assert.deepEqual([placed['a']!.column, placed['b']!.column, placed['c']!.column], [0, 1, 2]);
    assert.equal(placed['a']!.columns, 3);
    assert.deepEqual([placed['d']!.column, placed['d']!.columns], [0, 1]);
  });

  it('reuses a freed column inside a cluster', () => {
    const placed = Object.fromEntries(layoutColumns([{ item: 'a', start: 0, end: 60 }, { item: 'b', start: 30, end: 120 }, { item: 'c', start: 70, end: 100 }]).map((p) => [p.item, p]));
    assert.deepEqual([placed['a']!.column, placed['b']!.column, placed['c']!.column, placed['c']!.columns], [0, 1, 0, 2]);
  });

  it('clips to the visible window, keeps short events clickable and drops what is outside', () => {
    const placed = layoutColumns([{ item: 'early', start: 420, end: 540 }, { item: 'tiny', start: 600, end: 605 }, { item: 'gone', start: 100, end: 200 }, { item: 'late', start: 1000, end: 1200 }], 480, 1080);
    const by = Object.fromEntries(placed.map((p) => [p.item, p]));
    assert.deepEqual([by['early']!.start, by['early']!.clippedTop], [480, true]);
    assert.equal(by['tiny']!.end - by['tiny']!.start, 20);
    assert.equal(by['gone'], undefined);
    assert.deepEqual([by['late']!.end, by['late']!.clippedBottom], [1080, true]);
  });

  it('counts events that fall wholly outside the visible hours', () => {
    const day = local(2026, 10, 27);
    const events = [event({ start: local(2026, 10, 27, 6), end: local(2026, 10, 27, 7) }), event({ start: local(2026, 10, 27, 9), end: local(2026, 10, 27, 10) }),
      event({ start: local(2026, 10, 27, 20), end: local(2026, 10, 27, 21) }), event({ start: local(2026, 10, 27), end: local(2026, 10, 28), all_day: true })];
    assert.equal(hiddenOutside(events, day, 8 * 60, 18 * 60), 2);
  });
});

describe('tasks', () => {
  const now = local(2026, 10, 27, 12);

  it('orders open tasks by priority, then due date, and done tasks last', () => {
    const list = [task({ id: 'low', priority: 'low' }), task({ id: 'doneA', status: 'done', completed_at: '2026-10-26T10:00:00+00:00' }), task({ id: 'normalSoon', due_at: '2026-10-28T10:00:00+00:00' }),
      task({ id: 'high' , priority: 'high' }), task({ id: 'normalNone' }), task({ id: 'doneB', status: 'done', completed_at: '2026-10-27T10:00:00+00:00' })];
    assert.deepEqual(list.sort(compareTasks).map((t) => t.id), ['high', 'normalSoon', 'normalNone', 'low', 'doneB', 'doneA']);
    assert.deepEqual(priorities(list, 2).map((t) => t.id), ['high', 'normalSoon']);
  });

  it('groups by overdue, today, upcoming, undated and done', () => {
    const date = (d: number, h: number, m = 0): string => toInstant(local(2026, 10, d, h, m));
    const list = [task({ id: 'yesterday', due_at: date(26, 9) }), task({ id: 'earlier today', due_at: date(27, 8) }), task({ id: 'later today', due_at: date(27, 18) }),
      task({ id: 'date-only today', due_at: dueFromDate('2026-10-27') }), task({ id: 'tomorrow', due_at: date(28, 9) }), task({ id: 'none' }), task({ id: 'done', status: 'done', due_at: date(1, 9) })];
    const groups = Object.fromEntries(groupTasks(list, now).map((g) => [g.key, g.tasks.map((t) => t.id)]));
    assert.deepEqual(groups['overdue'], ['yesterday', 'earlier today']);
    assert.deepEqual(groups['today'], ['later today', 'date-only today']);
    assert.deepEqual(groups['upcoming'], ['tomorrow']);
    assert.deepEqual(groups['someday'], ['none']);
    assert.deepEqual(groups['done'], ['done']);
    assert.equal(isOverdue(list[0]!, now), true);
    assert.equal(isOverdue(list[3]!, now), false);
    assert.equal(isOverdue(list[6]!, now), false);
  });

  it('treats 23:59 as "a date with no time"', () => {
    const t = task({ due_at: dueFromDate('2026-10-30') });
    assert.ok(isDateOnly(t));
    assert.equal(dueDateInput(t), '2026-10-30');
    assert.equal(dueTimeInput(t), '');
    assert.equal(dueLabel(t, now, '24h', 'en-US'), 'Fri, Oct 30');
    const timed = task({ due_at: dueFromDate('2026-10-30', '14:30') });
    assert.ok(!isDateOnly(timed));
    assert.equal(dueTimeInput(timed), '14:30');
    assert.equal(dueLabel(timed, now, '24h', 'en-US'), 'Fri, Oct 30 14:30');
    assert.equal(dueFromDate(''), null);
    assert.equal(dueLabel(task({}), now, '24h'), '');
  });
});

describe('reminders', () => {
  const rem = (id: string, due: Date, status: Reminder['status'] = 'pending'): Reminder => ({ id, title: id, due_at: toInstant(due), status, entity_type: null, entity_id: null, occurrence_at: null, created_at: '', updated_at: '' });
  const now = local(2026, 10, 27, 12);

  it('finds what is due and what is coming', () => {
    const list = [rem('later', local(2026, 10, 27, 14)), rem('due', local(2026, 10, 27, 11)), rem('dismissed', local(2026, 10, 27, 9), 'dismissed'), rem('earlier', local(2026, 10, 26, 9))];
    assert.deepEqual(pending(list).map((r) => r.id), ['earlier', 'due', 'later']);
    assert.deepEqual(dueNow(list, now).map((r) => r.id), ['earlier', 'due']);
    assert.deepEqual(upcoming(list, 2).map((r) => r.id), ['earlier', 'due']);
    assert.ok(isDue(list[1]!, now) && !isDue(list[0]!, now) && !isDue(list[2]!, now));
  });

  it('labels times and keys alerts so a snooze alerts again', () => {
    assert.equal(reminderTime(rem('x', local(2026, 10, 27, 14)), now, '24h', 'en-US'), '14:00');
    assert.equal(reminderTime(rem('x', local(2026, 10, 28, 9)), now, '24h', 'en-US'), 'Tomorrow 09:00');
    assert.notEqual(alertKey(rem('x', local(2026, 10, 27, 14))), alertKey(rem('x', local(2026, 10, 27, 15))));
    assert.equal(REMIND_BEFORE_CHOICES[0]!.value, null);
  });
});

describe('snooze', () => {
  it('offers +1, +5, +10, +15, +30 and +60 minutes and labels them that way', () => {
    assert.deepEqual([...SNOOZE_MINUTES], [1, 5, 10, 15, 30, 60]);
    assert.deepEqual(SNOOZE_MINUTES.map(snoozeLabel), ['+1 min', '+5 min', '+10 min', '+15 min', '+30 min', '+60 min']);
    assert.deepEqual([120, 1440, 4320, 90].map(snoozeLabel), ['+2 h', '+1 d', '+3 d', '+90 min']);
  });

  it('turns a custom length into minutes and refuses nonsense', () => {
    assert.deepEqual(customSnooze(20, 'minutes'), { minutes: 20 });
    assert.deepEqual(customSnooze(2, 'hours'), { minutes: 120 });
    assert.deepEqual(customSnooze(3, 'days'), { minutes: 4320 });
    assert.deepEqual(customSnooze(30, 'days'), { minutes: MAX_SNOOZE_MINUTES });
    for (const [amount, unit] of [[0, 'minutes'], [-5, 'minutes'], [1.5, 'hours'], [Number.NaN, 'minutes'], [31, 'days'], [43_201, 'minutes']] as const) {
      assert.ok('error' in customSnooze(amount, unit), `${amount} ${unit}`);
    }
  });
});

describe('repeats', () => {
  const rule = (partial: Partial<RepeatRule> & Pick<RepeatRule, 'freq'>): RepeatRule => ({ interval: 1, until: null, count: null, ...partial });
  const tuesday = local(2026, 10, 27, 9);                       // the fourth Tuesday of October 2026

  it('names the common frequencies the way people say them', () => {
    const label = (id: string): string => PRESETS.find((p) => p.id === id)!.label;
    assert.equal(label('biweekly'), 'Every 2 weeks (bi-weekly)');
    assert.equal(label('bimonthly'), 'Every 2 months (bi-monthly)');
    assert.equal(label('quarterly'), 'Every 3 months (quarterly)');
    assert.equal(label('trimester'), 'Every 4 months (trimester)');
    assert.equal(PRESETS[0]!.label, 'Does not repeat');
    assert.equal(PRESETS[PRESETS.length - 1]!.label, 'Custom…');
  });

  it('recognises which choice a stored rule is, and falls back to custom', () => {
    assert.equal(presetOf(null), 'none');
    assert.equal(presetOf(rule({ freq: 'daily' })), 'daily');
    assert.equal(presetOf(rule({ freq: 'weekly', weekdays: [] })), 'weekly');
    assert.equal(presetOf(rule({ freq: 'weekly', interval: 2 })), 'biweekly');
    assert.equal(presetOf(rule({ freq: 'weekly', weekdays: [1, 2, 3, 4, 5] })), 'weekdays');
    assert.equal(presetOf(rule({ freq: 'monthly', interval: 2, month_mode: 'date' })), 'bimonthly');
    assert.equal(presetOf(rule({ freq: 'monthly', interval: 3 })), 'quarterly');
    assert.equal(presetOf(rule({ freq: 'monthly', interval: 4 })), 'trimester');
    assert.equal(presetOf(rule({ freq: 'monthly', interval: 6 })), 'semiannual');
    assert.equal(presetOf(rule({ freq: 'yearly' })), 'yearly');
    assert.equal(presetOf(rule({ freq: 'weekly', weekdays: [1, 3] })), 'custom');
    assert.equal(presetOf(rule({ freq: 'monthly', month_mode: 'weekday' })), 'custom');
    assert.equal(presetOf(rule({ freq: 'daily', interval: 3 })), 'custom');
    assert.equal(presetOf(rule({ freq: 'weekly', until: '2026-12-31' })), 'weekly');                     // the end is a separate setting
  });

  it('builds the rule a choice stands for, with its end', () => {
    assert.equal(ruleFromPreset('none', { until: null, count: null }), null);
    assert.equal(ruleFromPreset('custom', { until: null, count: null }), null);
    assert.deepEqual(ruleFromPreset('quarterly', { until: null, count: 4 }), { freq: 'monthly', interval: 3, month_mode: 'date', until: null, count: 4 });
    assert.deepEqual(ruleFromPreset('biweekly', { until: '2027-01-01', count: null }), { freq: 'weekly', interval: 2, weekdays: [], until: '2027-01-01', count: null });
    for (const preset of PRESETS) {                                                                       // every choice maps back to itself
      const built = ruleFromPreset(preset.id, { until: null, count: null });
      if (built) assert.equal(presetOf(built), preset.id);
    }
  });

  it('describes a rule in words', () => {
    assert.equal(describeRule(rule({ freq: 'daily' }), tuesday, 'en-US'), 'Every day');
    assert.equal(describeRule(rule({ freq: 'weekly', interval: 2 }), tuesday, 'en-US'), 'Every 2 weeks on Tuesday');
    assert.equal(describeRule(rule({ freq: 'weekly', weekdays: [1, 3, 5] }), tuesday, 'en-US'), 'Every week on Monday, Wednesday and Friday');
    assert.equal(describeRule(rule({ freq: 'weekly', weekdays: [1, 2, 3, 4, 5] }), tuesday, 'en-US'), 'Every week on weekdays');
    assert.equal(describeRule(rule({ freq: 'monthly', interval: 3, month_mode: 'date' }), tuesday, 'en-US'), 'Every 3 months on day 27');
    assert.equal(describeRule(rule({ freq: 'monthly', month_mode: 'weekday' }), tuesday, 'en-US'), 'Every month on the 4th Tuesday');
    assert.equal(describeRule(rule({ freq: 'monthly', month_mode: 'weekday' }), local(2026, 10, 30, 9), 'en-US'), 'Every month on the 5th Friday'.replace('5th', 'last'));
    assert.equal(describeRule(rule({ freq: 'yearly' }), tuesday, 'en-US'), 'Every year on October 27');
    assert.equal(describeRule(rule({ freq: 'daily', count: 5 }), tuesday, 'en-US'), 'Every day, 5 times');
    assert.equal(describeRule(rule({ freq: 'weekly', until: '2026-12-31' }), tuesday, 'en-US'), 'Every week on Tuesday, until Dec 31, 2026');
    assert.equal(shortRepeat(rule({ freq: 'weekly' })), 'Repeats every week');
    assert.equal(shortRepeat(rule({ freq: 'monthly', interval: 4 })), 'Repeats every 4 months');
    assert.equal(shortRepeat(rule({ freq: 'daily' })), 'Repeats daily');
  });

  it('counts weekdays of the month and writes ordinals', () => {
    assert.deepEqual([1, 7, 8, 14, 15, 28, 29, 31].map((d) => nthWeekdayOfMonth(local(2026, 10, d))), [1, 1, 2, 2, 3, 4, 5, 5]);
    assert.deepEqual([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101].map(ordinal), ['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '23rd', '101st']);
  });
});
