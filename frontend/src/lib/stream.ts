// One EventSource on /api/stream. It reconnects by itself; after any drop, `onReconnect` lets the app refetch.
// When the browser gives up (a non-200 answer such as 401), we retry with backoff and let the caller probe auth.

import { decodeStreamEvent, STREAM_EVENT_TYPES } from './events';
import type { StreamEvent } from './types';

export interface StreamHandlers {
  onEvent: (ev: StreamEvent) => void;
  onReconnect: () => void;
  onStatus: (connected: boolean) => void;
  /** the browser closed the stream for good; resolve false to stop retrying (e.g. sign-in needed) */
  onClosed: () => Promise<boolean>;
}

export function backoff(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5));
}

export function connectStream(url: string, h: StreamHandlers): () => void {
  let es: EventSource | null = null;
  let stopped = false;
  let dropped = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const open = () => {
    if (stopped) return;
    es = new EventSource(url);
    es.onopen = () => {
      attempt = 0;
      h.onStatus(true);
      if (dropped) {
        dropped = false;
        h.onReconnect();
      }
    };
    es.onerror = () => {
      h.onStatus(false);
      dropped = true;
      if (es && es.readyState === EventSource.CLOSED) {
        es = null;
        void h.onClosed().then((retry) => {
          if (!retry || stopped) return;
          timer = setTimeout(open, backoff(attempt++));
        });
      }
    };
    for (const type of STREAM_EVENT_TYPES) {
      es.addEventListener(type, (e) => {
        const ev = decodeStreamEvent(type, (e as MessageEvent<string>).data);
        if (ev) h.onEvent(ev);
      });
    }
  };

  open();
  return () => {
    stopped = true;
    clearTimeout(timer);
    es?.close();
    es = null;
  };
}
