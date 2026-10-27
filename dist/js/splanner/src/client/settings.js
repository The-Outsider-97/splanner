/** The Appearance & settings dialog, the "Stored on this device" data dialog and the workspace-name dialog. */
import { plural, appearanceControls, applyAppearance, copyText, debounce, glyph, h, labelled, openDialog, replace, saveBlob, toast, formatBytes, normaliseAppearance } from './kit.js';
import { errorText } from './store.js';
import { REMIND_BEFORE_CHOICES } from '../core/reminders.js';
import { formatHour } from '../core/dates.js';
export function appearanceOf(prefs) { return { theme: prefs.theme, scale: prefs.scale, motion: prefs.motion }; }
/** Cache the look so the next start paints in the right theme before the data arrives (a convenience; the server's copy is authoritative). */
export function rememberAppearance(prefs) {
    try {
        window.localStorage.setItem('splanner.appearance', JSON.stringify(appearanceOf(prefs)));
    }
    catch { /* private window or storage blocked */ }
}
export function applyCachedAppearance() {
    try {
        const raw = window.localStorage.getItem('splanner.appearance');
        if (raw)
            applyAppearance(normaliseAppearance(JSON.parse(raw)), 'soft');
    }
    catch { /* nothing cached, or unreadable: the defaults are fine */ }
}
const seg = (label, options, value, onPick) => {
    const buttons = options.map((o) => h('button', { type: 'button', text: o.label, attrs: { 'aria-pressed': String(o.value === value) },
        on: { click: () => { for (const b of buttons)
                b.setAttribute('aria-pressed', 'false'); buttons[options.indexOf(o)].setAttribute('aria-pressed', 'true'); onPick(o.value); } } }));
    return h('div', { class: 'field' }, h('span', { class: 'label', text: label }), h('div', { class: 'seg', attrs: { role: 'group', 'aria-label': label } }, buttons));
};
function selectField(label, id, options, value, onPick) {
    const el = h('select', { id }, options.map((o) => h('option', { value: o.value, text: o.label })));
    el.value = value;
    el.addEventListener('change', () => onPick(el.value));
    return labelled(label, el);
}
export function openSettingsDialog(ctx) {
    const { store, alerts } = ctx;
    const prefs = { ...store.prefs };
    const status = h('span', { class: 'muted small', attrs: { role: 'status', 'aria-live': 'polite' }, text: 'Changes save automatically' });
    let pending = {};
    const flush = debounce(() => {
        const patch = pending;
        pending = {};
        store.savePrefs(patch).then(() => { status.textContent = 'Saved'; }).catch((error) => { status.textContent = 'Not saved'; toast(errorText(error), { kind: 'error' }); });
    }, 350);
    const set = (patch) => { Object.assign(prefs, patch); pending = { ...pending, ...patch }; status.textContent = 'Saving…'; flush(); };
    const hours = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => ({ value: String(from + i), label: formatHour(from + i, prefs.time_format) }));
    const startSel = selectField('Day starts at', 'pref-day-start', hours(0, 22), String(prefs.day_start), (v) => { const n = Number(v); set({ day_start: n, ...(prefs.day_end <= n + 1 ? { day_end: n + 2 } : {}) }); sync(); });
    const endSel = selectField('Day ends at', 'pref-day-end', hours(2, 24), String(prefs.day_end), (v) => { const n = Number(v); set({ day_end: n, ...(prefs.day_start >= n - 1 ? { day_start: n - 2 } : {}) }); sync(); });
    const sync = () => { startSel.querySelector('select').value = String(prefs.day_start); endSel.querySelector('select').value = String(prefs.day_end); };
    const durations = [15, 30, 45, 60, 90, 120, 180].map((m) => ({ value: String(m), label: m < 60 ? `${m} minutes` : m === 60 ? '1 hour' : `${m / 60} hours` }));
    const reminders = REMIND_BEFORE_CHOICES.map((c) => ({ value: c.value === null ? '-1' : String(c.value), label: c.label }));
    const sounds = store.boot.sounds.map((s) => ({ value: s, label: s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) }));
    const volume = h('input', { type: 'range', min: '0', max: '1', step: '0.05', value: String(prefs.sound_volume), attrs: { 'aria-label': 'Alert volume' }, on: { input: () => set({ sound_volume: Number(volume.value) }) } });
    const soundOn = h('input', { type: 'checkbox', checked: prefs.sound, on: { change: () => set({ sound: soundOn.checked }) } });
    const soundSelect = selectField('Alert sound', 'pref-sound', sounds, prefs.sound_file, (v) => { set({ sound_file: v }); alerts.playSound(v, prefs.sound_volume); });
    const notifState = h('span', { class: 'muted small' });
    const drawNotif = () => {
        const supported = 'Notification' in window;
        notifState.textContent = !supported ? 'Not supported by this browser.' : prefs.browser_notifications && Notification.permission === 'granted' ? 'On: SPlanner will also show a browser notification.'
            : Notification.permission === 'denied' ? 'Blocked in your browser’s site settings.' : 'Off';
    };
    const notifButton = h('button', { class: 'btn small', type: 'button', text: 'Turn on browser notifications', on: { click: () => {
                if (!('Notification' in window)) {
                    drawNotif();
                    return;
                }
                void Notification.requestPermission().then((result) => { set({ browser_notifications: result === 'granted' }); drawNotif(); if (result === 'granted')
                    toast('Browser notifications are on', { kind: 'ok' }); });
            } } });
    drawNotif();
    const nameInput = h('input', { type: 'text', value: prefs.workspace_name, attrs: { maxlength: 40, autocomplete: 'off' }, on: { input: () => set({ workspace_name: nameInput.value.trim() || 'My workspace' }) } });
    const initialsInput = h('input', { type: 'text', value: prefs.initials, placeholder: 'ME', attrs: { maxlength: 3, autocomplete: 'off', 'aria-describedby': 'initials-hint' }, on: { input: () => set({ initials: initialsInput.value.replace(/\s/g, '').slice(0, 3).toUpperCase() }) } });
    const group = (title, ...children) => h('section', { class: 'settings-group' }, h('h3', { text: title }), ...children);
    openDialog({
        title: 'Appearance & settings', eyebrow: 'SPLANNER', size: 'wide', cancelLabel: 'Done', body: [
            group('Appearance', appearanceControls(appearanceOf(prefs), (next) => set({ theme: next.theme, scale: next.scale, motion: next.motion }))),
            group('Calendar', h('div', { class: 'fields' }, seg('Week starts on', [{ value: 'monday', label: 'Monday' }, { value: 'sunday', label: 'Sunday' }], prefs.week_start, (v) => set({ week_start: v })), seg('Time format', [{ value: '24h', label: '24-hour' }, { value: '12h', label: '12-hour' }], prefs.time_format, (v) => { set({ time_format: v }); }), startSel, endSel, selectField('New events last', 'pref-duration', durations, String(prefs.default_duration), (v) => set({ default_duration: Number(v) })), selectField('New events remind me', 'pref-remind', reminders, String(prefs.default_remind), (v) => set({ default_remind: Number(v) })), selectField('Open on', 'pref-view', [{ value: 'today', label: 'Today' }, { value: 'calendar', label: 'Calendar' }, { value: 'tasks', label: 'My tasks' }, { value: 'notes', label: 'Notes' }, { value: 'board', label: 'Sticky board' }], prefs.default_view, (v) => set({ default_view: v })))),
            group('Alerts', h('p', { class: 'muted small', text: 'SPlanner alerts you while it is open in your browser. It can’t ring when closed.' }), h('label', { class: 'switch' }, soundOn, h('span', { text: 'Play a sound' })), h('div', { class: 'fields' }, soundSelect, h('div', { class: 'field' }, h('span', { class: 'label', text: 'Volume' }), h('div', { class: 'row nowrap' }, h('div', { class: 'grow' }, volume), h('button', { class: 'btn small', type: 'button', on: { click: () => alerts.playSound(prefs.sound_file, prefs.sound_volume) } }, glyph('bell'), 'Preview')))), h('div', { class: 'row' }, notifButton, notifState)),
            group('Workspace', h('div', { class: 'fields' }, labelled('Workspace name', nameInput), labelled('Initials (shown in the avatar)', initialsInput, { hint: 'Up to three characters.' }))),
            h('div', { class: 'settings-status' }, status),
        ],
    }).result.then(() => { flush.flush(); });
}
// ---------------------------------------------------------------------------------------------------------------- data dialog
export function openDataDialog(ctx) {
    const { store } = ctx;
    const body = h('div', { class: 'data-dialog' }, h('p', { class: 'muted', text: 'Loading…' }));
    const draw = (info) => {
        const sldc = info.slai.sldc;
        const row = (label, value) => h('div', { class: 'kv' }, h('dt', { text: label }), h('dd', {}, value));
        replace(body, h('p', { text: 'Everything you create stays on this computer. Nothing is sent anywhere.' }), h('dl', { class: 'kv-list' }, row('Data folder', h('span', { class: 'row nowrap' }, h('code', { class: 'clip', text: info.dataDir }), h('button', { class: 'iconbtn tiny', type: 'button', attrs: { 'aria-label': 'Copy the data folder path' }, on: { click: () => { void copyText(info.dataDir).then((ok) => toast(ok ? 'Path copied' : 'Copy is not available here')); } } }, glyph('copy')))), row('Database', `${formatBytes(info.databaseBytes)} · schema ${info.schemaVersion} · integrity ${info.integrity}`), row('Contents', `${plural(info.counts.events, 'event')} · ${plural(info.counts.tasks, 'task')} (${info.counts.openTasks} open) · ${plural(info.counts.notes, 'note')} · ${plural(info.counts.stickies, 'sticky note')}`), row('Notes format', sldc.available ? `SLDC, through SLAI (${sldc.module ?? 'src.utils.sldc'})` : `Unavailable: ${sldc.reason ?? 'SLDC not found'}`), row('SLAI folder', info.slai.slaiRoot ?? 'not found'), row('Version', `SPlanner ${info.version} · Python ${info.python}`)), h('h3', { text: 'Backups' }), info.backups.length ? h('ul', { class: 'plain-list backup-list' }, info.backups.slice(0, 6).map((b) => h('li', { class: 'row nowrap' }, h('span', { class: 'clip grow', text: b.name }), h('span', { class: 'muted small', text: `${formatBytes(b.bytes)} · ${new Date(b.at).toLocaleDateString()}` })))) : h('p', { class: 'muted', text: 'No backups yet.' }), h('p', { class: 'muted small', text: 'A backup is made automatically about once a week, and before any upgrade of the data format. Back up now to save one right away.' }));
    };
    const load = () => { store.status().then(draw).catch((error) => replace(body, h('div', { class: 'notice err', text: errorText(error) }))); };
    load();
    const download = (path) => { store.api.download(path).then((file) => saveBlob(file.blob, file.filename)).catch((error) => toast(errorText(error), { kind: 'error' })); };
    openDialog({
        title: 'Stored on this device', eyebrow: 'YOUR DATA', size: 'wide', cancelLabel: 'Close', body,
        actions: [
            { label: 'Export calendar (.ics)', run: () => { download('/export/ics'); return undefined; } },
            { label: 'Export everything (.json)', run: () => { download('/export/json'); return undefined; } },
            { label: 'Back up now', kind: 'primary', run: async () => { try {
                    const made = await store.backup();
                    toast(`Backed up: ${made.name}`, { kind: 'ok' });
                    load();
                }
                catch (error) {
                    toast(errorText(error), { kind: 'error' });
                } return undefined; } },
        ],
    });
}
// ---------------------------------------------------------------------------------------------------------------- workspace card
export function openWorkspaceDialog(ctx) { openSettingsDialog(ctx); }
//# sourceMappingURL=settings.js.map