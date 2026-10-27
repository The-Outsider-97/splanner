/** Small pieces SPlanner's views share: colour picker, priority pill, avatar, empty states and the "⋯" menu button. */
import { glyph, h, openMenu } from './kit.js';
import { COLORS, COLOR_LABEL, PRIORITY_LABEL } from '../core/types.js';
export function colorPicker(initial, onChange, label = 'Colour') {
    let current = initial;
    const buttons = COLORS.map((color) => h('button', {
        class: `swatch tone-${color}`, type: 'button', attrs: { 'aria-label': COLOR_LABEL[color], 'aria-pressed': String(color === current), title: COLOR_LABEL[color] },
        on: { click: () => { current = color; for (const [i, b] of buttons.entries())
                b.setAttribute('aria-pressed', String(COLORS[i] === color)); onChange?.(color); } },
    }));
    return { el: h('div', { class: 'swatches', attrs: { role: 'group', 'aria-label': label } }, buttons), value: () => current };
}
export function priorityPill(priority) {
    return h('span', { class: `pill pill-${priority}`, text: PRIORITY_LABEL[priority] });
}
export function avatar(initials, fallback = 'ME') {
    return h('span', { class: 'avatar', attrs: { 'aria-hidden': 'true' }, text: (initials || fallback).toUpperCase() });
}
export function emptyState(text, action) {
    return h('div', { class: 'empty-state' }, h('p', { class: 'empty', text }), action ? h('button', { class: 'btn small', type: 'button', on: { click: action.run } }, glyph('plus'), action.label) : null);
}
/** A "⋯" button that opens a menu built at click time (so it always reflects the current record). */
export function moreButton(label, entries) {
    const button = h('button', { class: 'iconbtn tiny more', type: 'button', attrs: { 'aria-label': label, 'aria-haspopup': 'menu' },
        on: { click: (event) => { event.stopPropagation(); openMenu(button, entries(), { label, align: 'end' }); } } }, glyph('more'));
    return button;
}
export function iconTile(name, tone) { return h('span', { class: `tile tone-${tone}`, attrs: { 'aria-hidden': 'true' } }, glyph(name)); }
/** Clicking a non-button element should also work from the keyboard. */
export function activatable(el, run, label) {
    el.setAttribute('role', 'button');
    el.setAttribute('tabindex', '0');
    el.setAttribute('aria-label', label);
    el.addEventListener('click', run);
    el.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        run();
    } });
}
//# sourceMappingURL=widgets.js.map