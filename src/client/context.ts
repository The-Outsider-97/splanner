import type { EditorContext } from './editors.js';
import type { Alerts } from './alerts.js';

/** What every view receives: the data store, the navigation state, and the alert service. */
export interface ViewContext extends EditorContext { alerts: Alerts }

/** A mounted view. `destroy` runs when the person leaves it, so listeners and timers never outlive the screen. */
export interface MountedView { destroy(): void }
