/** Dedicated workspace management, independent of Appearance preferences. */
import { h, openDialog, replace, toast } from './kit.js';
import { errorText } from './store.js';
import type { ViewContext } from './context.js';
import { openSharePlanner } from './sharing.js';

interface Workspace {
  id: string; name: string; description: string; color: string; icon: string;
  owner_id: string; kind: 'personal' | 'shared'; archived: boolean; version: number;
  created_at: string; updated_at: string;
}
interface WorkspaceCollection { active_id: string; workspaces: Workspace[]; archived: Workspace[] }

export async function loadActiveWorkspace(ctx: ViewContext): Promise<Workspace | undefined> {
  const state = await ctx.store.api.get<WorkspaceCollection>('/workspaces');
  return state.workspaces.find((w) => w.id === state.active_id);
}

export function openWorkspaceDialog(ctx: ViewContext, onChange: (name: string) => void = () => undefined): void {
  const status = h('p', { class: 'muted small', attrs: { role: 'status', 'aria-live': 'polite' }, text: 'Loading workspaces…' });
  const list = h('div', { class: 'workspace-manager-list' });
  const archived = h('div', { class: 'workspace-manager-list' });
  const editor = h('div', { class: 'workspace-manager-edit' });
  const error = h('p', { class: 'error small', attrs: { role: 'alert' }, hidden: true });
  let state: WorkspaceCollection | undefined;
  let selected: string | null = null;
  const showError = (err: unknown): void => { error.hidden = false; error.textContent = errorText(err); };

  async function refresh(): Promise<void> {
    state = await ctx.store.api.get<WorkspaceCollection>('/workspaces');
    if (!selected) selected = state.active_id;
    draw();
  }
  async function switchTo(id: string): Promise<void> {
    try {
      await ctx.store.api.post(`/workspaces/${id}/select`, {});
      selected = id;
      await ctx.store.refresh();
      await refresh();
      const active = state?.workspaces.find((w) => w.id === id);
      if (active) onChange(active.name);
      toast(`Switched to ${active?.name ?? 'workspace'}`, { kind: 'ok' });
    } catch (err) { showError(err); }
  }
  function editorFor(ws: Workspace): HTMLElement {
    const name = h('input', { type: 'text', value: ws.name, attrs: { maxlength: 80, 'aria-label': 'Workspace name' } });
    const description = h('textarea', { rows: 3, value: ws.description, attrs: { maxlength: 1000, 'aria-label': 'Workspace description' } });
    const color = h('select', { attrs: { 'aria-label': 'Workspace color' } },
      ['blue', 'violet', 'green', 'pink', 'amber'].map((c) => h('option', { text: c, value: c })));
    color.value = ws.color;
    const save = h('button', { class: 'btn small primary', type: 'button', text: 'Save workspace', on: { click: () => {
      void ctx.store.api.patch<Workspace>(`/workspaces/${ws.id}`,
        { name: name.value, description: description.value, color: color.value, version: ws.version })
        .then(async (updated) => { await refresh(); if (ws.id === state?.active_id) onChange(updated.name); toast('Workspace saved', { kind: 'ok' }); })
        .catch(showError);
    } } });
    const archive = h('button', { class: 'btn small', type: 'button', text: 'Archive',
      attrs: { disabled: ws.id === state?.active_id || ws.id === '00000000000000000000000000000001' ? '' : undefined },
      on: { click: () => { void ctx.store.api.post(`/workspaces/${ws.id}/archive`, {}).then(refresh).catch(showError); } } });
    const remove = h('button', { class: 'btn small ghost', type: 'button', text: 'Delete empty workspace',
      attrs: { disabled: ws.id === state?.active_id || ws.id === '00000000000000000000000000000001' ? '' : undefined },
      on: { click: () => {
        if (!window.confirm(`Delete empty workspace “${ws.name}”? This is not reversible.`)) return;
        void ctx.store.api.del(`/workspaces/${ws.id}`).then(async () => { selected = null; await refresh(); }).catch(showError);
      } } });
    const share = h('button', { class: 'btn small', type: 'button', text: 'Share planner', on: { click: () => openSharePlanner(ctx, ws.id) } });
    return h('div', { class: 'workspace-form fields' },
      h('h3', { text: `Manage ${ws.name}` }),
      h('label', { class: 'import-field' }, h('span', { class: 'label', text: 'Name' }), name),
      h('label', { class: 'import-field' }, h('span', { class: 'label', text: 'Description' }), description),
      h('label', { class: 'import-field' }, h('span', { class: 'label', text: 'Color' }), color),
      h('div', { class: 'row' }, save, share, archive, remove));
  }
  function draw(): void {
    if (!state) return;
    status.textContent = `${state.workspaces.length} active workspace${state.workspaces.length === 1 ? '' : 's'} · Each workspace keeps independent local planning data.`;
    replace(list, state.workspaces.map((ws) => {
      const selectedNow = ws.id === state?.active_id;
      const select = h('button', { class: `btn small ${selectedNow ? 'primary' : ''}`, type: 'button',
        text: selectedNow ? 'Active' : 'Switch', attrs: { disabled: selectedNow ? '' : undefined },
        on: { click: () => { void switchTo(ws.id); } } });
      const manage = h('button', { class: 'btn small ghost', type: 'button', text: 'Manage', on: { click: () => { selected = ws.id; draw(); } } });
      return h('div', { class: 'workspace-manager-row' },
        h('span', { class: `workspace-color-dot color-${ws.color}`, attrs: { 'aria-hidden': 'true' } }),
        h('span', { class: 'grow clip' }, h('strong', { text: ws.name }), h('small', { class: 'muted', text: ws.description || ws.kind })),
        select, manage);
    }));
    replace(archived, state.archived.length ? state.archived.map((ws) => h('div', { class: 'workspace-manager-row' },
      h('strong', { class: 'grow', text: ws.name }),
      h('button', { class: 'btn small', type: 'button', text: 'Restore', on: { click: () => {
        void ctx.store.api.post(`/workspaces/${ws.id}/restore`, {}).then(refresh).catch(showError);
      } } }))) : h('p', { class: 'muted small', text: 'No archived workspaces.' }));
    const target = state.workspaces.find((w) => w.id === selected) ?? state.workspaces[0];
    replace(editor, target ? editorFor(target) : h('p', { class: 'muted', text: 'Select a workspace to manage it.' }));
  }

  const createName = h('input', { type: 'text', attrs: { maxlength: 80, placeholder: 'e.g. University, Work, Family', 'aria-label': 'New workspace name' } });
  const create = h('button', { class: 'btn small primary', type: 'button', text: 'Create workspace', on: { click: () => {
    error.hidden = true;
    void ctx.store.api.post<Workspace>('/workspaces', { name: createName.value.trim() })
      .then(async (ws) => { createName.value = ''; selected = ws.id; await refresh(); toast('Workspace created', { kind: 'ok' }); })
      .catch(showError);
  } } });
  openDialog<true>({
    title: 'Workspaces', eyebrow: 'YOUR PLANNING ENVIRONMENTS', size: 'wide', cancelLabel: 'Close',
    body: [status, list, h('h3', { text: 'Create workspace' }), h('div', { class: 'row' }, createName, create),
      editor, h('h3', { text: 'Archived' }), archived, error],
    actions: [],
  });
  void refresh().catch(showError);
}
