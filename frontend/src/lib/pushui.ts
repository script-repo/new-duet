// What the "Push to this device" control says, from what the browser and the server report.

export interface PushFacts {
  supported: boolean;
  iosNeedsInstall: boolean;
  permission: NotificationPermission | 'unsupported';
  /** this browser has a live subscription the server knows */
  subscribed: boolean;
  /** the server has a VAPID key (Web Push available) */
  serverEnabled: boolean;
}

export type PushState = 'on' | 'off' | 'ios-install' | 'denied' | 'unsupported' | 'server-off';

export function pushState(f: PushFacts): PushState {
  if (f.iosNeedsInstall) return 'ios-install';
  if (!f.supported) return 'unsupported';
  if (!f.serverEnabled) return 'server-off';
  if (f.permission === 'denied') return 'denied';
  return f.subscribed && f.permission === 'granted' ? 'on' : 'off';
}

/** Whether the toggle can be used at all in this state. */
export function pushToggleEnabled(s: PushState): boolean {
  return s === 'on' || s === 'off';
}

const HINTS: Record<PushState, string> = {
  on: 'Notifications reach this device even when Tandem is closed.',
  off: 'Turn on to get notified on this device when Tandem is closed.',
  'ios-install': 'On iPhone and iPad, push works only from the Home Screen app: tap Share, then “Add to Home Screen”, open Tandem from there and turn this on.',
  denied: 'Notifications are blocked for this site. Allow them in the browser’s site settings (the icon left of the address), or on iOS in Settings → Notifications → Tandem, then come back.',
  unsupported: 'This browser can’t receive push notifications. Use a current Chrome, Edge, Firefox or Safari over HTTPS.',
  'server-off': 'Web Push isn’t available on this server.',
};

export function pushHint(s: PushState): string {
  return HINTS[s];
}
