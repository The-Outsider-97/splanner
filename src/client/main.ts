/** SPlanner start-up: apply the cached look, load the workspace from the server, then build the interface. */
import { applyAppearance, h, replace } from './kit.js';
import { Alerts } from './alerts.js';
import type { ViewContext } from './context.js';
import { applyCachedAppearance, appearanceOf, rememberAppearance } from './settings.js';
import { mountShell } from './shell.js';
import { Store, errorText } from './store.js';
import { UiState } from './ui-state.js';

async function start(): Promise<void> {
  const root = document.getElementById('app');
  if (!root) throw new Error('The page is missing #app');
  applyCachedAppearance();
  const store = new Store();
  try {
    await store.load();
  } catch (error) {
    replace(root, h('div', { class: 'boot-error card', attrs: { role: 'alert' } }, h('h1', { text: 'SPlanner can’t start' }), h('p', { text: errorText(error) }),
      h('p', { class: 'muted', text: 'Open SPlanner from the link its console window printed, or start it again with: python -m applications.splanner' })));
    return;
  }
  applyAppearance(appearanceOf(store.prefs), 'soft');
  rememberAppearance(store.prefs);
  store.changed.on((kind) => { if (kind === 'prefs' || kind === 'all') { applyAppearance(appearanceOf(store.prefs), 'soft'); rememberAppearance(store.prefs); } });
  const ui = new UiState(() => store.prefs.default_view);
  const base = { store, ui };
  const alerts = new Alerts(base);
  const ctx: ViewContext = { ...base, alerts };
  mountShell(root, ctx);
  alerts.start();
  window.addEventListener('pageshow', (event) => { if (event.persisted) void store.refresh(); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void store.refresh().catch(() => undefined); });
}

void start();
