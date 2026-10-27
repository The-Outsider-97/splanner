/** Search across events, tasks, notes (including their text), sticky notes and reminders. Opens with Ctrl+K (⌘K) or "/". */
import { glyph, h, openDialog, replace, debounce } from './kit.js';
import type { ViewContext } from './context.js';
import { errorText } from './store.js';
import { openEventDialog, openReminderDialog, openTaskDialog } from './editors.js';
import { formatWhen, parseInstant, startOfDay } from '../core/dates.js';
import type { SearchHit } from '../core/types.js';

const ICON: Record<SearchHit['kind'], string> = { event: 'calendar', task: 'check-square', note: 'note', sticky: 'sticky', reminder: 'bell' };
const KIND: Record<SearchHit['kind'], string> = { event: 'Event', task: 'Task', note: 'Note', sticky: 'Sticky note', reminder: 'Reminder' };

export function openSearchDialog(ctx: ViewContext): void {
  const { store, ui } = ctx;
  let hits: SearchHit[] = [];
  let active = 0;
  const input = h('input', { type: 'search', placeholder: 'Search events, tasks, notes…', attrs: { 'aria-label': 'Search', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'search-results', autocomplete: 'off', maxlength: 120 } });
  const results = h('ul', { class: 'plain-list search-results', id: 'search-results', attrs: { role: 'listbox', 'aria-label': 'Results' } });
  const status = h('p', { class: 'muted small', attrs: { role: 'status', 'aria-live': 'polite' }, text: 'Type to search. Use ↑ ↓ and Enter.' });

  const choose = (hit: SearchHit): void => {
    handle.close(null);
    if (hit.kind === 'note') ui.go({ view: 'notes', id: hit.id });
    else if (hit.kind === 'sticky') ui.go({ view: 'board' });
    else if (hit.kind === 'event') {
      const event = store.data.events.find((e) => e.id === hit.id);
      if (event) { ui.setFocus(startOfDay(parseInstant(event.start_at))); ui.setMode('day'); ui.go({ view: 'calendar' }); void openEventDialog(ctx, { event }); }
    } else if (hit.kind === 'task') {
      const task = store.data.tasks.find((t) => t.id === hit.id);
      if (task) openTaskDialog(ctx, { task });
    } else {
      const reminder = store.data.reminders.find((r) => r.id === hit.id);
      if (reminder) openReminderDialog(ctx, reminder);
    }
  };

  const draw = (): void => {
    const now = store.now();
    replace(results, hits.map((hit, i) => h('li', { class: `search-hit${i === active ? ' is-active' : ''}`, id: `hit-${i}`, attrs: { role: 'option', 'aria-selected': String(i === active) }, on: { click: () => choose(hit), mousemove: () => { if (active !== i) { active = i; draw(); } } } },
      h('span', { class: 'hit-icon', attrs: { 'aria-hidden': 'true' } }, glyph(ICON[hit.kind])),
      h('span', { class: 'grow clip' }, h('span', { class: 'hit-title clip', text: hit.title }), h('span', { class: 'muted small clip hit-detail', text: [KIND[hit.kind], hit.detail].filter(Boolean).join(' · ') })),
      hit.at ? h('span', { class: 'muted small', text: formatWhen(hit.at, now, store.prefs.time_format) }) : null)));
    input.setAttribute('aria-activedescendant', hits.length ? `hit-${active}` : '');
  };

  const run = debounce(() => {
    const q = input.value.trim();
    if (!q) { hits = []; status.textContent = 'Type to search. Use ↑ ↓ and Enter.'; draw(); return; }
    store.search(q).then((found) => {
      if (input.value.trim() !== q) return;
      hits = found; active = 0;
      status.textContent = found.length ? `${found.length} ${found.length === 1 ? 'result' : 'results'}` : `Nothing found for “${q}”.`;
      draw();
    }).catch((error: unknown) => { status.textContent = errorText(error); });
  }, 160);

  input.addEventListener('input', () => run());
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' && hits.length) { event.preventDefault(); active = (active + 1) % hits.length; draw(); document.getElementById(`hit-${active}`)?.scrollIntoView({ block: 'nearest' }); }
    else if (event.key === 'ArrowUp' && hits.length) { event.preventDefault(); active = (active - 1 + hits.length) % hits.length; draw(); document.getElementById(`hit-${active}`)?.scrollIntoView({ block: 'nearest' }); }
    else if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); const hit = hits[active]; if (hit) choose(hit); }
  });

  const handle = openDialog<true>({ title: 'Search', eyebrow: 'FIND ANYTHING', size: 'normal', cancelLabel: 'Close', focus: () => input, body: [input, status, results] });
  void handle.result.then(() => run.cancel());
}
