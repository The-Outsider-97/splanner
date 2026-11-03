/** Native SLDC import: no planning item is saved without explicit confirmation.
 * Source packages are validated by SLAI; this UI edits only proposed metadata.
 */
import { h, openDialog, replace, toast } from './kit.js';
import { errorText } from './store.js';
import type { ViewContext } from './context.js';

type ProposalType = 'task' | 'event' | 'note';
interface Proposal {
  key: string; type: ProposalType; title: string; description: string;
  source_section: string; source_block: string | null;
  confidence: number; uncertainty: string[]; already_imported?: boolean;
  preserve_original?: boolean; priority?: 'low' | 'normal' | 'high';
  due_at?: string | null; start_at?: string | null; end_at?: string | null;
  time_zone?: string; location?: string; remind_before?: number | null;
}
interface ImportPreview { id: string; filename: string; title: string; sha256: string; state: string; proposals: Proposal[] }

const textField = (label: string, text: string, onChange: (value: string) => void, multiline = false): HTMLElement => {
  const input: HTMLInputElement | HTMLTextAreaElement = multiline ?
    h('textarea', { rows: 3, value: text }) : h('input', { type: 'text', value: text });
  input.addEventListener('input', () => onChange(input.value));
  return h('label', { class: 'import-field' }, h('span', { class: 'muted small', text: label }), input);
};

const optional = (value: string | null | undefined): string => value ?? '';

/** A timezone-safe field: ISO 8601 instants are not silently interpreted in the browser's timezone. */
function instantField(label: string, value: string | null | undefined,
                      onChange: (iso: string | null) => void): HTMLElement {
  const control = h('input', { type: 'text', value: optional(value), attrs: {
    'aria-label': label, placeholder: '2026-11-12T09:00:00+01:00', spellcheck: 'false',
  } });
  control.addEventListener('input', () => onChange(control.value.trim() || null));
  return h('label', { class: 'import-field' }, h('span', { class: 'muted small', text: label }), control);
}

function previewDialog(ctx: ViewContext, preview: ImportPreview): Promise<void> {
  const choices = preview.proposals.map((proposal) => ({
    selected: !proposal.already_imported,
    data: { ...proposal, uncertainty: [...proposal.uncertainty] },
  }));
  const list = h('div', { class: 'import-proposals' });
  const error = h('p', { class: 'error small', attrs: { role: 'alert' }, hidden: true });
  const summary = h('p', { class: 'muted small', attrs: { role: 'status', 'aria-live': 'polite' } });

  const draw = (): void => {
    replace(list, choices.map((choice, index) => {
      const item = choice.data;
      const toggle = h('input', { type: 'checkbox', checked: choice.selected,
        attrs: { 'aria-label': `Include ${item.title}` }, on: { change: () => { choice.selected = toggle.checked; drawCount(); } } });
      const kind = h('select', { attrs: { 'aria-label': `Type for ${item.title}` } },
        (['task', 'event', 'note'] as const).map((value) => h('option', { value, text: value === 'event' ? 'Calendar event' : value === 'task' ? 'Task' : 'Note' })));
      kind.value = item.type;
      kind.addEventListener('change', () => { item.type = kind.value as ProposalType; draw(); });
      const title = textField('Title', item.title, (v) => { item.title = v; });
      const description = textField('Description', item.description, (v) => { item.description = v; }, true);
      const dates: HTMLElement[] = [];
      if (item.type === 'task') {
        dates.push(instantField('Due (optional)', item.due_at, (value) => { item.due_at = value; }));
        const priority = h('select', { attrs: { 'aria-label': 'Task priority' } },
          (['low', 'normal', 'high'] as const).map((p) => h('option', { value: p, text: `${p[0]?.toUpperCase()}${p.slice(1)}` })));
        priority.value = item.priority ?? 'normal';
        priority.addEventListener('change', () => { item.priority = priority.value as 'low' | 'normal' | 'high'; });
        dates.push(h('label', { class: 'import-field' }, h('span', { class: 'muted small', text: 'Priority' }), priority));
      } else if (item.type === 'event') {
        dates.push(instantField('Start (required)', item.start_at, (v) => { item.start_at = v; }),
          instantField('End (required)', item.end_at, (v) => { item.end_at = v; }),
          textField('Location', item.location ?? '', (v) => { item.location = v; }),
          textField('Time zone', item.time_zone ?? ctx.store.zone, (v) => { item.time_zone = v; }));
      }
      const warnings = item.uncertainty.length > 0 ? h('p', { class: 'import-warning small',
        text: item.uncertainty.join(' ') }) : null;
      const already = item.already_imported ? h('p', { class: 'muted small', text: 'Already imported: will not be duplicated.' }) : null;
      const provenance = h('p', { class: 'muted small', text: `${item.source_section} · Confidence ${Math.round(item.confidence * 100)}%` });
      return h('section', { class: 'import-proposal', attrs: { 'data-item': String(index) } },
        h('div', { class: 'row nowrap' }, toggle,
          h('span', { class: 'grow', text: item.preserve_original ? 'Original SLDC document · preserve formatting' : `Suggestion ${index + 1}` }), kind),
        provenance, title, description, ...dates, warnings, already);
    }));
    drawCount();
  };
  function drawCount(): void {
    const count = choices.filter((c) => c.selected).length;
    summary.textContent = `${count} selected · ${choices.length} suggestions. Nothing is added until you confirm.`;
  }
  draw();
  const selected = (): Proposal[] => choices.filter((choice) => choice.selected).map((choice) => choice.data);
  const dialog = openDialog<true>({
    title: 'Review document import', eyebrow: preview.filename.toUpperCase().slice(0, 65), size: 'wide',
    body: [h('p', { text: `SPlanner analyzed “${preview.title}”. Review the proposed items below. The original SLDC is preserved separately.` }),
      summary, list, error], cancelLabel: 'Cancel',
    actions: [{ label: 'Create selected items', kind: 'primary', run: async () => {
      error.hidden = true;
      try {
        const commit = await ctx.store.api.post<{ created: unknown[]; duplicates_skipped: number }>(
          `/document-imports/${preview.id}/commit`, { selected: selected() });
        await ctx.store.refresh();
        toast(`Imported ${commit.created.length} planning items`, { kind: 'ok' });
        return true;
      } catch (err) {
        error.textContent = errorText(err);
        error.hidden = false;
        return undefined;
      }
    } }],
  });
  return dialog.result.then(async (accepted) => {
    if (!accepted) await ctx.store.api.post(`/document-imports/${preview.id}/cancel`, {}).catch(() => undefined);
  });
}

function fileBase64(file: File): Promise<string> {
  return file.arrayBuffer().then((buf) => {
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 16_384) {
      binary += String.fromCharCode(...bytes.subarray(offset, offset + 16_384));
    }
    return btoa(binary);
  });
}

export async function importDocuments(ctx: ViewContext, files: Iterable<File>): Promise<void> {
  const docs = Array.from(files).filter((f) => f.name.toLowerCase().endsWith('.sldc'));
  if (!docs.length) { toast('Choose one or more .sldc documents', { kind: 'error' }); return; }
  for (const file of docs) {
    try {
      if (file.size > 5 * 1024 * 1024) throw new Error('This SLDC file exceeds the 5 MB import limit.');
      const base64 = await fileBase64(file);
      const preview = await ctx.store.api.post<ImportPreview>('/document-imports',
        { filename: file.name, base64, time_zone: ctx.store.zone });
      await previewDialog(ctx, preview);
    } catch (err) {
      toast(`${file.name}: ${errorText(err)}`, { kind: 'error' });
    }
  }
}

export function openDocumentImport(ctx: ViewContext): void {
  const input = h('input', { type: 'file', attrs: { accept: '.sldc', multiple: '' } });
  input.hidden = true;
  document.body.append(input);
  input.addEventListener('change', () => {
    const selected = Array.from(input.files ?? []);
    input.remove();
    void importDocuments(ctx, selected);
  }, { once: true });
  input.click();
}

export function enableSLDCDrop(ctx: ViewContext): () => void {
  const onDrag = (event: DragEvent): void => {
    if (Array.from(event.dataTransfer?.items ?? []).some((item) => item.kind === 'file')) event.preventDefault();
  };
  const onDrop = (event: DragEvent): void => {
    const files = Array.from(event.dataTransfer?.files ?? []);
    if (!files.some((f) => f.name.toLowerCase().endsWith('.sldc'))) return;
    event.preventDefault();
    void importDocuments(ctx, files);
  };
  window.addEventListener('dragover', onDrag);
  window.addEventListener('drop', onDrop);
  return () => { window.removeEventListener('dragover', onDrag); window.removeEventListener('drop', onDrop); };
}
