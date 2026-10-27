/** What the person is looking at: which view, which day, and the calendar's week/day/month mode. Kept apart from the data in the store. */
import { Emitter } from './kit.js';
import { addDays, addMonths, startOfDay } from '../core/dates.js';
import { VIEWS, type ViewName } from '../core/types.js';

export type CalendarMode = 'week' | 'day' | 'month';
export interface Route { view: ViewName; id?: string }
export type UiChange = 'focus' | 'mode' | 'route';

export function parseRoute(hash: string, fallback: ViewName): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  const view = VIEWS.find((v) => v === parts[0]);
  if (!view) return { view: fallback };
  const id = parts[1];
  return id && /^[A-Za-z0-9_-]{1,64}$/.test(id) ? { view, id } : { view };
}

export function routeHref(route: Route): string { return `#/${route.view}${route.id ? `/${route.id}` : ''}`; }

export class UiState {
  readonly changed = new Emitter<UiChange>();
  focus: Date = startOfDay(new Date());
  mode: CalendarMode = 'week';
  route: Route;
  private readonly fallback: () => ViewName;

  constructor(fallback: () => ViewName) {
    this.fallback = fallback;
    this.route = parseRoute(window.location.hash, fallback());
    window.addEventListener('hashchange', () => {
      const next = parseRoute(window.location.hash, this.fallback());
      if (next.view === this.route.view && next.id === this.route.id) return;
      this.route = next;
      this.changed.emit('route');
    });
  }

  go(route: Route): void {
    const href = routeHref(route);
    if (window.location.hash === href) return;
    window.location.hash = href;                 // the hashchange listener updates `route` and notifies
  }

  /** Replace the address without adding a history entry (a draft note becoming a saved note, say). */
  replaceRoute(route: Route): void {
    this.route = route;
    window.history.replaceState(null, '', routeHref(route));
  }

  setFocus(day: Date): void { this.focus = startOfDay(day); this.changed.emit('focus'); }

  today(): void { this.setFocus(new Date()); }

  setMode(mode: CalendarMode): void { if (mode !== this.mode) { this.mode = mode; this.changed.emit('mode'); } }

  /** Previous or next week, day or month depending on what is on screen. */
  shift(direction: -1 | 1, unit: CalendarMode = this.route.view === 'calendar' ? this.mode : 'week'): void {
    this.setFocus(unit === 'month' ? addMonths(this.focus, direction) : addDays(this.focus, direction * (unit === 'week' ? 7 : 1)));
  }
}
