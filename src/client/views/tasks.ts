/** The Tasks page: quick add, filters, and tasks grouped by when they are due. */
import { confirmDialog, h, replace, toast } from '../kit.js';
import type { MountedView, ViewContext } from '../context.js';
import { addTaskForm, taskRow } from '../task-ui.js';
import { errorText } from '../store.js';
import { emptyState } from '../widgets.js';
import { groupTasks } from '../../core/tasks.js';
import type { Priority } from '../../core/types.js';
import { PRIORITIES, PRIORITY_LABEL } from '../../core/types.js';

type Filter = 'open' | 'all' | 'done';

export function mountTasks(host: HTMLElement, ctx: ViewContext): MountedView {
  const { store } = ctx;
  let filter: Filter = 'open';
  let priority: Priority | 'any' = 'any';
  let query = '';
  let collapsedDone = true;
  const form = addTaskForm(ctx);
  const search = h('input', { type: 'search', placeholder: 'Filter tasks', attrs: { 'aria-label': 'Filter tasks', autocomplete: 'off' }, on: { input: () => { query = search.value.trim().toLowerCase(); renderList(); } } });
  const filters = h('div', { class: 'row filters' });
  const list = h('div', { class: 'task-groups' });

  const drawFilters = (): void => {
    const count = (f: Filter): number => store.data.tasks.filter((t) => f === 'all' || t.status === f).length;
    replace(filters,
      h('div', { class: 'seg', attrs: { role: 'group', 'aria-label': 'Show' } }, (['open', 'all', 'done'] as const).map((f) =>
        h('button', { type: 'button', text: `${f === 'open' ? 'Open' : f === 'done' ? 'Done' : 'All'} ${count(f)}`, attrs: { 'aria-pressed': String(filter === f) }, on: { click: () => { filter = f; drawFilters(); renderList(); } } }))),
      h('div', { class: 'chips', attrs: { role: 'group', 'aria-label': 'Priority' } }, (['any', ...PRIORITIES] as const).map((p) =>
        h('button', { class: 'chip', type: 'button', text: p === 'any' ? 'Any priority' : PRIORITY_LABEL[p], attrs: { 'aria-pressed': String(priority === p) }, on: { click: () => { priority = p; drawFilters(); renderList(); } } }))),
      h('div', { class: 'grow' }), search);
  };

  const renderList = (): void => {
    const now = store.now();
    const visible = store.data.tasks.filter((t) => (filter === 'all' || t.status === filter) && (priority === 'any' || t.priority === priority)
      && (!query || `${t.title} ${t.description}`.toLowerCase().includes(query)));
    const groups = groupTasks(visible, now);
    if (groups.length === 0) {
      replace(list, emptyState(store.data.tasks.length === 0 ? 'No tasks yet. Add your first one above.' : 'Nothing matches these filters.'));
      return;
    }
    replace(list, groups.map((group) => {
      const isDone = group.key === 'done';
      const open = !(isDone && collapsedDone && filter === 'all');
      const header = isDone && filter === 'all'
        ? h('button', { class: 'group-toggle', type: 'button', attrs: { 'aria-expanded': String(open) }, on: { click: () => { collapsedDone = !collapsedDone; renderList(); } } }, `${group.label} · ${group.tasks.length}`)
        : h('h2', { class: `group-title${group.key === 'overdue' ? ' overdue' : ''}`, text: `${group.label} · ${group.tasks.length}` });
      return h('section', { class: 'card group' },
        h('div', { class: 'card-head' }, header,
          isDone ? h('button', { class: 'textbtn', type: 'button', text: 'Clear completed', on: { click: () => {
            void confirmDialog({ title: 'Clear completed tasks?', message: `${group.tasks.length} completed ${group.tasks.length === 1 ? 'task' : 'tasks'} will be deleted for good.`, confirmLabel: 'Clear', danger: true }).then((yes) => {
              if (yes) store.clearCompleted().then((n) => toast(`Cleared ${n} completed ${n === 1 ? 'task' : 'tasks'}`)).catch((error: unknown) => toast(errorText(error), { kind: 'error' }));
            });
          } } }) : null),
        open ? h('ul', { class: 'plain-list task-list' }, group.tasks.map((t) => taskRow(ctx, t, { due: true }))) : null);
    }));
  };

  const render = (): void => { drawFilters(); renderList(); };
  replace(host, h('section', { class: 'card add-card' }, form), h('div', { class: 'card filter-card' }, filters), list);
  render();
  const off = store.changed.on((kind) => { if (kind === 'tasks' || kind === 'all' || kind === 'prefs') render(); });
  return { destroy: off };
}
