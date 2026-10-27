/** The time grid used by the dashboard's "This week" panel and by the Calendar's week and day views: hour rows, day columns, an
 *  all-day strip, overlapping events side by side, a "now" line, click-to-create and (when `editable`) drag-to-move and drag-to-resize. */
import { activatable } from './widgets.js';
import { glyph, h, toast } from './kit.js';
import { errorText } from './store.js';
import { openEventDialog } from './editors.js';
import type { ViewContext } from './context.js';
import {
  addDays, allDaySpan, dayDiff, formatHour, formatRange, formatTime, formatDayLong, isoDate, minutesSinceMidnight, occursOn, parseInstant, sameDay, startOfDay, weekdayNames,
} from '../core/dates.js';
import { layoutColumns, spanOnDay, hiddenOutside } from '../core/layout.js';
import { shortRepeat } from '../core/recurrence.js';
import type { CalEvent } from '../core/types.js';

export interface GridOptions {
  days: Date[];
  startHour: number;
  endHour: number;
  hourHeight: number;
  editable: boolean;
  /** Called when a day header is chosen (the Calendar opens that day). */
  onDay?: (day: Date) => void;
}

export interface GridHandle { el: HTMLElement; destroy(): void; scrollTarget(): HTMLElement; hiddenCount: number }

const SNAP = 15;
const MIN_HEIGHT = 22;

export function createTimeGrid(ctx: ViewContext, options: GridOptions): GridHandle {
  const { store } = ctx;
  const { days, startHour, endHour, hourHeight } = options;
  const format = store.prefs.time_format;
  const windowStart = startHour * 60;
  const windowEnd = endHour * 60;
  const cleanups: Array<() => void> = [];
  const today = startOfDay(store.now());
  const first = days[0] as Date;
  store.ensureWindow(first, days[days.length - 1] as Date);
  const events = store.calendarEvents().filter((e) => days.some((d) => occursOn(e, d)));
  const timed = events.filter((e) => !e.all_day);
  const allDay = events.filter((e) => e.all_day);
  const hiddenCount = days.reduce((sum, d) => sum + hiddenOutside(timed, d, windowStart, windowEnd), 0);
  const single = days.length === 1;
  const columns = new Map<number, HTMLElement>();

  const root = h('div', { class: `timegrid${single ? ' is-single' : ''}`, css: { '--hour-h': `${hourHeight}px`, '--cols': String(days.length) } });

  // ------------------------------------------------------------------ header
  const names = weekdayNames(store.prefs.week_start, 'short');
  const head = h('div', { class: 'tg-head' }, h('div', { class: 'tg-corner' }));
  for (const day of days) {
    const isToday = sameDay(day, today);
    const label = single ? day.toLocaleDateString(undefined, { weekday: 'short' }) : (names[(day.getDay() - (store.prefs.week_start === 'monday' ? 1 : 0) + 7) % 7] ?? '');
    const cell = h('button', { class: `tg-day${isToday ? ' is-today' : ''}`, type: 'button', attrs: { 'aria-label': formatDayLong(day), ...(isToday ? { 'aria-current': 'date' } : {}) },
      on: { click: () => options.onDay?.(day) } }, h('span', { class: 'tg-dow', text: label }), h('span', { class: 'tg-num', text: String(day.getDate()) }));
    head.appendChild(cell);
  }
  root.appendChild(head);

  // ------------------------------------------------------------------ all-day strip
  if (allDay.length > 0) {
    const strip = h('div', { class: 'tg-allday' }, h('div', { class: 'tg-gutter small muted', text: 'all-day' }));
    const lane = h('div', { class: 'tg-allday-lane' });
    const rows: number[][] = [];
    for (const event of allDay.sort((a, b) => a.start_at.localeCompare(b.start_at))) {
      const span = allDaySpan(event);
      const from = Math.max(0, dayDiff(first, span.first));
      const to = Math.min(days.length - 1, dayDiff(first, span.last));
      let row = rows.findIndex((used) => used.every((c) => c < from || c > to) );
      if (row === -1) { rows.push([]); row = rows.length - 1; }
      for (let c = from; c <= to; c += 1) rows[row]?.push(c);
      const chip = h('button', { class: `tg-chip tone-${event.color}${event.recurrence ? ' is-repeating' : ''}`, type: 'button', text: event.title, attrs: { title: event.recurrence ? `${event.title} (${shortRepeat(event.recurrence).toLowerCase()})` : event.title },
        on: { click: () => { void openEventDialog(ctx, { event }); } } });
      chip.style.setProperty('grid-column', `${from + 1} / ${to + 2}`);
      chip.style.setProperty('grid-row', String(row + 1));
      lane.appendChild(chip);
    }
    strip.appendChild(lane);
    root.appendChild(strip);
  }

  // ------------------------------------------------------------------ body
  const body = h('div', { class: 'tg-body' });
  const gutter = h('div', { class: 'tg-gutter hours', css: { height: `${(endHour - startHour) * hourHeight}px` } });
  for (let hour = startHour; hour <= endHour; hour += 1) {
    const label = h('span', { class: 'tg-hour', text: formatHour(hour, format) });
    label.style.setProperty('top', `${(hour - startHour) * hourHeight}px`);
    gutter.appendChild(label);
  }
  body.appendChild(gutter);
  const lanes = h('div', { class: 'tg-lanes' });
  body.appendChild(lanes);

  const minutesToPx = (minutes: number): number => ((minutes - windowStart) / 60) * hourHeight;
  const pxToMinutes = (px: number): number => windowStart + (px / hourHeight) * 60;

  const blockLabel = (event: CalEvent): string => `${event.title}, ${formatRange(parseInstant(event.start_at), parseInstant(event.end_at), format)}${event.recurrence ? `, ${shortRepeat(event.recurrence).toLowerCase()}` : ''}`;

  days.forEach((day, index) => {
    const column = h('div', { class: `tg-col${sameDay(day, today) ? ' is-today' : ''}`, attrs: { 'data-date': isoDate(day) } });
    column.style.setProperty('height', `${(endHour - startHour) * hourHeight}px`);
    columns.set(index, column);
    const spans = timed.flatMap((event) => { const s = spanOnDay(event, day); return s ? [{ item: event, start: s.start, end: s.end }] : []; });
    for (const placed of layoutColumns(spans, windowStart, windowEnd, (MIN_HEIGHT / hourHeight) * 60)) {
      const event = placed.item;
      const top = minutesToPx(placed.start);
      const height = Math.max(MIN_HEIGHT, minutesToPx(placed.end) - top);
      const start = parseInstant(event.start_at);
      const end = parseInstant(event.end_at);
      const block = h('div', { class: `ev tone-${event.color}${height < 26 ? ' is-short' : ''}${placed.clippedTop ? ' clip-top' : ''}${placed.clippedBottom ? ' clip-bottom' : ''}`,
        attrs: { tabindex: '0', role: 'button', 'aria-label': blockLabel(event), title: `${event.title}\n${formatRange(start, end, format)}${event.location ? `\n${event.location}` : ''}`, 'data-id': event.id, ...(event.occurrence ? { 'data-occ': event.occurrence } : {}) } },
        h('span', { class: 'ev-time', text: formatRange(start, end, format) }), h('span', { class: 'ev-title', text: event.title }),
        event.recurrence ? h('span', { class: 'ev-repeat', attrs: { 'aria-hidden': 'true', title: shortRepeat(event.recurrence) } }, glyph('repeat')) : null,
        height >= 56 && event.location ? h('span', { class: 'ev-place', text: event.location }) : null,
        options.editable && !placed.clippedBottom ? h('span', { class: 'ev-resize', attrs: { 'aria-hidden': 'true' } }) : null);
      block.style.setProperty('top', `${top}px`);
      block.style.setProperty('height', `${height}px`);
      block.style.setProperty('left', `calc(${(placed.column / placed.columns) * 100}% + 2px)`);
      block.style.setProperty('width', `calc(${100 / placed.columns}% - 4px)`);
      column.appendChild(block);
      if (options.editable) wireEditable(block, event, index);
      else activatable(block, () => { void openEventDialog(ctx, { event }); }, blockLabel(event));
    }
    // click on empty space creates an event there
    column.addEventListener('click', (e) => {
      if (e.target !== column) return;
      const rect = column.getBoundingClientRect();
      const minutes = Math.floor(pxToMinutes(e.clientY - rect.top) / 30) * 30;
      const start = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 0, minutes);
      void openEventDialog(ctx, { start, end: new Date(start.getTime() + store.prefs.default_duration * 60_000) });
    });
    lanes.appendChild(column);
  });
  root.appendChild(body);

  // ------------------------------------------------------------------ "now" line
  const nowLine = h('div', { class: 'tg-now', attrs: { 'aria-hidden': 'true' } });
  const placeNow = (): void => {
    const now = store.now();
    const at = days.findIndex((d) => sameDay(d, now));
    const column = columns.get(at);
    const minutes = minutesSinceMidnight(now);
    if (!column || minutes < windowStart || minutes > windowEnd) { nowLine.remove(); return; }
    nowLine.style.setProperty('top', `${minutesToPx(minutes)}px`);
    if (nowLine.parentElement !== column) column.appendChild(nowLine);
  };
  placeNow();
  const timer = window.setInterval(placeNow, 60_000);
  cleanups.push(() => window.clearInterval(timer));

  // ------------------------------------------------------------------ drag to move / resize
  function columnAt(clientX: number): number {
    let best = 0;
    let distance = Infinity;
    for (const [index, column] of columns) {
      const rect = column.getBoundingClientRect();
      if (clientX >= rect.left && clientX <= rect.right) return index;
      const d = Math.min(Math.abs(clientX - rect.left), Math.abs(clientX - rect.right));
      if (d < distance) { distance = d; best = index; }
    }
    return best;
  }

  function wireEditable(block: HTMLElement, event: CalEvent, dayIndex: number): void {
    const start0 = parseInstant(event.start_at);
    const end0 = parseInstant(event.end_at);
    const duration = end0.getTime() - start0.getTime();
    let drag: { kind: 'move' | 'resize'; x: number; y: number; moved: boolean; start: Date; end: Date; ghost: HTMLElement | null } | null = null;

    const apply = (e: PointerEvent): void => {
      if (!drag) return;
      const dy = e.clientY - drag.y;
      const dMinutes = Math.round(((dy / hourHeight) * 60) / SNAP) * SNAP;
      if (drag.kind === 'resize') {
        drag.start = start0;
        drag.end = new Date(Math.max(start0.getTime() + SNAP * 60_000, end0.getTime() + dMinutes * 60_000));
      } else {
        const dDays = columnAt(e.clientX) - dayIndex;
        drag.start = addDays(new Date(start0.getTime() + dMinutes * 60_000), dDays);
        // addDays keeps the wall-clock time, which is what a person means by "the same time tomorrow" across a clock change
        drag.end = new Date(drag.start.getTime() + duration);
      }
      showGhost();
    };

    const showGhost = (): void => {
      if (!drag) return;
      const targetIndex = Math.max(0, Math.min(days.length - 1, dayDiff(first, drag.start)));
      const column = columns.get(targetIndex);
      if (!column) return;
      const span = spanOnDay({ start_at: drag.start.toISOString(), end_at: drag.end.toISOString() }, days[targetIndex] as Date);
      if (!span) return;
      if (!drag.ghost) { drag.ghost = h('div', { class: `ev ev-ghost tone-${event.color}`, attrs: { 'aria-hidden': 'true' } }); }
      if (drag.ghost.parentElement !== column) column.appendChild(drag.ghost);
      const top = minutesToPx(Math.max(span.start, windowStart));
      drag.ghost.style.setProperty('top', `${top}px`);
      drag.ghost.style.setProperty('height', `${Math.max(MIN_HEIGHT, minutesToPx(Math.min(span.end, windowEnd)) - top)}px`);
      drag.ghost.style.setProperty('left', '2px');
      drag.ghost.style.setProperty('width', 'calc(100% - 4px)');
      drag.ghost.textContent = '';
      drag.ghost.append(h('span', { class: 'ev-time', text: formatRange(drag.start, drag.end, format) }), h('span', { class: 'ev-title', text: event.title }));
    };

    const finish = (commit: boolean): void => {
      if (!drag) return;
      const done = drag;
      drag = null;
      done.ghost?.remove();
      block.classList.remove('is-dragging');
      if (!commit) return;
      if (!done.moved) { void openEventDialog(ctx, { event }); return; }
      if (done.start.getTime() === start0.getTime() && done.end.getTime() === end0.getTime()) return;
      store.moveEvent(event, done.start, done.end).then(() => toast(event.occurrence !== undefined ? `Moved this one to ${formatTime(done.start, format)}` : `Moved to ${formatTime(done.start, format)}`)).catch((error: unknown) => toast(errorText(error), { kind: 'error' }));
    };

    block.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      const kind = (e.target as HTMLElement).closest('.ev-resize') ? 'resize' : 'move';
      drag = { kind, x: e.clientX, y: e.clientY, moved: false, start: start0, end: end0, ghost: null };
      block.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    block.addEventListener('pointermove', (e) => {
      if (!drag) return;
      if (!drag.moved && Math.abs(e.clientX - drag.x) < 4 && Math.abs(e.clientY - drag.y) < 4) return;
      if (!drag.moved) { drag.moved = true; block.classList.add('is-dragging'); }
      apply(e);
    });
    block.addEventListener('pointerup', (e) => { block.releasePointerCapture(e.pointerId); finish(true); });
    block.addEventListener('pointercancel', () => finish(false));
    block.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && drag) { finish(false); return; }
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void openEventDialog(ctx, { event }); return; }
      if (!e.altKey) return;
      const step = e.key === 'ArrowUp' ? -SNAP : e.key === 'ArrowDown' ? SNAP : 0;
      const dayStep = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0;
      if (!step && !dayStep) return;
      e.preventDefault();
      const start = addDays(new Date(start0.getTime() + (e.shiftKey ? 0 : step) * 60_000), e.shiftKey ? 0 : dayStep);
      const end = e.shiftKey ? new Date(Math.max(start0.getTime() + SNAP * 60_000, end0.getTime() + step * 60_000)) : new Date(start.getTime() + duration);
      store.moveEvent(event, start, end).catch((error: unknown) => toast(errorText(error), { kind: 'error' }));
    });
  }

  return { el: root, destroy: () => { for (const clean of cleanups) clean(); }, scrollTarget: () => body, hiddenCount };
}
