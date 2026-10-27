/** The application frame: sidebar (brand, workspace, navigation, mini calendar, data and appearance), topbar and page heading.
 *  It mounts the active view into #view and tears it down when the person navigates away. */
import { clear, glyph, h, plural, replace } from './kit.js';
import { showCreateMenu } from './editors.js';
import { openRemindersDialog } from './alerts.js';
import { openDataDialog, openSettingsDialog } from './settings.js';
import { openSearchDialog } from './search.js';
import { avatar } from './widgets.js';
import { mountToday } from './views/today.js';
import { mountCalendar } from './views/calendar.js';
import { mountTasks } from './views/tasks.js';
import { mountNotes } from './views/notes.js';
import { mountBoard } from './views/board.js';
import { addMonths, formatDayLong, formatMonthYear, formatWeekRange, isoDate, monthGrid, sameDay, startOfDay, weekDays, weekdayNames } from '../core/dates.js';
const VIEW_INFO = {
    today: { label: 'Today', icon: 'home', eyebrow: 'A FRESH DAY TO MAKE PROGRESS', title: 'Good to see you.', dates: true },
    calendar: { label: 'Calendar', icon: 'calendar', eyebrow: 'YOUR SCHEDULE', title: 'Calendar', dates: true },
    tasks: { label: 'My tasks', icon: 'check-square', eyebrow: 'STAY ON TRACK', title: 'My tasks', dates: false },
    notes: { label: 'Notes', icon: 'note', eyebrow: 'CAPTURE IDEAS', title: 'Notes', dates: false },
    board: { label: 'Sticky board', icon: 'layout', eyebrow: 'THINK OUT LOUD', title: 'Sticky board', dates: false },
};
const NAV = ['today', 'calendar', 'tasks', 'notes', 'board'];
export function mountShell(root, ctx) {
    const { store, ui, alerts } = ctx;
    let view = null;
    let miniAnchor = new Date(ui.focus);
    // ---------------------------------------------------------------- sidebar
    const navLinks = new Map();
    const nav = h('nav', { class: 'nav-list', attrs: { 'aria-label': 'Main' } }, NAV.map((name) => {
        const a = h('a', { class: 'nav-item', href: `#/${name}` }, glyph(VIEW_INFO[name].icon), h('span', { text: VIEW_INFO[name].label }));
        navLinks.set(name, a);
        return a;
    }));
    const workspaceName = h('strong', { class: 'clip' });
    const avatarSlot = h('span', {});
    const workspace = h('button', { class: 'workspace', type: 'button', attrs: { 'aria-label': 'Workspace settings' }, on: { click: () => openSettingsDialog(ctx) } }, avatarSlot, h('span', { class: 'workspace-text' }, workspaceName, h('small', { text: 'Personal · Local account' })), glyph('chevron-down'));
    const miniTitle = h('strong', { class: 'mini-title', attrs: { 'aria-live': 'polite' } });
    const miniGrid = h('div', { class: 'mini-grid' });
    const mini = h('section', { class: 'mini-calendar', attrs: { 'aria-label': 'Mini calendar' } }, h('div', { class: 'mini-head' }, miniTitle, h('div', { class: 'row nowrap' }, h('button', { class: 'iconbtn tiny', type: 'button', attrs: { 'aria-label': 'Previous month' }, on: { click: () => { miniAnchor = addMonths(miniAnchor, -1); drawMini(); } } }, glyph('chevron-left')), h('button', { class: 'iconbtn tiny', type: 'button', attrs: { 'aria-label': 'Next month' }, on: { click: () => { miniAnchor = addMonths(miniAnchor, 1); drawMini(); } } }, glyph('chevron-right')))), miniGrid);
    const sidebar = h('aside', { class: 'sidebar', id: 'sidebar', attrs: { 'aria-label': 'Sidebar' } }, h('div', { class: 'brand' }, h('img', { class: 'brand-mark', src: '/assets/img/logo.svg', alt: '', attrs: { width: 36, height: 36 } }), h('span', { class: 'brand-text' }, 'SPlanner', h('small', { text: 'INTELLIGENT WORKSPACE' }))), workspace, nav, mini, h('div', { class: 'side-bottom' }, h('button', { class: 'side-link', type: 'button', on: { click: () => openDataDialog(ctx) } }, glyph('laptop'), h('span', { text: 'Stored on this device' })), h('button', { class: 'side-link', type: 'button', on: { click: () => openSettingsDialog(ctx) } }, glyph('settings'), h('span', { class: 'grow', text: 'Appearance' }), glyph('chevron-right'))));
    // ---------------------------------------------------------------- topbar and heading
    const crumb = h('span', { class: 'crumb-current' });
    const badge = h('span', { class: 'bubble', hidden: true, attrs: { 'aria-hidden': 'true' } });
    const bell = h('button', { class: 'iconbtn', type: 'button', attrs: { 'aria-label': 'Reminders' }, on: { click: () => openRemindersDialog(ctx, alerts) } }, glyph('bell'), badge);
    const createButton = h('button', { class: 'btn primary', type: 'button', attrs: { 'aria-haspopup': 'menu' }, on: { click: () => showCreateMenu(ctx, createButton) } }, glyph('plus'), 'Create new');
    const menuButton = h('button', { class: 'iconbtn menu-toggle', type: 'button', attrs: { 'aria-label': 'Open the sidebar', 'aria-controls': 'sidebar', 'aria-expanded': 'false' },
        on: { click: () => { const open = !shell.classList.contains('nav-open'); shell.classList.toggle('nav-open', open); menuButton.setAttribute('aria-expanded', String(open)); } } }, glyph('menu'));
    const topbar = h('header', { class: 'topbar' }, menuButton, h('div', { class: 'breadcrumbs' }, h('span', { class: 'muted', text: 'Workspace' }), h('span', { class: 'crumb-sep', attrs: { 'aria-hidden': 'true' }, text: '/' }), crumb), h('div', { class: 'grow' }), h('span', { class: 'local-pill', title: 'Your data stays on this computer' }, h('span', { class: 'dot', attrs: { 'aria-hidden': 'true' } }), 'Local mode'), h('button', { class: 'iconbtn', type: 'button', attrs: { 'aria-label': 'Search (Ctrl+K)', title: 'Search (Ctrl+K)' }, on: { click: () => openSearchDialog(ctx) } }, glyph('search')), bell, createButton);
    const eyebrow = h('span', { class: 'eyebrow' });
    const title = h('h1', {});
    const subtitle = h('p', { class: 'subtitle' });
    const dateNav = h('div', { class: 'date-nav', attrs: { role: 'group', 'aria-label': 'Change date' } }, h('button', { class: 'iconbtn', type: 'button', attrs: { 'aria-label': 'Previous' }, on: { click: () => ui.shift(-1) } }, glyph('chevron-left')), h('button', { class: 'btn today-btn', type: 'button', text: 'Today', on: { click: () => ui.today() } }), h('button', { class: 'iconbtn', type: 'button', attrs: { 'aria-label': 'Next' }, on: { click: () => ui.shift(1) } }, glyph('chevron-right')));
    const heading = h('section', { class: 'page-heading' }, h('div', {}, eyebrow, title, subtitle), dateNav);
    const noticeHost = h('div', { class: 'notices' });
    const viewHost = h('div', { class: 'view', id: 'view' });
    const main = h('main', { class: 'content', id: 'main', attrs: { tabindex: '-1' } }, heading, noticeHost, viewHost);
    const shell = h('div', { class: 'shell' }, sidebar, h('div', { class: 'main-col' }, topbar, main));
    replace(root, h('a', { class: 'skip', href: '#main', on: { click: (e) => { e.preventDefault(); main.focus(); } } }, 'Skip to the workspace'), shell);
    // ---------------------------------------------------------------- drawing
    function drawMini() {
        const today = startOfDay(store.now());
        const weekStart = store.prefs.week_start;
        miniTitle.textContent = formatMonthYear(miniAnchor);
        const names = weekdayNames(weekStart, 'short');
        const focusWeek = weekDays(ui.focus, weekStart);
        replace(miniGrid, names.map((n) => h('span', { class: 'mini-dow', text: n })), monthGrid(miniAnchor, weekStart).map((day) => {
            const out = day.getMonth() !== miniAnchor.getMonth();
            const inWeek = focusWeek.some((d) => sameDay(d, day));
            const b = h('button', { class: `mini-day${out ? ' out' : ''}${sameDay(day, today) ? ' is-today' : ''}${inWeek && !sameDay(day, today) ? ' in-week' : ''}`, type: 'button', text: String(day.getDate()),
                attrs: { 'aria-label': formatDayLong(day), ...(sameDay(day, today) ? { 'aria-current': 'date' } : {}), 'data-date': isoDate(day) },
                on: { click: () => { ui.setFocus(day); if (ui.route.view !== 'today' && ui.route.view !== 'calendar') {
                        ui.setMode('day');
                        ui.go({ view: 'calendar' });
                    } shell.classList.remove('nav-open'); } } });
            return b;
        }));
    }
    function drawIdentity() {
        workspaceName.textContent = store.prefs.workspace_name;
        replace(avatarSlot, avatar(store.prefs.initials));
    }
    function headingText() {
        const info = VIEW_INFO[ui.route.view];
        const now = store.now();
        eyebrow.textContent = info.eyebrow;
        title.textContent = info.title;
        crumb.textContent = info.label;
        document.title = `${info.label} · SPlanner`;
        dateNav.hidden = !info.dates;
        let sub = '';
        if (ui.route.view === 'today') {
            const here = sameDay(ui.focus, now);
            sub = `${formatDayLong(ui.focus)} · ${here ? 'A little progress adds up.' : `Week of ${weekDays(ui.focus, store.prefs.week_start)[0]?.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`}`;
        }
        else if (ui.route.view === 'calendar') {
            sub = ui.mode === 'day' ? formatDayLong(ui.focus) : ui.mode === 'month' ? formatMonthYear(ui.focus) : formatWeekRange(weekDays(ui.focus, store.prefs.week_start));
        }
        else if (ui.route.view === 'tasks') {
            const open = store.counts.openTasks;
            const done = store.data.tasks.filter((t) => t.status === 'done').length;
            sub = `${open} open · ${done} completed`;
        }
        else if (ui.route.view === 'notes')
            sub = `${plural(store.data.notes.length, 'note')}, each saved as an SLDC document`;
        else
            sub = `${plural(store.data.stickies.length, 'sticky note')} · drag to arrange`;
        subtitle.textContent = sub;
        for (const [name, link] of navLinks) {
            if (name === ui.route.view)
                link.setAttribute('aria-current', 'page');
            else
                link.removeAttribute('aria-current');
        }
    }
    function mountView() {
        view?.destroy();
        clear(viewHost);
        shell.classList.remove('nav-open');
        menuButton.setAttribute('aria-expanded', 'false');
        const mounts = { today: mountToday, calendar: mountCalendar, tasks: mountTasks, notes: mountNotes, board: mountBoard };
        view = mounts[ui.route.view](viewHost, ctx);
        headingText();
    }
    function drawBadge() {
        const count = alerts.due.length;
        badge.hidden = count === 0;
        badge.textContent = count > 9 ? '9+' : String(count);
        bell.setAttribute('aria-label', count ? `Reminders, ${count} due` : 'Reminders');
    }
    function drawNotices() {
        const items = store.boot.notices;
        replace(noticeHost, items.map((n) => {
            const el = h('div', { class: `notice ${n.kind === 'warn' ? 'warn' : 'ok'}`, attrs: { role: 'status' } }, glyph(n.kind === 'warn' ? 'alert' : 'info'), h('div', { class: 'grow', text: n.text }), h('button', { class: 'iconbtn tiny', type: 'button', attrs: { 'aria-label': 'Dismiss' }, on: { click: () => el.remove() } }, glyph('close')));
            return el;
        }));
    }
    const offlineBanner = h('div', { class: 'notice err', attrs: { role: 'alert' }, hidden: true }, glyph('alert'), h('div', { class: 'grow', text: 'SPlanner can’t be reached. It may have been closed, so changes are not being saved. Start it again, then reload this page.' }));
    noticeHost.after(offlineBanner);
    store.offline.on((error) => { offlineBanner.hidden = error === null; });
    // ---------------------------------------------------------------- wiring
    drawIdentity();
    drawMini();
    drawBadge();
    drawNotices();
    mountView();
    ui.changed.on((kind) => {
        if (kind === 'route')
            mountView();
        else {
            if (kind === 'focus') {
                miniAnchor = new Date(ui.focus);
            }
            headingText();
            drawMini();
        }
    });
    store.changed.on((kind) => {
        if (kind === 'prefs' || kind === 'all') {
            drawIdentity();
            drawMini();
        }
        headingText();
    });
    alerts.changed.on(drawBadge);
    document.addEventListener('keydown', (event) => {
        const target = event.target;
        const typing = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable);
        if (((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') || (!typing && event.key === '/' && !document.querySelector('dialog[open]'))) {
            event.preventDefault();
            if (!document.querySelector('dialog[open]'))
                openSearchDialog(ctx);
        }
    });
}
//# sourceMappingURL=shell.js.map