/** Real collaboration UI: remote identity, invitations, ACLs and synchronization status.
 * Passwords and remote bearer tokens never enter browser localStorage.
 */
import { confirmDialog, h, openDialog, replace, toast } from './kit.js';
import { errorText } from './store.js';
import type { ViewContext } from './context.js';

interface CollaborationState {
  configured: boolean; signed_in: boolean; user: { id: string; username: string } | null;
  status: string; pending: number; conflicts: number; last_error: string | null;
}
interface CloudWorkspace { id: string; name: string; owner_id: string; role: 'owner' | 'editor' | 'viewer' }
interface Invitation { id: string; workspace_id: string; workspace_name: string; role: string; expires_at: number }
interface Member { user_id: string; username: string; role: string }

export function openSharePlanner(ctx: ViewContext, localWorkspaceId?: string): void {
  const status = h('p', { class: 'muted small', attrs: { role: 'status', 'aria-live': 'polite' }, text: 'Checking collaboration…' });
  const error = h('p', { class: 'error small', attrs: { role: 'alert' }, hidden: true });
  const identity = h('section', { class: 'fields' });
  const invitations = h('section', { class: 'fields' });
  const memberships = h('section', { class: 'fields' });
  const conflicts = h('section', { class: 'fields' });
  const actions = h('section', { class: 'fields' });
  const showError = (err: unknown): void => { error.textContent = errorText(err); error.hidden = false; };
  const api = ctx.store.api;

  async function send<T>(path: string, body: object = {}): Promise<T> {
    error.hidden = true;
    try { return await api.post<T>(path, body); }
    catch (err) { showError(err); throw err; }
  }

  async function refresh(): Promise<void> {
    const state = await api.get<CollaborationState>('/collaboration/status');
    status.textContent = !state.configured ? 'Remote collaboration is not configured. Local planning remains fully available.'
      : !state.signed_in ? 'Sign in to share planning with other users.'
      : `${state.user?.username} · ${state.status.replace(/_/g, ' ')} · ${state.pending} outgoing · ${state.conflicts} conflicts`;
    if (state.last_error) error.textContent = state.last_error;
    if (!state.configured) {
      replace(identity, h('p', { class: 'muted', text: 'Set app.collaboration_url to the deployed HTTPS collaboration service. SPlanner’s local server must remain loopback-only.' }));
      replace(actions); replace(invitations); replace(memberships); replace(conflicts);
      return;
    }
    if (!state.signed_in) {
      const username = h('input', { type: 'text', attrs: { maxlength: 80, autocomplete: 'username', 'aria-label': 'Collaboration username', placeholder: 'Username' } });
      const password = h('input', { type: 'password', attrs: { autocomplete: 'current-password', 'aria-label': 'Password', placeholder: 'Password (12+ characters)' } });
      const login = h('button', { class: 'btn small primary', type: 'button', text: 'Sign in', on: { click: () => {
        void send('/collaboration/login', { username: username.value.trim(), password: password.value })
          .then(() => { password.value = ''; return refresh(); }).catch(() => undefined);
      } } });
      const register = h('button', { class: 'btn small', type: 'button', text: 'Create account', on: { click: () => {
        void send('/collaboration/register', { username: username.value.trim(), password: password.value })
          .then(() => send('/collaboration/login', { username: username.value.trim(), password: password.value }))
          .then(() => { password.value = ''; return refresh(); }).catch(() => undefined);
      } } });
      replace(identity, h('h3', { text: 'Collaboration account' }), username, password,
        h('div', { class: 'row' }, login, register));
      replace(actions); replace(invitations); replace(memberships); replace(conflicts);
      return;
    }
    const sync = h('button', { class: 'btn small primary', type: 'button', text: 'Synchronize now', on: { click: () => {
      void send('/collaboration/sync').then(async () => { await ctx.store.refresh(); await refresh(); toast('Planner synchronized', { kind: 'ok' }); })
        .catch(() => undefined);
    } } });
    const logout = h('button', { class: 'btn small', type: 'button', text: 'Sign out', on: { click: () => {
      void send('/collaboration/logout').then(refresh).catch(() => undefined);
    } } });
    replace(identity, h('p', { text: `Signed in as ${state.user?.username ?? ''}` }),
      h('div', { class: 'row' }, sync, logout));
    const [workspaces, pending, unresolved] = await Promise.all([
      api.get<CloudWorkspace[]>('/collaboration/workspaces'),
      api.get<Invitation[]>('/collaboration/invitations'),
      api.get<Array<{ workspace_id: string; resource_type: string; resource_id: string; error: string }>>('/collaboration/conflicts'),
    ]);
    replace(conflicts, h('h3', { text: 'Synchronization conflicts' }),
      unresolved.length ? unresolved.map((item) => {
        const choose = (choice: 'local' | 'remote'): void => {
          void confirmDialog({ title: `Use ${choice} version?`,
            message: choice === 'local' ? 'This will send your local changes over the remote revision.'
              : 'This will discard pending local edits and use the authoritative remote revision.',
            confirmLabel: 'Resolve conflict', danger: true }).then((accepted) => {
            if (!accepted) return;
            void send('/collaboration/resolve', { ...item, choice })
              .then(async () => { await ctx.store.refresh(); await refresh(); }).catch(() => undefined);
          });
        };
        return h('div', { class: 'workspace-remote-row' },
          h('p', { class: 'muted small', text: `${item.resource_type} · ${item.resource_id} · ${item.error}` }),
          h('div', { class: 'row' },
            h('button', { class: 'btn small', type: 'button', text: 'Keep local', on: { click: () => choose('local') } }),
            h('button', { class: 'btn small', type: 'button', text: 'Use remote', on: { click: () => choose('remote') } })));
      }) : h('p', { class: 'muted small', text: 'No unresolved synchronization conflicts.' }));
    replace(invitations, h('h3', { text: 'Invitations for you' }),
      pending.length ? pending.map((inv) => h('div', { class: 'workspace-manager-row' },
        h('span', { class: 'grow', text: `${inv.workspace_name} · ${inv.role}` }),
        h('button', { class: 'btn small primary', type: 'button', text: 'Accept', on: { click: () => {
          void send(`/collaboration/invitations/${inv.id}/accept`).then(async () => { await ctx.store.refresh(); await refresh(); }).catch(() => undefined);
        } } }),
        h('button', { class: 'btn small', type: 'button', text: 'Reject', on: { click: () => {
          void send(`/collaboration/invitations/${inv.id}/reject`).then(refresh).catch(() => undefined);
        } } }))) : h('p', { class: 'muted small', text: 'No pending invitations.' }));

    const published = workspaces.find((ws) => ws.id === localWorkspaceId && ws.role === 'owner');
    const publish = h('button', { class: 'btn small primary', type: 'button', text: 'Publish this workspace', on: { click: () => {
      void confirmDialog({ title: 'Publish this workspace?', danger: true, confirmLabel: 'Publish',
        message: 'Its planning items will become available to members you explicitly invite.' }).then((accepted) => {
        if (accepted) void send('/collaboration/publish').then(refresh).catch(() => undefined);
      });
    } } });
    const inviteUser = h('input', { type: 'text', attrs: { placeholder: 'Registered username', 'aria-label': 'Invite username' } });
    const inviteRole = h('select', { attrs: { 'aria-label': 'Permission' } },
      h('option', { value: 'viewer', text: 'Viewer' }), h('option', { value: 'editor', text: 'Editor' }));
    const invite = h('button', { class: 'btn small primary', type: 'button', text: 'Send invitation', on: { click: () => {
      if (!published) return;
      void send(`/collaboration/workspaces/${published.id}/invite`, { username: inviteUser.value.trim(), role: inviteRole.value })
        .then(() => { inviteUser.value = ''; toast('Invitation sent', { kind: 'ok' }); return refresh(); }).catch(() => undefined);
    } } });
    replace(actions, h('h3', { text: 'Share your workspace' }),
      !localWorkspaceId ? h('p', { class: 'muted', text: 'Select a local workspace to publish it.' })
        : published ? h('div', { class: 'fields' }, h('p', { class: 'muted small', text: 'This workspace is published. Only invited members can access it.' }),
          h('div', { class: 'row' }, inviteUser, inviteRole, invite)) : publish);

    replace(memberships, h('h3', { text: 'Accessible planners' }),
      workspaces.length ? workspaces.map((ws) => {
        const membersSlot = h('div', { class: 'fields' });
        const showMembers = h('button', { class: 'btn small', type: 'button', text: 'Members', on: { click: () => {
          void api.get<Member[]>(`/collaboration/workspaces/${ws.id}/members`).then((all) => {
            replace(membersSlot, all.map((person) => {
              const isOwner = person.role === 'owner';
              const control = h('select', { attrs: { 'aria-label': `Role of ${person.username}`, disabled: ws.role !== 'owner' || isOwner } },
                h('option', { value: 'viewer', text: 'Viewer' }), h('option', { value: 'editor', text: 'Editor' }),
                h('option', { value: 'owner', text: 'Owner' }));
              control.value = person.role;
              control.addEventListener('change', () => {
                void api.patch(`/collaboration/workspaces/${ws.id}/members/${person.user_id}`, { role: control.value }).catch(showError);
              });
              const revoke = h('button', { class: 'btn small ghost', type: 'button', text: 'Remove',
                attrs: { disabled: ws.role !== 'owner' || isOwner }, on: { click: () => {
                  void api.patch(`/collaboration/workspaces/${ws.id}/members/${person.user_id}`, { role: null })
                    .then(refresh).catch(showError);
                } } });
              return h('div', { class: 'workspace-manager-row' },
                h('span', { class: 'grow', text: person.username }), control, revoke);
            }));
          }).catch(showError);
        } } });
        return h('div', { class: 'workspace-remote-row' },
          h('div', { class: 'workspace-manager-row' }, h('strong', { class: 'grow', text: `${ws.name} · ${ws.role}` }), showMembers), membersSlot);
      }) : h('p', { class: 'muted', text: 'No cloud workspaces. Publish one or accept an invitation.' }));
  }

  openDialog<true>({ title: 'Share planner', eyebrow: 'SECURE COLLABORATION', size: 'wide', cancelLabel: 'Close',
    body: [status, error, identity, actions, invitations, memberships, conflicts], actions: [] });
  void refresh().catch(showError);
}
