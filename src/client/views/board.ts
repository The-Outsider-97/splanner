/** The sticky board: free-form coloured notes you can drag around and type into. Each sticky's text is a small SLDC document, like a note. */
import { announce, confirmDialog, debounce, glyph, h, replace, toast, type MenuEntry } from '../kit.js';
import type { MountedView, ViewContext } from '../context.js';
import { errorText } from '../store.js';
import { emptyState, moreButton } from '../widgets.js';
import { COLORS, COLOR_LABEL, type Color, type Sticky } from '../../core/types.js';

const WIDTH = 236;
const HEIGHT = 208;
const GAP = 20;
const STEP = 16;
/** The smallest and largest a sticky note can be resized to (pixels). The server enforces the same limits. */
const MIN_W = 150;
const MIN_H = 110;
const MAX_SIZE = 1000;
const clampSize = (value: number, low: number): number => Math.round(Math.min(MAX_SIZE, Math.max(low, value)));

interface StickyView { el: HTMLElement; text: HTMLTextAreaElement; save: ReturnType<typeof debounce>; chain: Promise<unknown>; dragging: boolean }

export function mountBoard(host: HTMLElement, ctx: ViewContext): MountedView {
  const { store, ui } = ctx;
  const views = new Map<string, StickyView>();
  const canvas = h('div', { class: 'board-canvas' });
  const scroller = h('div', { class: 'board', attrs: { role: 'region', 'aria-label': 'Sticky notes board' } }, canvas);
  const emptyEl = h('div', { class: 'board-empty' });
  let zTop = 1;

  const current = (id: string): Sticky | undefined => store.data.stickies.find((s) => s.id === id);

  /** Run store updates for one sticky one after another, each with the version the previous one produced. */
  function enqueue(id: string, work: (sticky: Sticky) => Promise<unknown>): Promise<void> {
    const view = views.get(id);
    if (!view) return Promise.resolve();
    const next = view.chain.catch(() => undefined).then(async () => { const sticky = current(id); if (sticky) await work(sticky); });
    view.chain = next;
    return next.then(() => undefined, (error: unknown) => { toast(errorText(error), { kind: 'error' }); });
  }

  const freeSpot = (): { x: number; y: number } => {
    const taken = store.data.stickies.map((s) => ({ x: s.x, y: s.y }));
    const columns = Math.max(1, Math.floor(Math.max(scroller.clientWidth, 720) / (WIDTH + GAP)));
    for (let i = 0; i < 200; i += 1) {
      const x = GAP + (i % columns) * (WIDTH + GAP);
      const y = GAP + Math.floor(i / columns) * (HEIGHT + GAP);
      if (!taken.some((t) => Math.abs(t.x - x) < WIDTH / 2 && Math.abs(t.y - y) < HEIGHT / 2)) return { x, y };
    }
    return { x: GAP, y: GAP };
  };

  async function addSticky(): Promise<void> {
    try {
      const color = COLORS[store.data.stickies.length % COLORS.length] as Color;
      const created = await store.createSticky({ ...freeSpot(), color, content: '' });
      views.get(created.id)?.text.focus();
      announce('Sticky note added');
    } catch (error) { toast(errorText(error), { kind: 'error' }); }
  }

  /** A sticky note without a saved size has the default one from the stylesheet; with one, exactly that many pixels. */
  function size(el: HTMLElement, width: number | null, height: number | null): void {
    for (const [name, value] of [['width', width], ['height', height]] as const) {
      if (value === null) el.style.removeProperty(name); else el.style.setProperty(name, `${Math.round(value)}px`);
    }
  }

  function place(view: StickyView, sticky: Sticky): void {
    view.el.style.setProperty('transform', `translate(${Math.round(sticky.x)}px, ${Math.round(sticky.y)}px)`);
    view.el.className = `sticky tone-${sticky.color}`;
    size(view.el, sticky.width, sticky.height);
  }

  function build(sticky: Sticky): StickyView {
    const id = sticky.id;
    const text = h('textarea', { class: 'sticky-text', placeholder: 'Write something…', attrs: { 'aria-label': 'Sticky note text', maxlength: 2000, spellcheck: 'true' } });
    text.value = sticky.text;
    const entries = (): MenuEntry[] => [
      ...COLORS.map((color): MenuEntry => ({ label: `Colour: ${COLOR_LABEL[color]}`, icon: 'palette', disabled: current(id)?.color === color, run: () => { void enqueue(id, (s) => store.updateSticky(id, { color, version: s.version })); } })),
      'separator',
      { label: 'Reset size', icon: 'refresh', disabled: current(id)?.width == null && current(id)?.height == null, run: () => { void enqueue(id, (s) => store.updateSticky(id, { width: null, height: null, version: s.version })); } },
      { label: 'Delete sticky note', icon: 'trash', danger: true, run: () => {
        void confirmDialog({ title: 'Delete this sticky note?', message: 'Its text will be deleted for good.', confirmLabel: 'Delete', danger: true }).then((yes) => {
          if (yes) store.deleteSticky(id).then(() => toast('Sticky note deleted')).catch((error: unknown) => toast(errorText(error), { kind: 'error' }));
        });
      } },
    ];
    const grip = h('div', { class: 'sticky-grip', attrs: { role: 'button', tabindex: '0', 'aria-label': 'Move sticky note (arrow keys also move it)', title: 'Drag to move' } }, glyph('drag'));
    const resizer = h('div', { class: 'sticky-resize', attrs: { role: 'button', tabindex: '0', 'aria-label': 'Resize sticky note (arrow keys also resize it, double-click resets the size)', title: 'Drag to resize' } });
    const el = h('div', { class: `sticky tone-${sticky.color}`, attrs: { 'data-id': id } }, h('div', { class: 'sticky-head' }, grip, h('span', { class: 'grow' }), moreButton('Sticky note actions', entries)), text, resizer);
    const save = debounce(() => { const value = text.value; void enqueue(id, (s) => (s.text === value ? Promise.resolve() : store.updateSticky(id, { content: value, version: s.version }, { silent: true }))); }, 700);
    text.addEventListener('input', () => save());
    text.addEventListener('blur', () => save.flush());
    const view: StickyView = { el, text, save, chain: Promise.resolve(), dragging: false };

    // dragging by the grip
    let origin: { px: number; py: number; x: number; y: number } | null = null;
    grip.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      const s = current(id); if (!s) return;
      origin = { px: e.clientX, py: e.clientY, x: s.x, y: s.y };
      view.dragging = true; el.classList.add('is-dragging'); el.style.setProperty('z-index', String(++zTop));
      grip.setPointerCapture(e.pointerId); e.preventDefault();
    });
    grip.addEventListener('pointermove', (e) => {
      if (!origin) return;
      const x = Math.max(0, origin.x + e.clientX - origin.px);
      const y = Math.max(0, origin.y + e.clientY - origin.py);
      el.style.setProperty('transform', `translate(${Math.round(x)}px, ${Math.round(y)}px)`);
      el.dataset['x'] = String(x); el.dataset['y'] = String(y);
    });
    const drop = (commit: boolean): void => {
      if (!origin) return;
      const x = Number(el.dataset['x'] ?? origin.x); const y = Number(el.dataset['y'] ?? origin.y);
      origin = null; view.dragging = false; el.classList.remove('is-dragging'); delete el.dataset['x']; delete el.dataset['y'];
      const s = current(id);
      if (!commit || !s) { if (s) place(view, s); return; }
      if (Math.round(x) === Math.round(s.x) && Math.round(y) === Math.round(s.y)) return;
      void enqueue(id, (latest) => store.updateSticky(id, { x: Math.round(x), y: Math.round(y), version: latest.version }, { silent: true })).then(() => { const now = current(id); if (now) place(view, now); fit(); });
    };
    grip.addEventListener('pointerup', (e) => { grip.releasePointerCapture(e.pointerId); drop(true); });
    grip.addEventListener('pointercancel', () => drop(false));
    grip.addEventListener('keydown', (e) => {
      const dx = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0;
      const dy = e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' ? 1 : 0;
      if (!dx && !dy) return;
      e.preventDefault();
      const s = current(id); if (!s) return;
      const step = e.shiftKey ? STEP * 4 : STEP;
      const x = Math.max(0, s.x + dx * step); const y = Math.max(0, s.y + dy * step);
      place(view, { ...s, x, y });
      void enqueue(id, (latest) => store.updateSticky(id, { x, y, version: latest.version }, { silent: true })).then(fit);
    });
    // resizing by the corner handle
    let sizing: { px: number; py: number; w: number; h: number } | null = null;
    const draw = (w: number, hgt: number): void => { size(el, w, hgt); el.dataset['w'] = String(w); el.dataset['h'] = String(hgt); };
    // Several quick key presses are saved one after another; until the last one is stored the note keeps the size being typed towards
    // (re-drawing from an earlier save in between would make the note jump back and the next key start from the wrong size).
    let queued = 0; let target: { w: number; h: number } | null = null;
    const commitSize = (w: number, hgt: number): void => {
      queued += 1; view.dragging = true;
      void enqueue(id, (latest) => store.updateSticky(id, { width: w, height: hgt, version: latest.version }, { silent: true })).then(() => {
        queued -= 1;
        if (queued > 0) return;
        target = null; view.dragging = false;
        const now = current(id); if (now) place(view, now);
        fit();
      });
    };
    resizer.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      const box = el.getBoundingClientRect();
      sizing = { px: e.clientX, py: e.clientY, w: box.width, h: box.height };
      view.dragging = true; el.classList.add('is-resizing'); el.style.setProperty('z-index', String(++zTop));
      resizer.setPointerCapture(e.pointerId); e.preventDefault();
    });
    resizer.addEventListener('pointermove', (e) => {
      if (!sizing) return;
      draw(clampSize(sizing.w + e.clientX - sizing.px, MIN_W), clampSize(sizing.h + e.clientY - sizing.py, MIN_H));
    });
    const endSizing = (commit: boolean): void => {
      if (!sizing) return;
      const w = Number(el.dataset['w'] ?? sizing.w); const hgt = Number(el.dataset['h'] ?? sizing.h);
      const before = sizing;
      sizing = null; view.dragging = false; el.classList.remove('is-resizing'); delete el.dataset['w']; delete el.dataset['h'];
      const s = current(id);
      if (!commit || !s || (Math.round(w) === Math.round(before.w) && Math.round(hgt) === Math.round(before.h))) { if (s) place(view, s); return; }
      commitSize(w, hgt);
    };
    resizer.addEventListener('pointerup', (e) => { resizer.releasePointerCapture(e.pointerId); endSizing(true); });
    resizer.addEventListener('pointercancel', () => endSizing(false));
    resizer.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && sizing) { endSizing(false); return; }
      const dx = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0;
      const dy = e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' ? 1 : 0;
      if (!dx && !dy) return;
      e.preventDefault();
      const box = el.getBoundingClientRect();
      const base = target ?? { w: box.width, h: box.height };
      const step = e.shiftKey ? STEP * 4 : STEP;
      const w = clampSize(base.w + dx * step, MIN_W); const hgt = clampSize(base.h + dy * step, MIN_H);
      target = { w, h: hgt };
      size(el, w, hgt);
      commitSize(w, hgt);
    });
    resizer.addEventListener('dblclick', () => { void enqueue(id, (s) => store.updateSticky(id, { width: null, height: null, version: s.version })); });
    place(view, sticky);
    return view;
  }

  /** Make the scrollable area just big enough for the notes plus room to drop more. */
  function fit(): void {
    const maxX = Math.max(0, ...store.data.stickies.map((s) => s.x + (s.width ?? WIDTH))) + 120;
    const maxY = Math.max(0, ...store.data.stickies.map((s) => s.y + (s.height ?? HEIGHT))) + 120;
    canvas.style.setProperty('min-width', `${maxX}px`);
    canvas.style.setProperty('min-height', `${Math.max(520, maxY)}px`);
  }

  function reconcile(): void {
    const stickies = store.data.stickies;
    const ids = new Set(stickies.map((s) => s.id));
    for (const [id, view] of views) if (!ids.has(id)) { view.save.cancel(); view.el.remove(); views.delete(id); }
    for (const sticky of stickies) {
      let view = views.get(sticky.id);
      if (!view) { view = build(sticky); views.set(sticky.id, view); canvas.appendChild(view.el); continue; }
      if (!view.dragging) place(view, sticky);
      if (document.activeElement !== view.text && view.text.value !== sticky.text) view.text.value = sticky.text;
    }
    replace(emptyEl, stickies.length === 0 ? emptyState('The board is empty. Add a sticky note for anything you want in sight.', { label: 'Add sticky note', run: () => { void addSticky(); } }) : null);
    fit();
  }

  replace(host, h('section', { class: 'card board-card' },
    h('div', { class: 'card-head' }, h('p', { class: 'muted', text: 'Drag a note by its handle and resize it from the bottom-right corner. Text saves as you type.' }),
      h('button', { class: 'btn small primary', type: 'button', on: { click: () => { void addSticky(); } } }, glyph('plus'), 'Add sticky note')), emptyEl, scroller));
  reconcile();
  if (ui.route.id === 'new') { ui.replaceRoute({ view: 'board' }); void addSticky(); }
  const off = store.changed.on((kind) => { if (kind === 'stickies' || kind === 'all') reconcile(); });
  return { destroy: () => { off(); for (const view of views.values()) view.save.flush(); } };
}
