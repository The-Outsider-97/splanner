/** One task row, used by the dashboard's priorities and the Tasks page, with the actions both offer. */
import { confirmDialog, glyph, h, nextId, toast, type MenuEntry } from './kit.js';
import { errorText } from './store.js';
import { openTaskDialog } from './editors.js';
import type { ViewContext } from './context.js';
import { moreButton, priorityPill } from './widgets.js';
import { dueLabel, isOverdue } from '../core/tasks.js';
import { PRIORITIES, PRIORITY_LABEL, type Task } from '../core/types.js';

export function taskEntries(ctx: ViewContext, task: Task): MenuEntry[] {
  const fail = (error: unknown): void => { toast(errorText(error), { kind: 'error' }); };
  return [
    { label: 'Edit', icon: 'edit', run: () => { openTaskDialog(ctx, { task }); } },
    { label: task.status === 'done' ? 'Mark as open' : 'Mark as done', icon: 'check', run: () => { ctx.store.toggleTask(task).catch(fail); } },
    'separator',
    ...PRIORITIES.filter((p) => p !== task.priority).map((priority): MenuEntry => ({ label: `Set priority: ${PRIORITY_LABEL[priority]}`, icon: 'flag',
      run: () => { ctx.store.updateTask(task.id, { priority, version: task.version }).catch(fail); } })),
    'separator',
    { label: 'Delete', icon: 'trash', danger: true, run: () => {
      void confirmDialog({ title: 'Delete this task?', message: `“${task.title}” will be removed.`, confirmLabel: 'Delete', danger: true }).then((yes) => {
        if (yes) ctx.store.deleteTask(task.id).then(() => toast('Task deleted')).catch(fail);
      });
    } },
  ];
}

export function taskRow(ctx: ViewContext, task: Task, options: { due?: boolean } = {}): HTMLElement {
  const { store } = ctx;
  const id = nextId('task');
  const done = task.status === 'done';
  const box = h('input', { type: 'checkbox', id, checked: done, attrs: { 'aria-label': `${done ? 'Mark open' : 'Mark done'}: ${task.title}` },
    on: { change: () => { store.toggleTask(task).catch((error: unknown) => { box.checked = done; toast(errorText(error), { kind: 'error' }); }); } } });
  const now = store.now();
  const overdue = isOverdue(task, now);
  const due = options.due && task.due_at ? dueLabel(task, now, store.prefs.time_format) : '';
  return h('li', { class: `task-row${done ? ' is-done' : ''}${overdue ? ' is-overdue' : ''}` },
    h('span', { class: 'check' }, box),
    h('button', { class: 'task-title clip', type: 'button', attrs: { title: task.description ? `${task.title}\n${task.description}` : task.title }, on: { click: () => { openTaskDialog(ctx, { task }); } } }, task.title),
    due ? h('span', { class: `due${overdue ? ' overdue' : ''}`, text: due }, ) : null,
    done ? null : priorityPill(task.priority),
    moreButton(`Actions for ${task.title}`, () => taskEntries(ctx, task)),
    task.description && options.due ? h('span', { class: 'visually-hidden', text: task.description }) : null,
  );
}

export function addTaskForm(ctx: ViewContext): HTMLFormElement {
  const input = h('input', { type: 'text', placeholder: 'Add a task and press Enter', attrs: { maxlength: 180, 'aria-label': 'New task title', autocomplete: 'off' } });
  const form: HTMLFormElement = h('form', { class: 'quick-add', attrs: { novalidate: true },
    on: { submit: (event) => {
      event.preventDefault();
      const title = input.value.trim();
      if (!title) { input.focus(); return; }
      input.disabled = true;
      ctx.store.createTask({ title }).then(() => { input.value = ''; }).catch((error: unknown) => toast(errorText(error), { kind: 'error' })).finally(() => { input.disabled = false; input.focus(); });
    } } },
    h('span', { class: 'plus', attrs: { 'aria-hidden': 'true' } }, glyph('plus')), input,
    h('button', { class: 'btn small', type: 'button', on: { click: () => { openTaskDialog(ctx, input.value.trim() ? { title: input.value.trim() } : {}); } } }, 'More options'));
  return form;
}
