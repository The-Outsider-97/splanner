/** Notes: a list and an editor. Every note is an SLDC document on disk (written by SLAI's own SLDC library); this page edits plain text.
 *  Typing saves automatically after a short pause, with an optimistic-lock version so an edit made elsewhere is never silently overwritten. */
import { announce, confirmDialog, copyText, debounce, glyph, h, replace, saveBlob, toast, type MenuEntry } from '../kit.js';
import type { MountedView, ViewContext } from '../context.js';
import { errorText } from '../store.js';
import { emptyState, moreButton } from '../widgets.js';
import { relativeDay, parseInstant } from '../../core/dates.js';
import type { Note, NoteSummary } from '../../core/types.js';

type SaveState = 'saved' | 'saving' | 'dirty' | 'failed' | 'readonly';
const STATUS: Record<SaveState, string> = { saved: 'All changes saved', saving: 'Saving…', dirty: 'Unsaved changes', failed: 'Couldn’t save — your text is still here', readonly: 'Read-only' };

const titleFrom = (title: string, content: string): string => title.trim() || content.split('\n').map((l) => l.trim()).find(Boolean)?.slice(0, 60) || 'Untitled note';

export function mountNotes(host: HTMLElement, ctx: ViewContext): MountedView {
  const { store, ui } = ctx;
  let note: Note | null = null;               // the saved note being edited (null for a draft or nothing selected)
  let draft = false;
  let state: SaveState = 'saved';
  let loadingId: string | null = null;
  let hits: Set<string> | null = null;
  let query = '';
  let version = 0;
  let saving: Promise<void> | null = null;
  let conflict = false;

  const listEl = h('div', { class: 'notes-items', attrs: { role: 'list' } });
  const search = h('input', { type: 'search', placeholder: 'Search notes', attrs: { 'aria-label': 'Search notes', autocomplete: 'off' } });
  const editorEl = h('section', { class: 'card note-editor', attrs: { 'aria-label': 'Note editor' } });
  const layout = h('div', { class: 'notes-layout' },
    h('section', { class: 'card notes-list', attrs: { 'aria-label': 'All notes' } },
      h('div', { class: 'card-head' }, search, h('button', { class: 'btn small primary', type: 'button', on: { click: () => ui.go({ view: 'notes', id: 'new' }) } }, glyph('plus'), 'New')), listEl), editorEl);
  replace(host, layout);

  const runSearch = debounce(() => {
    if (!query) { hits = null; drawList(); return; }
    store.search(query).then((found) => { hits = new Set(found.filter((f) => f.kind === 'note').map((f) => f.id)); drawList(); }).catch(() => { hits = null; drawList(); });
  }, 220);
  search.addEventListener('input', () => { query = search.value.trim(); runSearch(); });

  function drawList(): void {
    const now = store.now();
    const items = store.data.notes.filter((n) => !query || (hits ? hits.has(n.id) : `${n.title} ${n.excerpt}`.toLowerCase().includes(query.toLowerCase())));
    const selected = ui.route.id && ui.route.id !== 'new' ? ui.route.id : null;
    if (items.length === 0) { replace(listEl, emptyState(store.data.notes.length === 0 ? 'No notes yet. Start with a thought.' : 'No notes match your search.', store.data.notes.length === 0 ? { label: 'New note', run: () => ui.go({ view: 'notes', id: 'new' }) } : undefined)); return; }
    replace(listEl, items.map((n) => noteItem(n, selected === n.id, now)));
  }

  function noteItem(n: NoteSummary, selected: boolean, now: Date): HTMLElement {
    const when = relativeDay(parseInstant(n.updated_at), now);
    return h('a', { class: `note-item${selected ? ' is-selected' : ''}`, href: `#/notes/${n.id}`, attrs: { role: 'listitem', ...(selected ? { 'aria-current': 'true' } : {}) } },
      h('span', { class: 'note-item-title clip' }, n.pinned ? h('span', { class: 'pin', attrs: { 'aria-label': 'Pinned' } }, glyph('pin')) : null, n.title),
      h('span', { class: 'note-item-excerpt', text: n.excerpt || 'No text yet' }), h('span', { class: 'note-item-when muted small', text: when }));
  }

  // ---------------------------------------------------------------- editor
  let titleInput: HTMLInputElement | null = null;
  let bodyInput: HTMLTextAreaElement | null = null;
  let statusEl: HTMLElement | null = null;
  let noticeEl: HTMLElement | null = null;
  let pinButton: HTMLButtonElement | null = null;

  const setState = (next: SaveState): void => { state = next; if (statusEl) { statusEl.textContent = STATUS[next]; statusEl.dataset['state'] = next; } };

  function showEmpty(): void {
    note = null; draft = false; titleInput = bodyInput = statusEl = noticeEl = null;
    layout.classList.remove('has-note');
    replace(editorEl, emptyState(store.data.notes.length ? 'Choose a note to read or edit it.' : 'Your notes live here. Create one to begin.', { label: 'New note', run: () => ui.go({ view: 'notes', id: 'new' }) }));
  }

  function showError(message: string, id: string | null): void {
    titleInput = bodyInput = statusEl = noticeEl = null;
    layout.classList.add('has-note');
    replace(editorEl, backButton(), h('div', { class: 'notice err', attrs: { role: 'alert' } }, glyph('alert'), h('div', { class: 'grow', text: message })),
      id ? h('div', { class: 'row' }, h('button', { class: 'btn danger', type: 'button', on: { click: () => { void removeNote(id, null); } } }, glyph('trash'), 'Delete this note')) : null);
  }

  const backButton = (): HTMLElement => h('a', { class: 'btn small ghost back-link', href: '#/notes' }, glyph('chevron-left'), 'All notes');

  function menuEntries(): MenuEntry[] {
    const current = note;
    return [
      { label: current?.pinned ? 'Unpin' : 'Pin to top', icon: 'pin', disabled: !current, run: () => { void togglePin(); } },
      { label: 'Copy text', icon: 'copy', run: () => { void copyText(bodyInput?.value ?? '').then((ok) => toast(ok ? 'Copied' : 'Copy is not available here', { kind: ok ? 'ok' : 'error' })); } },
      { label: 'Download as .sldc', icon: 'download', disabled: !current, run: () => { void download(); } },
      'separator',
      { label: 'Delete note', icon: 'trash', danger: true, disabled: !current, run: () => { if (current) void removeNote(current.id, current); } },
    ];
  }

  function showEditor(next: Note | null): void {
    note = next; draft = next === null; conflict = false; version = next?.version ?? 0;
    layout.classList.add('has-note');
    const readonly = next ? !next.editable : false;
    titleInput = h('input', { class: 'note-title', type: 'text', value: next?.title ?? '', placeholder: 'Untitled note', attrs: { maxlength: 180, 'aria-label': 'Note title', autocomplete: 'off' } });
    bodyInput = h('textarea', { class: 'note-body', placeholder: 'Start writing…', attrs: { 'aria-label': 'Note text', spellcheck: 'true' } });
    bodyInput.value = next?.content ?? '';
    titleInput.readOnly = readonly; bodyInput.readOnly = readonly;
    statusEl = h('span', { class: 'save-state muted', attrs: { role: 'status', 'aria-live': 'polite' } });
    noticeEl = h('div', { class: 'notice warn', hidden: !readonly, attrs: { role: 'note' } }, glyph('lock'), h('div', { class: 'grow', text: 'This note has formatting, images or a signature that SPlanner can’t edit without losing it, so it is read-only here. Open the .sldc file in Reader or Writer to change it.' }));
    const pin = h('button', { class: 'iconbtn', type: 'button', attrs: { 'aria-label': next?.pinned ? 'Unpin note' : 'Pin note', 'aria-pressed': String(next?.pinned ?? false) }, disabled: !next, on: { click: () => { void togglePin(); } } }, glyph('pin'));
    pinButton = pin;
    replace(editorEl,
      h('div', { class: 'note-head' }, backButton(), titleInput, pin, moreButton('Note actions', menuEntries)),
      noticeEl, bodyInput,
      h('div', { class: 'note-foot' }, statusEl, h('span', { class: 'muted small', text: next ? `Stored on this device as an SLDC document · edited ${relativeDay(parseInstant(next.updated_at), store.now())}` : 'Starts saving as soon as you type' })));
    setState(readonly ? 'readonly' : 'saved');
    if (!readonly) { const mark = (): void => { if (conflict) return; setState('dirty'); queueSave(); }; titleInput.addEventListener('input', mark); bodyInput.addEventListener('input', mark); }
    if (draft) titleInput.focus();
  }

  const queueSave = debounce(() => { void save(); }, 700);

  async function save(): Promise<void> {
    if (saving) { await saving; if (state !== 'dirty') return; }
    if (!titleInput || !bodyInput || conflict) return;
    const content = bodyInput.value;
    const typedTitle = titleInput.value;
    const title = titleFrom(typedTitle, content);
    if (draft && !titleInput.value.trim() && !content.trim()) { setState('saved'); return; }
    setState('saving');
    const work = (async (): Promise<void> => {
      try {
        if (draft) {
          const created = await store.createNote({ title, content });
          note = created; draft = false; version = created.version;
          ui.replaceRoute({ view: 'notes', id: created.id });
          drawList();
        } else if (note) {
          const updated = await store.updateNote(note.id, { title, content, version });
          note = updated; version = updated.version;
        }
        if (bodyInput && titleInput && bodyInput.value === content && titleInput.value === typedTitle) setState('saved'); else { setState('dirty'); queueSave(); }
      } catch (error) {
        const status = (error as { status?: number }).status;
        if (status === 409) { conflict = true; showConflict(errorText(error)); } else { setState('failed'); toast(errorText(error), { kind: 'error' }); }
      }
    })();
    saving = work;
    await work;
    saving = null;
  }

  function showConflict(message: string): void {
    setState('failed');
    if (!noticeEl) return;
    noticeEl.hidden = false;
    noticeEl.className = 'notice err';
    replace(noticeEl, glyph('alert'), h('div', { class: 'grow', text: `${message}` }),
      h('button', { class: 'btn small', type: 'button', text: 'Load the latest', on: { click: () => { if (note) void openNote(note.id, true); } } }),
      h('button', { class: 'btn small', type: 'button', text: 'Keep my text', on: { click: () => { void keepMine(); } } }));
  }

  async function keepMine(): Promise<void> {
    if (!note || !bodyInput || !titleInput) return;
    const mine = { title: titleFrom(titleInput.value, bodyInput.value), content: bodyInput.value };
    try {
      const latest = await store.getNote(note.id);
      const updated = await store.updateNote(note.id, { ...mine, version: latest.version });
      note = updated; version = updated.version; conflict = false;
      if (noticeEl) noticeEl.hidden = true;
      setState('saved');
      toast('Your text was saved over the other change', { kind: 'ok' });
    } catch (error) { toast(errorText(error), { kind: 'error' }); }
  }

  async function togglePin(): Promise<void> {
    if (!note) return;
    try {
      note = await store.updateNote(note.id, { pinned: !note.pinned, version: note.version });
      version = note.version;
      pinButton?.setAttribute('aria-pressed', String(note.pinned));
      pinButton?.setAttribute('aria-label', note.pinned ? 'Unpin note' : 'Pin note');
      announce(note.pinned ? 'Pinned' : 'Unpinned');
    } catch (error) { toast(errorText(error), { kind: 'error' }); }
  }

  async function download(): Promise<void> {
    if (!note) return;
    try { const file = await store.api.download(`/notes/${note.id}/file`); saveBlob(file.blob, file.filename); } catch (error) { toast(errorText(error), { kind: 'error' }); }
  }

  async function removeNote(id: string, current: Note | null): Promise<void> {
    const yes = await confirmDialog({ title: 'Delete this note?', message: `“${current?.title ?? 'This note'}” and its file will be deleted for good.`, confirmLabel: 'Delete', danger: true });
    if (!yes) return;
    try { queueSave.cancel(); await store.deleteNote(id); toast('Note deleted'); ui.go({ view: 'notes' }); } catch (error) { toast(errorText(error), { kind: 'error' }); }
  }

  async function openNote(id: string, force = false): Promise<void> {
    if (!force && note?.id === id) return;
    await flush();
    loadingId = id;
    try {
      const loaded = await store.getNote(id);
      if (loadingId !== id) return;
      showEditor(loaded);
    } catch (error) {
      if (loadingId !== id) return;
      showError(errorText(error), id);
    }
  }

  async function flush(): Promise<void> {
    if (state === 'dirty') { queueSave.cancel(); await save(); } else if (saving) await saving;
  }

  function route(): void {
    const id = ui.route.id;
    layout.classList.toggle('has-note', Boolean(id));
    if (id === 'new') { void flush().then(() => { showEditor(null); drawList(); }); return; }
    if (id) void openNote(id).then(drawList); else { void flush().then(() => { showEmpty(); drawList(); }); }
    drawList();
  }

  const beforeUnload = (event: BeforeUnloadEvent): void => { if (state === 'dirty' || state === 'saving' || state === 'failed') { event.preventDefault(); event.returnValue = ''; } };
  const onHide = (): void => { if (document.visibilityState === 'hidden' && state === 'dirty') { queueSave.cancel(); void save(); } };
  window.addEventListener('beforeunload', beforeUnload);
  document.addEventListener('visibilitychange', onHide);

  route();
  const offs = [
    store.changed.on((kind) => { if (kind === 'notes' || kind === 'all') drawList(); }),
    ui.changed.on((kind) => { if (kind === 'route') route(); }),
  ];
  return { destroy: () => {
    for (const off of offs) off();
    window.removeEventListener('beforeunload', beforeUnload);
    document.removeEventListener('visibilitychange', onHide);
    runSearch.cancel();
    if (state === 'dirty') { queueSave.cancel(); void save(); }
  } };
}
